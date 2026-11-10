"""The formatted-table preflight against real flextables (#62). Needs the runtime's R."""

from __future__ import annotations

import subprocess

import pytest

from opncafyr.core.rscript import rscript_command
from opncafyr.workflows.nca.publish.rds_check import rds_findings

pytestmark = pytest.mark.integration

MAKE = """
suppressMessages(library(flextable))
dir <- Sys.getenv("MAKE_DIR")
df <- data.frame(dose = c(10, 20), cmax = c(1.5, 3.1))
saveRDS(autofit(flextable(df)), file.path(dir, "plain.rds"))
saveRDS(set_caption(autofit(flextable(df)), caption = "Summary of parameters"),
        file.path(dir, "captioned.rds"))
saveRDS(list(not = "a flextable"), file.path(dir, "list.rds"))
writeLines("not an rds file", file.path(dir, "broken.rds"))
"""


def test_the_preflight_tells_captioned_plain_wrong_and_broken_tables_apart(runtime, tmp_path):
    project = tmp_path / "report"
    folder = project / "OUTPUTS" / "tables"
    folder.mkdir(parents=True)
    script = tmp_path / "make.R"
    script.write_text(MAKE, "utf-8")
    spec = rscript_command(runtime, script, env={"MAKE_DIR": str(folder)})
    done = subprocess.run(  # noqa: S603 - the runtime's Rscript, a fixed script
        spec.argv,
        env={**spec.env, "PATH": str(runtime.executable("rscript").parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"flextable is not in the runtime library: {done.stderr[-200:]}")
    listed = [f"OUTPUTS/tables/{n}.rds" for n in ("plain", "captioned", "list", "broken")]
    found = {
        f["path"].rpartition("/")[2]: f["check"]
        for f in rds_findings(runtime, project, listed, tmp_path / "tmp")
    }
    assert found == {
        "captioned.rds": "rds-captioned",
        "list.rds": "rds-not-flextable",
        "broken.rds": "rds-unreadable",
    }
