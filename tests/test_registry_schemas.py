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
    item = {
        "run_id": "r",
        "artifact_path": "a.png",
        "artifact_sha256": SHA,
        "published_path": "elsewhere/a.png",
    }
    doc = {
        "schema_version": "1.0",
        "publication_id": "p",
        "created_at": "2026-11-03T00:00:00Z",
        "items": [item],
    }
    with pytest.raises(ValidationError):
        validate(doc, "publication-v1")


def test_atomic_write_validates_before_writing(tmp_path):
    target = tmp_path / "manifest.json"
    with pytest.raises(ValidationError):
        atomic_write_json(target, {"schema_version": "1.0"}, "run-manifest-v1")
    assert not target.exists()
    atomic_write_json(target, _manifest(), "run-manifest-v1")
    assert json.loads(target.read_text())["run_id"] == "20261103T142512Z-7c4e"
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]
