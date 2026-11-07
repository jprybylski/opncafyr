import json

import pytest
from jsonschema import ValidationError

from opncafyr.core.manifest import atomic_write_json, load_schema, validate
from opncafyr.core.registry import DescriptorError, ToolDescriptor, load_descriptors

SCHEMAS = [
    "tool-descriptor-v1",
    "workspace-v1",
    "run-manifest-v1",
    "publication-v1",
    "recipe-v1",
    "runtime-v1",
    "job-v1",
    "instance-v1",
    "settings-v1",
    "reportifyr-sidecar-v1",
    "preflight-v1",
    "publish-binding-v1",
    "publish-build-v1",
]
SHA = "0" * 64


@pytest.mark.parametrize("name", SCHEMAS)
def test_schemas_are_valid(name):
    assert load_schema(name)["$id"] == f"opncafyr/{name}"


def test_bundled_descriptors_load():
    tools = load_descriptors()
    assert tools["opnca"].web_route == "/apps/opnca/"
    assert "build_deck" in tools["deckifyr"].capabilities


def test_descriptor_cannot_smuggle_an_executable():
    with pytest.raises(DescriptorError, match="field 'executable'"):
        ToolDescriptor.from_mapping(
            {
                "schema_version": "1.0",
                "id": "evil",
                "version": 1,
                "runtime": "python",
                "capabilities": ["run"],
                "executables": ["python"],
                "health": {"kind": "version_flag", "executable": "python"},
                "executable": "/bin/sh",
            }
        )


EDITOR_BASE = {
    "schema_version": "1.0",
    "id": "notes",
    "version": 1,
    "runtime": "python",
    "capabilities": ["edit_project_files"],
    "executables": ["python"],
    "health": {"kind": "version_flag", "executable": "python"},
}


def test_the_report_opts_into_the_project_editor():
    from opncafyr.core.registry import load_project_editors

    tools = load_descriptors()
    spec = tools["quartifyr"].project_editor
    assert (spec.project_root, spec.file_profile, spec.settings_schema) == (
        "report",
        "report-source-v1",
        "report-settings-v1",
    )
    assert set(load_project_editors(tools)) == {"quartifyr"}
    assert tools["deckifyr"].project_editor is None


@pytest.mark.parametrize(
    ("editor", "field"),
    [
        ({"capability": "other", "project_root": "n", "file_profile": "p-v1"}, "capability"),
        (
            {"capability": "edit_project_files", "project_root": "outputs", "file_profile": "p-v1"},
            "project_root",
        ),
        (
            {"capability": "edit_project_files", "project_root": "../up", "file_profile": "p-v1"},
            "project_root",
        ),
        (
            {
                "capability": "edit_project_files",
                "project_root": ".opncafyr",
                "file_profile": "p-v1",
            },
            "project_root",
        ),
        (
            {"capability": "edit_project_files", "project_root": "n", "file_profile": "*.md"},
            "file_profile",
        ),
        (
            {
                "capability": "edit_project_files",
                "project_root": "n",
                "file_profile": "p-v1",
                "command": "sh",
            },
            "command",
        ),
    ],
)
def test_project_editor_values_are_ids_only(editor, field):
    with pytest.raises(DescriptorError, match=field):
        ToolDescriptor.from_mapping({**EDITOR_BASE, "project_editor": editor})


def _manifest(**overrides):
    from artifact_docs import run_manifest

    return run_manifest(**overrides)


def test_run_manifest_example_validates():
    validate(_manifest(), "run-manifest-v1")


def test_run_manifest_requires_analyte():
    doc = _manifest()
    del doc["analyte"]
    with pytest.raises(ValidationError):
        validate(doc, "run-manifest-v1")


def test_publication_must_target_outputs():
    from artifact_docs import publication

    validate(publication(), "publication-v1")
    for key, value in (
        ("published_path", "elsewhere/a.png"),
        ("published_path", "OUTPUTS/figures/sub/a.png"),
        ("sidecar_path", "OUTPUTS/figures/a.png"),
        ("artifact_sha256", "latest"),
    ):
        doc = publication()
        doc["items"][0][key] = value
        with pytest.raises(ValidationError):
            validate(doc, "publication-v1")


def test_publication_pins_runs():
    from artifact_docs import publication

    for drop in ("runs", "tools"):
        doc = publication()
        del doc[drop]
        with pytest.raises(ValidationError):
            validate(doc, "publication-v1")
    with pytest.raises(ValidationError):
        validate(publication(publication_id="latest"), "publication-v1")


def test_atomic_write_validates_before_writing(tmp_path):
    target = tmp_path / "manifest.json"
    with pytest.raises(ValidationError):
        atomic_write_json(target, {"schema_version": "1.0"}, "run-manifest-v1")
    assert not target.exists()
    atomic_write_json(target, _manifest(), "run-manifest-v1")
    assert json.loads(target.read_text())["run_id"] == "20261103T142512Z-7c4e"
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]
