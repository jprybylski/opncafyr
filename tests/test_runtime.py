import json
import sys

import pytest
from jsonschema import ValidationError

from opncafyr.core.runtime import RuntimeUnavailable, load_runtime


def _write(tmp_path, doc):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(doc))
    return path


def test_missing_record_is_an_empty_runtime(tmp_path):
    runtime = load_runtime(tmp_path / "absent.json")
    assert not runtime.has("rscript")
    with pytest.raises(RuntimeUnavailable):
        runtime.executable("rscript")


def test_loads_absolute_existing_paths(tmp_path):
    doc = {"schema_version": 1, "executables": {"python": sys.executable}}
    path = _write(tmp_path, {**doc, "r_library": str(tmp_path)})
    runtime = load_runtime(path)
    assert str(runtime.executable("python")) == sys.executable
    assert runtime.require_r_library() == tmp_path


@pytest.mark.parametrize("value", ["Rscript", "relative/bin/Rscript"])
def test_relative_executable_rejected(tmp_path, value):
    path = _write(tmp_path, {"schema_version": 1, "executables": {"rscript": value}})
    with pytest.raises(ValueError, match="absolute"):
        load_runtime(path)


def test_missing_executable_rejected(tmp_path):
    exe = str(tmp_path / "nope")
    path = _write(tmp_path, {"schema_version": 1, "executables": {"rscript": exe}})
    with pytest.raises(ValueError, match="not a file"):
        load_runtime(path)


def test_unknown_fields_rejected(tmp_path):
    path = _write(tmp_path, {"schema_version": 1, "executables": {}, "command": "sh"})
    with pytest.raises(ValidationError):
        load_runtime(path)
