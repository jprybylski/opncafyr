"""Unit tests for the opnca-r and deckifyr web-app adapters (no R or deckifyr needed)."""

from pathlib import Path

import httpx
import pytest

from opncafyr.adapters.deckifyr import DeckifyrWebApp
from opncafyr.adapters.opnca import MAX_UPLOAD_BYTES, OpncaWebApp
from opncafyr.core.adapter import ChildActivity, LaunchContext, SupportsActivity
from opncafyr.core.registry import load_descriptors, load_web_adapters
from opncafyr.core.runtime import Runtime
from opncafyr.core.supervisor import LaunchError
from tool_sets import DEV_ONLY, PRODUCT_WEB_APPS


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


@pytest.fixture
def runtime(tmp_path):
    lib = tmp_path / "lib"
    _exe(lib / "opnca" / "scripts" / "serve.R")
    return Runtime(
        executables={
            "rscript": _exe(tmp_path / "R" / "bin" / "Rscript"),
            "deckifyr_python": _exe(tmp_path / "venv" / "bin" / "python"),
            "soffice": _exe(tmp_path / "lo" / "soffice"),
        },
        r_library=lib,
    )


def _ctx(tmp_path, runtime, tool, route):
    return LaunchContext(
        tool=tool,
        port=50001,
        base_path=route,
        workdir=tmp_path / "w",
        tmpdir=tmp_path / "w" / "tmp",
        runtime=runtime,
        token="t" * 43,
    )


@pytest.fixture
def run_dir(tmp_path):
    run = tmp_path / "run"
    (run / "input").mkdir(parents=True)
    (run / "input" / "analysis.csv").write_text("USUBJID\n1\n")
    return run


def test_registry_finds_both_web_adapters():
    adapters = load_web_adapters(load_descriptors())
    assert set(adapters) == PRODUCT_WEB_APPS | DEV_ONLY


def test_opnca_upload_limit_matches_descriptor():
    assert load_descriptors()["opnca"].max_body_bytes == MAX_UPLOAD_BYTES


def test_opnca_argv(tmp_path, runtime, run_dir):
    ctx = _ctx(tmp_path, runtime, "opnca", "/apps/opnca/")
    session = {
        "data_path": str(run_dir / "input" / "analysis.csv"),
        "output_dir": str(run_dir),
        "run_id": "20261106T120000Z-ab12",
        "label": "--evil",
    }
    spec = OpncaWebApp().launch(ctx, session)
    assert spec.argv[0] == str(runtime.executable("rscript"))
    assert spec.argv[1:3] == ["--vanilla", str(runtime.r_library / "opnca/scripts/serve.R")]
    args = spec.argv[3:]
    for flag in (
        "--port=50001",
        "--base-path=/apps/opnca/",
        "--lock-dataset",
        "--no-persist-mapping",
        "--storage-namespace=20261106T120000Z-ab12",
        "--no-overwrite",
        "--require-blq-rules",
        "--require-explicit-settings",
        "--no-guess-roles",
        "--api-token-env=OPNCA_API_TOKEN",
        "--shutdown-token-env=OPNCA_SHUTDOWN_TOKEN",
        f"--max-upload-bytes={MAX_UPLOAD_BYTES}",
        "--label=--evil",
    ):
        assert flag in args
    assert not any(a.startswith("--allowed-host") for a in args)
    assert spec.env["OPNCA_API_TOKEN"] == ctx.token
    assert len(spec.env["OPNCA_SHUTDOWN_TOKEN"]) >= 16
    assert spec.env["R_LIBS"] == str(runtime.r_library)
    assert spec.auth_headers == {"X-Opnca-Api-Token": ctx.token}
    assert spec.ready_line_prefix == "OPNCA_READY "


@pytest.mark.parametrize(
    "change",
    [
        {"data_path": "input/analysis.csv"},
        {"output_dir": "relative"},
        {"run_id": "../x"},
        {"run_id": ""},
        {"mapping_path": "/does/not/exist.json"},
    ],
)
def test_opnca_rejects_bad_sessions(tmp_path, runtime, run_dir, change):
    session = {
        "data_path": str(run_dir / "input" / "analysis.csv"),
        "output_dir": str(run_dir),
        "run_id": "r1",
        **change,
    }
    with pytest.raises(ValueError):
        OpncaWebApp().launch(_ctx(tmp_path, runtime, "opnca", "/apps/opnca/"), session)


class _Child:
    def __init__(self, ctx, spec, ready_line=None):
        self.context, self.spec, self.ready_line, self.pid = ctx, spec, ready_line, 1


def test_opnca_ready_needs_the_line_for_our_base_url(tmp_path, runtime, run_dir):
    ctx = _ctx(tmp_path, runtime, "opnca", "/apps/opnca/")
    data = str(run_dir / "input/analysis.csv")
    spec = OpncaWebApp().launch(ctx, {"data_path": data, "output_dir": str(run_dir), "run_id": "r"})
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    adapter = OpncaWebApp()
    assert not adapter.is_ready(_Child(ctx, spec), ok)
    wrong = 'OPNCA_READY {"url":"http://127.0.0.1:50001/"}'
    assert not adapter.is_ready(_Child(ctx, spec, wrong), ok)
    right = 'OPNCA_READY {"url":"http://127.0.0.1:50001/apps/opnca/","pid":1}'
    assert adapter.is_ready(_Child(ctx, spec, right), ok)


def test_deckifyr_argv(tmp_path, runtime):
    project = tmp_path / "deck"
    project.mkdir()
    ctx = _ctx(tmp_path, runtime, "deckifyr", "/apps/deckifyr/")
    spec = DeckifyrWebApp().launch(ctx, {"project": str(project)})
    assert spec.argv[:5] == [
        str(runtime.executable("deckifyr_python")),
        "-I",
        "-m",
        "deckifyr",
        "serve",
    ]
    args = spec.argv[5:]
    for flag in (
        f"--project={project}",
        "--presentation=presentation.yaml",
        "--host=127.0.0.1",
        "--port=50001",
        "--launcher=workspace",
        "--root-path=/apps/deckifyr",
        "--api-token-env=DECKIFYR_API_TOKEN",
        "--trusted-binaries-only",
        f"--soffice-bin={runtime.executable('soffice')}",
    ):
        assert flag in args
    assert not any(a.startswith(("--quarto-bin", "--allowed-host")) for a in args)
    lib = str(ctx.runtime.r_library)
    # R code deckifyr runs sees the app's R library only, never the user's (#34).
    assert spec.env == {
        "DECKIFYR_API_TOKEN": ctx.token,
        "R_LIBS": lib,
        "R_LIBS_USER": lib,
        "R_LIBS_SITE": "",
    }
    assert spec.auth_headers == {"X-Deckifyr-Api-Token": ctx.token}


@pytest.mark.parametrize("presentation", ["../p.yaml", "sub/p.yaml", "/abs/p.yaml", ".."])
def test_deckifyr_presentation_must_be_a_file_name(tmp_path, runtime, presentation):
    project = tmp_path / "deck"
    project.mkdir()
    ctx = _ctx(tmp_path, runtime, "deckifyr", "/apps/deckifyr/")
    with pytest.raises(ValueError):
        DeckifyrWebApp().launch(ctx, {"project": str(project), "presentation": presentation})


def test_deckifyr_ready_checks_project_loaded(tmp_path, runtime):
    project = tmp_path / "deck"
    project.mkdir()
    ctx = _ctx(tmp_path, runtime, "deckifyr", "/apps/deckifyr/")
    spec = DeckifyrWebApp().launch(ctx, {"project": str(project)})
    child = _Child(ctx, spec)

    def client(doc):
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=doc)))

    adapter = DeckifyrWebApp()
    assert adapter.is_ready(child, client({"project_loaded": True}))
    assert not adapter.is_ready(child, client({"project_loaded": False}))
    with pytest.raises(LaunchError):
        adapter.is_ready(child, client({"project_loaded": False, "project_error": {"code": "E"}}))


def test_deckifyr_reports_unsaved_edits_and_busy_jobs(tmp_path, runtime):
    project = tmp_path / "deck"
    project.mkdir()
    ctx = _ctx(tmp_path, runtime, "deckifyr", "/apps/deckifyr/")
    child = _Child(ctx, DeckifyrWebApp().launch(ctx, {"project": str(project)}))

    def client(doc, status=200):
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json=doc))
        )

    adapter = DeckifyrWebApp()
    assert isinstance(adapter, SupportsActivity)
    assert adapter.activity(child, client({"dirty": False, "active_jobs": 0})) == ChildActivity(
        unsaved=False, busy=False
    )
    assert adapter.activity(child, client({"dirty": True, "active_jobs": 2})) == ChildActivity(
        unsaved=True, busy=True
    )
    # No dirty flag: it can't promise the edits are saved.
    assert adapter.activity(child, client({"active_jobs": 0})).unsaved is True
    with pytest.raises(httpx.HTTPStatusError):
        adapter.activity(child, client({}, status=500))
