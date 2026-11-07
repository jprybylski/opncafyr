"""#38 acceptance on a real study: clean, check and run the fixture with R, then build the
support bundle the way the home page does. No fixture subject ID or concentration may be
in it.
"""

from __future__ import annotations

import csv
import zipfile

import pytest

from nca_helpers import FIXTURES, SOURCE, make_study, nca_client, ready_recipe, start_run

pytestmark = pytest.mark.integration


def _fixture_values() -> tuple[set[str], set[str]]:
    with (FIXTURES / SOURCE).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    ids = {r["USUBJID"] for r in rows if r["USUBJID"]}
    concs = {r["CONC"] for r in rows if "." in r["CONC"] or "," in r["CONC"]}
    return ids, concs


def test_bundle_after_a_real_run(security, tmp_path, runtime, monkeypatch):
    app_root = tmp_path / "app root"
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(app_root))
    study = make_study(tmp_path)
    with nca_client(security, tmp_path, study, runtime=runtime) as api:
        rid = ready_recipe(api)
        start_run(api, rid, "PARENT")
        preview = api.get("/api/support/preview").json()
        saved = api.post("/api/support/bundle", json={"preview_id": preview["preview_id"]})
        assert saved.status_code == 200, saved.text
    with zipfile.ZipFile(app_root / "logs" / "support" / saved.json()["name"]) as zf:
        text = "\n".join(zf.read(n).decode("utf-8") for n in zf.namelist())
    ids, concs = _fixture_values()
    assert [i for i in ids if i in text] == []
    assert [c for c in concs if c in text] == []
    assert str(study) not in text
    assert '"workspace": "<study:Study>"' in text
