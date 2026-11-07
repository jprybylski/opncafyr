"""The one way the launcher runs R (core/rscript.py, #22)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opncafyr.core.rscript import rscript_command
from opncafyr.core.runtime import Runtime, RuntimeUnavailable


@pytest.fixture
def runtime(tmp_path):
    bin_dir = tmp_path / "R" / "bin"
    bin_dir.mkdir(parents=True)
    rscript = bin_dir / "Rscript"
    rscript.write_text("")
    lib = tmp_path / "lib"
    lib.mkdir()
    return Runtime(executables={"rscript": rscript}, r_library=lib)


def test_vanilla_pinned_library_and_path(runtime):
    script = Path("/x/clean.R")
    spec = rscript_command(runtime, script, args=["a"], env={"CLEAN_INPUT": "f"})
    # str(), not a literal: Windows spells the same path "\x\clean.R".
    assert spec.argv == [str(runtime.executables["rscript"]), "--vanilla", str(script), "a"]
    assert spec.env["R_LIBS"] == spec.env["R_LIBS_USER"] == str(runtime.r_library)
    assert spec.env["CLEAN_INPUT"] == "f"
    assert spec.path_dirs == [runtime.executables["rscript"].parent]


def test_launcher_variables_are_refused(runtime):
    with pytest.raises(ValueError, match="OPNCAFYR_"):
        rscript_command(runtime, Path("/x.R"), env={"opncafyr_secret": "x"})


def test_no_r_is_reported():
    with pytest.raises(RuntimeUnavailable):
        rscript_command(Runtime(), Path("/x.R"))
