"""The publication curator: completed runs to OUTPUTS/ with a publication manifest (#31)."""

from __future__ import annotations

import json
import os

import pytest

from opncafyr.core import runs as R
from opncafyr.core.manifest import validate
from opncafyr.core.workspace import init_workspace
from opncafyr.workflows.nca.publish import curator as C
from opncafyr.workflows.nca.publish import sidecars as S
from opncafyr.workflows.nca.publish.api import bundled_footnotes
from opncafyr.workflows.nca.publish.catalog import catalog, mean_note
from publish_helpers import BASIC, PRESELECTED, completed_run, picks, writable


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    root.mkdir()
    init_workspace(root, "0.0.1")
    yield root
    writable(root)


@pytest.fixture
def api(client, study):
    from opncafyr.core.workspace import open_workspace

    client.app.state.workspace = open_workspace(study)
    client.study = study
    return client


def post(api, items, **kw):
    return api.post(
        "/api/nca/publications", json={"items": items, **kw}, headers=api.session_headers
    )


def test_catalog_preselects_what_adr_0007_names(study):
    _, folder = completed_run(study)
    entries = {e.artifact: e for e in catalog(R.read_manifest(folder))}
    assert {a for a, e in entries.items() if e.preselected} == set(PRESELECTED)
    assert entries["listing"].contains_subject_ids and not entries["summary"].contains_subject_ids
    assert entries["boxplot-cmax"].preselected is False
    assert "parameters-long" not in " ".join(entries)  # the long table is not offered


def test_the_mean_figures_and_table_state_how_they_were_made(study):
    _, folder = completed_run(study)
    entries = {e.artifact: e for e in catalog(R.read_manifest(folder))}
    for name in ("mean-linear", "mean-log"):
        assert entries[name].kind == "figure" and entries[name].preselected
        assert not entries[name].contains_subject_ids
    table = entries["mean-profiles"]
    assert table.kind == "table" and not table.preselected and not table.contains_subject_ids
    assert (
        table.note
        == entries["mean-log"].note
        == (
            "Arithmetic mean ± SD at each nominal time (NFRLT), of the observed concentrations "
            "after the run's BLQ rules and exclusions."
        )
    )
    assert entries["profiles-log"].note is None


def test_mean_note_wording():
    def note(**mean):
        return mean_note({"nca_settings": {"mean_profiles": mean}})

    geo = note(statistic="geometric", error_measure="se", time_basis="actual", grouping="ARM")
    assert geo.startswith("Geometric mean with geometric SE at each actual time, by ARM,")
    assert note(statistic="arithmetic", error_measure="none", time_basis="nominal").startswith(
        "Arithmetic mean at each nominal time,"
    )
    assert mean_note({"nca_settings": {}}) is None


def test_a_run_without_the_mean_figures_says_so(api):
    run_id, _ = completed_run(api.study, mean=False)
    row = api.get("/api/nca/publish/runs").json()["runs"][0]
    assert row["publishable"] and not {"mean-linear", "mean-log"} & {
        a["artifact"] for a in row["artifacts"]
    }
    assert [m["artifact"] for m in row["missing"]] == ["mean-conc-time"]
    assert "saved without the mean figures" in row["missing"][0]["reason"]
    assert post(api, picks(run_id, ["mean-linear"])).status_code == 404


def test_the_mean_sidecars_carry_the_choices(api):
    run_id, _ = completed_run(api.study)
    r = post(api, picks(run_id, ["mean-linear", "mean-profiles"]))
    assert r.status_code == 201, r.text
    for item in r.json()["publication"]["items"]:
        side = json.loads((api.study / item["sidecar_path"]).read_text("utf-8"))
        notes = side["object_meta"]["footnotes"]["notes"]
        assert notes[1].startswith("Arithmetic mean ± SD at each nominal time (NFRLT)")
        assert "SD" in side["object_meta"]["footnotes"]["abbreviations"]
    _, findings = S.check_outputs(api.study, bundled_footnotes())
    assert not [f for f in findings if f.severity == "error"]


def test_runs_listing_says_why_a_run_cannot_be_published(api):
    done, _ = completed_run(api.study, "Drug A")
    review, _ = completed_run(api.study, "Drug B", state="reviewing")
    out = api.get("/api/nca/publish/runs").json()
    rows = {r["run_id"]: r for r in out["runs"]}
    assert rows[done]["publishable"] and rows[done]["replay"] == "passed"
    assert {a["artifact"] for a in rows[done]["artifacts"] if a["preselected"]} == set(PRESELECTED)
    assert not rows[review]["publishable"] and "completed" in rows[review]["reason"]
    assert rows[review]["artifacts"] == [] and rows[review]["missing"] == []
    assert rows[done]["missing"] == []


def test_publish_copies_byte_identical_files_with_valid_sidecars(api):
    run_id, folder = completed_run(api.study, "Drug A", label="Drug A (plasma)")
    r = post(api, picks(run_id), label="First report")
    assert r.status_code == 201, r.text
    doc = r.json()["publication"]
    validate(doc, "publication-v1")
    assert [x["run_id"] for x in doc["runs"]] == [run_id]
    assert doc["runs"][0]["manifest_sha256"] == R.sha256_file(folder / "manifest.json")
    assert {i["artifact"] for i in doc["items"]} == set(BASIC)
    for item in doc["items"]:
        published = api.study / item["published_path"]
        source = folder / item["artifact_path"]
        assert published.read_bytes() == source.read_bytes()
        assert item["published_sha256"] == item["artifact_sha256"] == R.sha256_file(source)
        assert R.sha256_file(api.study / item["sidecar_path"]) == item["sidecar_sha256"]
        assert published.name.startswith(f"drug-a-plasma_{run_id}_")
    # The sidecars pass the #30 checks, and the page sees nothing unrecorded.
    files, findings = S.check_outputs(api.study, bundled_footnotes())
    assert len(files) == 4 and findings == []
    assert api.get("/api/nca/outputs/check").json()["report"]["findings"] == []
    side = json.loads((api.study / doc["items"][0]["sidecar_path"]).read_text("utf-8"))
    assert side["object_meta"]["meta_type"] == "NA"
    assert side["source_meta"]["path"] == f".opncafyr/runs/{run_id}/analysis.R"
    # Listed, and readable with nothing changed.
    pubs = api.get("/api/nca/publications").json()["publications"]
    assert pubs[0]["publication_id"] == doc["publication_id"] and pubs[0]["items"] == 4
    got = api.get(f"/api/nca/publications/{doc['publication_id']}").json()
    assert got["changed"] == [] and got["publication"] == doc


def test_dry_run_writes_nothing(api):
    run_id, _ = completed_run(api.study)
    r = post(api, picks(run_id), dry_run=True)
    assert r.status_code == 200
    plan = r.json()["plan"]
    assert plan["blocking"] is False and plan["report"]["findings"] == []
    assert {i["status"] for i in plan["items"]} == {"new"}
    assert not (api.study / "OUTPUTS").exists()
    assert not (api.study / ".opncafyr" / "publications").exists()


@pytest.mark.parametrize("kind", ["reviewing", "replay_failed"])
def test_non_completed_or_replay_failed_runs_are_refused(api, kind):
    if kind == "reviewing":
        run_id, _ = completed_run(api.study, state="reviewing")
    else:
        run_id, _ = completed_run(api.study, replay="failed")
    for dry in (True, False):
        r = post(api, picks(run_id), dry_run=dry)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_publishable"
    assert not (api.study / "OUTPUTS").exists()


def test_a_tampered_run_is_refused(api):
    run_id, folder = completed_run(api.study)
    target = folder / "results" / "opnca-summary.csv"
    target.chmod(0o644)
    target.write_text("changed\n", "utf-8")
    r = post(api, picks(run_id))
    assert r.status_code == 409 and "no longer match" in r.json()["detail"]["message"]


def test_unknown_and_duplicate_picks(api):
    run_id, _ = completed_run(api.study)
    assert post(api, picks(run_id, ["no-such-artifact"])).status_code == 404
    assert post(api, picks(run_id, ["summary", "summary"])).status_code == 422
    assert post(api, []).status_code == 422
    bad = [{"run_id": "../x", "artifact": "summary"}]
    assert post(api, bad).status_code == 422


def test_identical_republish_reuses_files_and_different_content_is_refused(api):
    run_id, _ = completed_run(api.study)
    first = post(api, picks(run_id)).json()["publication"]
    again = post(api, picks(run_id, ["summary"]))
    assert again.status_code == 201
    item = again.json()["publication"]["items"][0]
    assert item["sidecar_sha256"] == next(
        i["sidecar_sha256"] for i in first["items"] if i["artifact"] == "summary"
    )
    # Someone edits a published file: the name is taken by different bytes now.
    published = api.study / item["published_path"]
    published.write_text("edited\n", "utf-8")
    plan = post(api, picks(run_id, ["summary"]), dry_run=True).json()["plan"]
    assert plan["conflicts"] == 1 and plan["blocking"]
    r = post(api, picks(run_id, ["summary", "listing"]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_conflict"
    assert r.json()["detail"]["paths"] == [item["published_path"]]
    assert published.read_text("utf-8") == "edited\n"  # never replaced
    changed = api.get(f"/api/nca/publications/{first['publication_id']}").json()["changed"]
    assert changed == [item["published_path"]]


def test_two_analytes_in_one_publication(api):
    a, _ = completed_run(api.study, "Drug A")
    b, _ = completed_run(api.study, "Metabolite B")
    r = post(api, picks(a, ["summary"]) + picks(b, ["summary", "profiles-log"]))
    assert r.status_code == 201
    doc = r.json()["publication"]
    assert sorted(x["run_id"] for x in doc["runs"]) == sorted([a, b])
    names = sorted(i["published_path"] for i in doc["items"])
    assert len(set(names)) == 3
    assert any("metabolite-b" in n for n in names) and any("drug-a" in n for n in names)


def test_an_interrupted_publish_records_nothing(api, monkeypatch):
    run_id, _ = completed_run(api.study)
    calls = []
    real = C._write_bytes

    def boom(target, data):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("disk full")
        real(target, data)

    monkeypatch.setattr(C, "_write_bytes", boom)
    with pytest.raises(OSError):
        C.publish(api.study, [C.Pick(run_id, a) for a in PRESELECTED], bundled_footnotes())
    assert not (api.study / ".opncafyr" / "publications").exists()
    findings = api.get("/api/nca/outputs/check").json()["report"]["findings"]
    assert {f["check"] for f in findings} >= {"not-recorded"}
    # No hidden temp file is left looking like a published file.
    leftovers = [p for p in (api.study / "OUTPUTS").rglob("*") if p.name.startswith(".")]
    assert leftovers == []


def test_sidecar_footnote_abbreviations_are_defined(study):
    run_id, folder = completed_run(study)
    entry = next(e for e in catalog(R.read_manifest(folder)) if e.artifact == "summary")
    fn = bundled_footnotes()
    doc = C.build_sidecar(
        R.read_manifest(folder), entry, "OUTPUTS/tables/x.csv", "0" * 64, fn, "me"
    )
    validate(doc, "reportifyr-sidecar-v1")
    assert set(doc["object_meta"]["footnotes"]["abbreviations"]) <= fn.abbreviations
    assert "PK" in doc["object_meta"]["footnotes"]["abbreviations"]


def test_published_names_are_deterministic(study):
    run_id, folder = completed_run(study, "  Drug/A ++ ")
    manifest = R.read_manifest(folder)
    entry = next(e for e in catalog(manifest) if e.artifact == "profiles-linear")
    assert C.published_path(manifest, entry) == (
        f"OUTPUTS/figures/drug-a_{run_id}_profiles-linear.png"
    )
    assert os.path.basename(C.sidecar_path("OUTPUTS/figures/x.png")) == "x_png_metadata.json"
