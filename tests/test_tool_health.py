"""Tool registry, descriptor validation and startup health probes (#13)."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from opncafyr import cli
from opncafyr.core import registry
from opncafyr.core.app import create_app
from opncafyr.core.health import HealthState, ToolHealth, check_tool, probe_command
from opncafyr.core.instance import read_record
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.registry import DescriptorError, ToolDescriptor, load_descriptors
from opncafyr.core.runtime import Runtime, RuntimeUnavailable
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces
from tool_sets import ALL_TOOLS

PY = Path(sys.executable)

BASE = {
    "schema_version": "1.0",
    "id": "toy",
    "version": 1,
    "runtime": "python",
    "capabilities": ["make_toy", "preview_toy"],
    "executables": ["py"],
    "health": {"kind": "python_import", "executable": "py", "target": "json"},
    "optional_requirements": [
        {
            "name": "libreoffice",
            "executable": "soffice",
            "capabilities": ["preview_toy"],
            "message": "Previews unavailable: LibreOffice is not installed.",
        }
    ],
}


def _desc(**changes) -> dict:
    doc = copy.deepcopy(BASE)
    for key, value in changes.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    return doc


# --- descriptor validation ------------------------------------------------------------


def test_valid_descriptor_loads():
    tool = ToolDescriptor.from_mapping(_desc())
    assert tool.health.kind == "python_import"
    assert tool.optional_requirements[0].capabilities == ("preview_toy",)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"schema_version": None}, "schema_version"),
        ({"schema_version": "2.0"}, "schema_version"),
        ({"executable": "/bin/sh"}, "executable"),
        ({"runtime": "shell"}, "runtime"),
        ({"health": {"kind": "shell", "executable": "py", "target": "x"}}, "health.kind"),
        (
            {"health": {"kind": "python_import", "executable": "py", "target": "os; rm"}},
            "health.target",
        ),
        (
            {"health": {"kind": "r_package", "executable": "py", "target": "x); system('id'"}},
            "health.target",
        ),
        ({"health": {"kind": "python_import", "executable": "py"}}, "health.target"),
        (
            {"health": {"kind": "python_import", "executable": "rs", "target": "json"}},
            "health.executable",
        ),
        ({"executables": ["/usr/bin/python"]}, "executables[0]"),
        (
            {"jobs": {"make_toy": {"input_schema": "nope-v1", "output_schema": "job-v1"}}},
            "jobs.make_toy.input_schema",
        ),
        (
            {"jobs": {"other": {"input_schema": "job-v1", "output_schema": "job-v1"}}},
            "jobs.other",
        ),
        (
            {
                "optional_requirements": [
                    {"name": "lo", "executable": "soffice", "capabilities": ["x"], "message": "m"}
                ]
            },
            "optional_requirements[0].capabilities[0]",
        ),
    ],
)
def test_invalid_descriptor_names_the_field(changes, field):
    with pytest.raises(DescriptorError) as info:
        ToolDescriptor.from_mapping(_desc(**changes), "adapters/toy/descriptor.yaml")
    assert info.value.field == field
    assert f"field '{field}'" in str(info.value)
    assert "adapters/toy/descriptor.yaml" in str(info.value)


def _adapter_root(tmp_path: Path, docs: dict[str, dict]) -> Path:
    root = tmp_path / "adapters"
    for folder, doc in docs.items():
        (root / folder).mkdir(parents=True)
        (root / folder / "descriptor.yaml").write_text(yaml.safe_dump(doc), "utf-8")
    return root


def test_descriptor_id_must_match_its_folder(tmp_path):
    root = _adapter_root(tmp_path, {"other": _desc()})
    with pytest.raises(DescriptorError, match="field 'id'"):
        load_descriptors(root)


# --- job contracts and their schemas (#55) ------------------------------------------

SUMMARY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["rows"],
    "properties": {"rows": {"type": "integer"}},
}


def _with_job(tmp_path, job: dict, bundled: dict[str, object] | None = None) -> Path:
    root = _adapter_root(tmp_path, {"toy": _desc(jobs={"make_toy": job})})
    for name, doc in (bundled or {}).items():
        folder = root / "toy" / "schemas"
        folder.mkdir(exist_ok=True)
        text = doc if isinstance(doc, str) else json.dumps(doc)
        (folder / f"{name}.schema.json").write_text(text, "utf-8")
    return root


def test_job_schema_can_be_bundled_with_the_adapter(tmp_path):
    root = _with_job(tmp_path, {"output_schema": "toy-summary-v1"}, {"toy-summary-v1": SUMMARY})
    contract = load_descriptors(root)["toy"].jobs["make_toy"]
    assert contract.output_schema == "toy-summary-v1"
    assert contract.input_schema is None
    assert contract.check_output({"rows": 2}, "summary.json") == []
    assert contract.check_output({"rows": "x-secret"}, "summary.json") == [
        "summary.json at /rows fails 'type'"
    ]


def test_job_schema_falls_back_to_the_launcher_schemas(tmp_path):
    root = _with_job(tmp_path, {"input_schema": "publication-v1", "output_schema": "job-v1"})
    contract = load_descriptors(root)["toy"].jobs["make_toy"]
    assert contract.input_schema == "publication-v1"
    assert contract.output_document == registry.load_schema("job-v1")


def test_job_schema_in_both_places_is_refused(tmp_path):
    root = _with_job(tmp_path, {"output_schema": "job-v1"}, {"job-v1": SUMMARY})
    with pytest.raises(DescriptorError, match=r"field 'jobs\.make_toy\.output_schema'.*both"):
        load_descriptors(root)


def test_missing_job_schema_names_both_places(tmp_path):
    root = _with_job(tmp_path, {"output_schema": "nope-v1"})
    with pytest.raises(DescriptorError, match="schemas/ folder or the launcher's schemas"):
        load_descriptors(root)


def test_invalid_bundled_job_schema_is_refused(tmp_path):
    root = _with_job(tmp_path, {"output_schema": "toy-v1"}, {"toy-v1": "{not json"})
    with pytest.raises(DescriptorError, match="not a valid JSON Schema"):
        load_descriptors(root)
    root = _with_job(tmp_path / "b", {"output_schema": "toy-v1"}, {"toy-v1": {"type": 3}})
    with pytest.raises(DescriptorError, match="not a valid JSON Schema"):
        load_descriptors(root)


def test_bundled_job_schema_is_not_found_without_the_adapter_folder():
    """``from_mapping`` without a bundle only knows the launcher's schemas."""
    with pytest.raises(DescriptorError, match="field 'jobs.make_toy.output_schema'"):
        ToolDescriptor.from_mapping(_desc(jobs={"make_toy": {"output_schema": "toy-v1"}}))


def test_invalid_descriptor_stops_the_launch_before_binding(tmp_path, monkeypatch, capsys):
    bad = _desc(health={"kind": "shell", "executable": "py", "target": "x"})
    root = _adapter_root(tmp_path, {"toy": bad})
    real = registry.load_descriptors
    monkeypatch.setattr(registry, "load_descriptors", lambda root_=None: real(root))
    monkeypatch.setattr(cli, "_bind_loopback", pytest.fail)

    state = tmp_path / "state"
    assert cli.run_controller(state=state, open_browser=False, claim_wait=1) == 1
    err = capsys.readouterr().err
    assert "adapters/toy/descriptor.yaml" in err
    assert "field 'health.kind'" in err
    assert read_record(state) is None


def test_bundled_descriptors_reference_real_schemas():
    tools = load_descriptors()
    assert set(tools) == ALL_TOOLS
    for tool in tools.values():
        assert tool.health is not None
        assert tool.health.executable in tool.executables


# --- workspace files can't register tools ---------------------------------------------


def test_descriptors_in_a_workspace_are_ignored(tmp_path, security, monkeypatch):
    study = tmp_path / "Study"
    evil = _desc(id="evil", executables=["sh"], health={"kind": "version_flag", "executable": "sh"})
    for rel in (
        "descriptor.yaml",
        "adapters/evil/descriptor.yaml",
        ".opncafyr/adapters/evil/descriptor.yaml",
    ):
        target = study / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(evil), "utf-8")
    monkeypatch.chdir(study)

    jobs = JobManager(poll_interval=0.05)
    recent = RecentWorkspaces(tmp_path / "state" / "recent.json")
    app = create_app(
        security, jobs=jobs, picker=lambda prompt, start: study, recent=recent, runtime=Runtime()
    )
    try:
        with TestClient(
            app,
            base_url=f"http://127.0.0.1:{security.port}",
            cookies={security.cookie_name: security.secret},
        ) as api:
            api.headers[SESSION_HEADER] = security.secret
            api.post("/api/workspace/pick")
            api.post("/api/workspace/init", json={"path": str(study)})
            assert api.post("/api/workspace/open", json={"path": str(study)}).status_code == 200
            ids = {t["id"] for t in api.get("/api/tools").json()["tools"]}
    finally:
        jobs.shutdown()
    assert ids == ALL_TOOLS
    assert set(load_descriptors()) == ids


# --- probes ---------------------------------------------------------------------------


def _tools(**docs: dict) -> dict[str, ToolDescriptor]:
    return {tid: ToolDescriptor.from_mapping({**doc, "id": tid}) for tid, doc in docs.items()}


def _wait(health: ToolHealth) -> None:
    assert health.wait(30)


def test_probe_commands_are_built_by_core(tmp_path):
    runtime = Runtime(executables={"py": PY, "rscript": PY}, r_library=tmp_path)
    (tool,) = _tools(t=_desc()).values()
    assert probe_command(tool, runtime) == ([str(PY), "-I", "-c", "import json"], {})

    r_doc = _desc(
        runtime="r",
        executables=["rscript"],
        health={"kind": "r_package", "executable": "rscript", "target": "opnca"},
    )
    (r_tool,) = _tools(r=r_doc).values()
    argv, env = probe_command(r_tool, runtime)
    assert argv == [str(PY), "--vanilla", "-e", "library(opnca)"]
    # The app library only: never the user's own R library or a site library (#34).
    assert env == {"R_LIBS": str(tmp_path), "R_LIBS_USER": str(tmp_path), "R_LIBS_SITE": ""}
    with pytest.raises(RuntimeUnavailable):
        probe_command(r_tool, Runtime(executables={"rscript": PY}))


def test_probe_results(tmp_path):
    tools = _tools(
        good=_desc(),
        broken=_desc(health={"kind": "python_import", "executable": "py", "target": "no_such_mod"}),
        absent=_desc(executables=["py", "rscript"]),
    )
    health = ToolHealth(tools, Runtime(executables={"py": PY}))
    health.start()
    _wait(health)
    good, broken, absent = (health.status(t) for t in ("good", "broken", "absent"))
    assert good.state == HealthState.OK
    assert broken.state == HealthState.UNAVAILABLE
    assert "exit code 1" in broken.detail
    assert absent.state == HealthState.UNAVAILABLE
    assert absent.detail == "The installed runtime has no 'rscript'."
    assert [r.name for r in good.missing] == ["libreoffice"]


def test_an_installed_optional_requirement_is_not_reported():
    tools = _tools(good=_desc())
    health = ToolHealth(tools, Runtime(executables={"py": PY, "soffice": PY}))
    health.check_all()
    assert health.status("good").missing == ()


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX shell script as the executable")
def test_a_hanging_probe_times_out_and_is_killed(tmp_path):
    exe = tmp_path / "slow"
    exe.write_text("#!/bin/sh\nsleep 30\n")
    exe.chmod(0o755)
    (tool,) = _tools(
        slow=_desc(executables=["slow"], health={"kind": "version_flag", "executable": "slow"})
    ).values()
    start = time.monotonic()
    status = check_tool(tool, Runtime(executables={"slow": exe}), workdir=tmp_path, timeout=0.5)
    assert time.monotonic() - start < 15
    assert status.state == HealthState.UNAVAILABLE
    assert "did not finish" in status.detail


# --- /api/tools -----------------------------------------------------------------------


def test_api_tools_reports_health_and_missing_requirements(security):
    tools = _tools(
        good=_desc(),
        absent=_desc(executables=["py", "rscript"]),
    )
    app = create_app(security, tools=tools, runtime=Runtime(executables={"py": PY}))
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as api:
        _wait(app.state.health)
        body = {t["id"]: t for t in api.get("/api/tools").json()["tools"]}
    assert body["good"]["health"] == {"state": "ok", "detail": "Health check passed."}
    assert body["good"]["version"] == 1
    assert body["good"]["missing_requirements"] == [
        {
            "name": "libreoffice",
            "message": "Previews unavailable: LibreOffice is not installed.",
            "capabilities": ["preview_toy"],
        }
    ]
    assert body["absent"]["health"]["state"] == "unavailable"


def test_api_tools_reports_every_bundled_stub(security):
    app = create_app(security, runtime=Runtime())
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as api:
        _wait(app.state.health)
        body = {t["id"]: t for t in api.get("/api/tools").json()["tools"]}
    assert set(body) == ALL_TOOLS
    for tool in body.values():
        assert tool["health"]["state"] == "unavailable"
        assert tool["health"]["detail"].startswith("The installed runtime has no ")
    assert "preview_deck" in body["deckifyr"]["missing_requirements"][0]["capabilities"]
