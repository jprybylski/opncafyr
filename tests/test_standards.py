"""Standards bundles (#70, ADR 0011): verify, preview, install, references, selection."""

from __future__ import annotations

import io
import json
import os
import tarfile
import zipfile

import pytest

from opncafyr.core import standards as S
from opncafyr.core.manifest import load_schema
from opncafyr.core.workspace import init_workspace, open_workspace
from publish_helpers import writable
from standards_helpers import (
    PROFILE,
    TUTORIAL,
    deck_files,
    mixed_bundle,
    report_files,
    sha,
    write_bundle,
)


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    root.mkdir()
    init_workspace(root, "0.0.1")
    yield root
    writable(root)


@pytest.fixture
def api(client, study):
    client.app.state.workspace = open_workspace(study)
    client.study = study
    client.headers.update(client.session_headers)
    return client


def inspect(api, path, **body):
    return api.post(
        "/api/standards/inspect", json={"source": {"kind": "path", "path": str(path)}, **body}
    )


def state(study):
    return json.loads((study / S.STATE_FILE).read_text("utf-8"))


def test_schemas_load():
    assert load_schema("standards-bundle-v1")["$id"] == "opncafyr/standards-bundle-v1"
    assert load_schema("standards-state-v1")["$id"] == "opncafyr/standards-state-v1"


def test_version_ranges():
    assert S.in_range("0.4.1", ">=0.4, <1")
    assert not S.in_range("1.0", ">=0.4, <1")
    assert S.in_range("0.0.1", "!=0.0.2")
    assert S.in_range("2", "==2.0.0")


def test_types_are_listed(api):
    body = api.get("/api/standards").json()
    ids = {t["id"]: t for t in body["types"]}
    assert ids["tutorial"]["install_to"] == "tutorials"
    assert ids["profile"]["install_to"] == "profiles"
    assert ids["report-template"]["native"] == "quartifyr"
    assert ids["deck-template"]["selectable"] is True
    assert body["snapshots"] == [] and body["selected"] == {}


def test_mixed_bundle_previews_then_installs(api, study, tmp_path):
    source = mixed_bundle(tmp_path / "src")
    preview = inspect(api, source).json()
    assert preview["problems"] == [], preview["problems"]
    assert preview["preview_id"]
    assert preview["bundle"] == {
        "id": "house",
        "version": "1.0.0",
        "title": "House standards",
        "description": None,
    }
    assert preview["snapshot"].startswith("house/1.0.0-")
    comps = {c["id"]: c for c in preview["components"]}
    assert comps["dose-escalation"]["plan"] == [
        {
            "source": "profiles/dose-escalation.yaml",
            "dest": "profiles/dose-escalation.yaml",
            "sha256": sha(PROFILE),
            "status": "new",
        }
    ]
    assert comps["house-report"]["selectable"] and comps["house-report"]["plan"] == []
    # Nothing is written before install.
    assert not (study / "profiles").exists()
    assert not (study / S.STATE_FILE).exists()

    done = api.post(
        "/api/standards/install",
        json={"preview_id": preview["preview_id"], "select": ["house-report", "house-deck"]},
    ).json()
    assert done["created"] is True
    assert (study / "profiles/dose-escalation.yaml").read_bytes() == PROFILE
    assert (study / "tutorials/first-analysis.yaml").read_bytes() == TUTORIAL
    doc = state(study)
    snap = doc["snapshots"][done["snapshot"]]
    assert snap["source"] == {"kind": "dir", "display": "src"}
    assert set(snap["components"]) == {
        "dose-escalation",
        "first-analysis",
        "house-report",
        "house-deck",
    }
    assert doc["selected"]["report-template"]["component"] == "house-report"
    folder = study / S.STORE / done["snapshot"]
    assert (folder / "deck/logo.png").is_file()
    assert not os.access(folder / "deck/logo.png", os.W_OK) or os.name == "nt"
    found = S.material(study, "deck-template")
    assert set(found.files) == {
        "presentation.yaml",
        "house-design.yaml",
        "house-layouts.yaml",
        "logo.png",
    }
    assert (
        found.provenance["bundle_id"] == "house" and found.provenance["component"] == "house-deck"
    )
    # No absolute paths in the record.
    assert str(tmp_path) not in (study / S.STATE_FILE).read_text("utf-8")


def test_a_preview_can_only_be_used_once(api, tmp_path):
    preview = inspect(api, mixed_bundle(tmp_path / "src")).json()
    first = api.post("/api/standards/install", json={"preview_id": preview["preview_id"]})
    assert first.status_code == 200
    again = api.post("/api/standards/install", json={"preview_id": preview["preview_id"]})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "preview_expired"


@pytest.mark.parametrize(
    "case",
    ["hash", "missing", "traversal", "duplicate", "link", "type", "requires", "suffix"],
)
def test_bad_bundles_fail_before_anything_is_written(api, study, tmp_path, case):
    src = tmp_path / "src"
    profile = {
        "id": "p",
        "type": "profile",
        "title": "P",
        "files": {"profiles/dose-escalation.yaml": PROFILE},
    }
    kw = {}
    comps = [profile]
    if case == "hash":
        kw["tamper"] = {"profiles/dose-escalation.yaml": PROFILE + b"\n# edited\n"}
    elif case == "missing":
        write_bundle(src, comps)
        (src / "profiles/dose-escalation.yaml").unlink()
    elif case == "duplicate":
        comps = [profile, {**profile, "files": {"profiles/other.yaml": PROFILE}}]
    elif case == "link":
        comps = [{**profile, "links": ["nowhere"]}]
    elif case == "type":
        comps = [{**profile, "type": "spreadsheet"}]
    elif case == "requires":
        kw["extra"] = {"requires": {"opncafyr": ">=99"}}
    elif case == "suffix":
        comps = [{**profile, "files": {"profiles/data.csv": b"ID,DV\n1,2\n"}}]
    if case != "missing":
        write_bundle(src, comps, **kw)
    if case == "traversal":
        text = (src / "standards.yaml").read_text("utf-8")
        (src / "standards.yaml").write_text(
            text.replace("profiles/dose-escalation.yaml", "../escape.yaml"), "utf-8"
        )
    preview = inspect(api, src).json()
    assert preview["problems"], case
    assert preview["preview_id"] is None
    assert not (study / "profiles").exists()
    assert not (study / S.STATE_FILE).exists()
    assert not list((study / S.STORE).glob("*/*")) if (study / S.STORE).exists() else True


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_file_reached_through_a_link_is_refused(api, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "dose-escalation.yaml").write_bytes(PROFILE)
    src = tmp_path / "src"
    write_bundle(
        src,
        [
            {
                "id": "p",
                "type": "profile",
                "title": "P",
                "files": {"profiles/dose-escalation.yaml": PROFILE},
            }
        ],
    )
    (src / "profiles/dose-escalation.yaml").unlink()
    (src / "profiles").rmdir()
    (src / "profiles").symlink_to(outside)
    preview = inspect(api, src).json()
    assert any("link" in p for p in preview["problems"])


def test_partial_bundle_profile_only(api, study, tmp_path):
    src = write_bundle(
        tmp_path / "src",
        [{"id": "p", "type": "profile", "title": "P", "files": {"dose-escalation.yaml": PROFILE}}],
    )
    preview = inspect(api, src).json()
    assert preview["problems"] == []
    api.post(
        "/api/standards/install", json={"preview_id": preview["preview_id"]}
    ).raise_for_status()
    assert (study / "profiles/dose-escalation.yaml").is_file()
    assert state(study)["selected"] == {}


def test_local_edits_are_never_overwritten(api, study, tmp_path):
    v1 = mixed_bundle(tmp_path / "v1")
    first = inspect(api, v1).json()
    api.post("/api/standards/install", json={"preview_id": first["preview_id"]}).raise_for_status()

    # An unedited copy is updated by a new version; an edited one is a conflict.
    edited_tutorial = TUTORIAL.replace(b"title:", b"title:", 1) + b"# my note\n"
    (study / "tutorials/first-analysis.yaml").write_bytes(edited_tutorial)
    new_profile = PROFILE + b"# v2\n"
    v2 = tmp_path / "v2"
    write_bundle(
        v2,
        [
            {
                "id": "dose-escalation",
                "type": "profile",
                "title": "DE",
                "files": {"profiles/dose-escalation.yaml": new_profile},
            },
            {
                "id": "first-analysis",
                "type": "tutorial",
                "title": "FA",
                "files": {"tutorials/first-analysis.yaml": TUTORIAL + b"# v2\n"},
            },
        ],
        version="2.0.0",
    )
    preview = inspect(api, v2).json()
    plans = {r["dest"]: r for c in preview["components"] for r in c["plan"]}
    assert plans["profiles/dose-escalation.yaml"]["status"] == "update"
    conflict = plans["tutorials/first-analysis.yaml"]
    assert conflict["status"] == "conflict" and conflict["reason"] == "edited"
    assert conflict["copy_to"] == "tutorials/first-analysis-2-0-0.yaml"
    assert any(line.startswith("-# my note") for line in conflict["diff"])

    refused = api.post("/api/standards/install", json={"preview_id": preview["preview_id"]})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "needs_choice"
    assert refused.json()["detail"]["conflicts"] == ["tutorials/first-analysis.yaml"]
    assert (study / "profiles/dose-escalation.yaml").read_bytes() == PROFILE  # nothing yet

    done = api.post(
        "/api/standards/install",
        json={
            "preview_id": preview["preview_id"],
            "choices": {"tutorials/first-analysis.yaml": "copy"},
        },
    ).json()
    assert (study / "profiles/dose-escalation.yaml").read_bytes() == new_profile
    assert (study / "tutorials/first-analysis.yaml").read_bytes() == edited_tutorial
    assert (study / "tutorials/first-analysis-2-0-0.yaml").read_bytes() == TUTORIAL + b"# v2\n"
    assert {"path": "tutorials/first-analysis-2-0-0.yaml", "action": "copy"} in done["files"]
    # Both versions stay imported.
    assert len(state(study)["snapshots"]) == 2


def test_a_file_the_study_made_itself_is_a_conflict(api, study, tmp_path):
    (study / "profiles").mkdir()
    (study / "profiles/dose-escalation.yaml").write_bytes(PROFILE + b"# mine\n")
    preview = inspect(api, mixed_bundle(tmp_path / "src")).json()
    rows = [r for c in preview["components"] for r in c["plan"]]
    assert rows[0]["status"] == "conflict" and rows[0]["reason"] == "not_from_a_bundle"
    api.post(
        "/api/standards/install",
        json={
            "preview_id": preview["preview_id"],
            "choices": {"profiles/dose-escalation.yaml": "keep"},
        },
    ).raise_for_status()
    assert (study / "profiles/dose-escalation.yaml").read_bytes() == PROFILE + b"# mine\n"


def test_variants_and_links_choose_the_set(api, study, tmp_path):
    src = mixed_bundle(
        tmp_path / "src",
        extra={
            "variants": [{"id": "deck-only", "title": "Deck only", "components": ["house-deck"]}]
        },
    )
    preview = inspect(api, src).json()
    api.post(
        "/api/standards/install", json={"preview_id": preview["preview_id"], "variant": "deck-only"}
    ).raise_for_status()
    assert not (study / "profiles").exists()

    # A profile brings the tutorial and templates it links to.
    preview = inspect(api, src).json()
    api.post(
        "/api/standards/install",
        json={"preview_id": preview["preview_id"], "components": ["dose-escalation"]},
    ).raise_for_status()
    assert (study / "tutorials/first-analysis.yaml").is_file()


def test_archives_import_like_folders(api, study, tmp_path):
    src = mixed_bundle(tmp_path / "src")
    zpath = tmp_path / "house-1.0.0.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, f"house-1.0.0/{path.relative_to(src).as_posix()}")
        zf.writestr("../evil.txt", "x")
    preview = inspect(api, zpath).json()
    assert preview["problems"] == []
    assert preview["source"] == {"kind": "archive", "display": "house-1.0.0.zip"}
    assert any("left out" in n for n in preview["notes"])
    assert not (tmp_path / "evil.txt").exists()

    tpath = tmp_path / "house.tar.gz"
    with tarfile.open(tpath, "w:gz") as tf:
        tf.add(src, arcname=".")
        info = tarfile.TarInfo("evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    assert inspect(api, tpath).json()["problems"] == []


def test_a_study_relative_path_is_accepted(api, study):
    mixed_bundle(study / "incoming")
    preview = inspect(api, "incoming").json()
    assert preview["problems"] == [] and preview["source"]["display"] == "incoming"


def test_a_failed_import_keeps_the_selected_template(api, study, tmp_path):
    good = inspect(api, mixed_bundle(tmp_path / "v1")).json()
    api.post(
        "/api/standards/install",
        json={"preview_id": good["preview_id"], "select": ["house-report"]},
    ).raise_for_status()
    before = (study / S.STATE_FILE).read_bytes()
    bad = mixed_bundle(tmp_path / "v2", version="2.0.0", tamper={"report/config.yaml": b"x: 1\n"})
    assert inspect(api, bad).json()["problems"]
    assert (study / S.STATE_FILE).read_bytes() == before
    assert S.material(study, "report-template").component == "house-report"


def test_a_changed_snapshot_is_refused_at_use(api, study, tmp_path):
    preview = inspect(api, mixed_bundle(tmp_path / "src")).json()
    done = api.post(
        "/api/standards/install",
        json={"preview_id": preview["preview_id"], "select": ["house-report"]},
    ).json()
    target = study / S.STORE / done["snapshot"] / "report/config.yaml"
    target.chmod(0o644)
    target.write_bytes(b"figure_width: 1\n")
    with pytest.raises(S.StandardsError) as exc:
        S.material(study, "report-template")
    assert exc.value.code == "standards_changed"
    listing = api.get("/api/standards").json()
    assert listing["selected"]["report-template"]["ok"] is False


# --- references: a house style kept once ---------------------------------------------------


def house(tmp_path, version="1.2.0", name="house"):
    return write_bundle(
        tmp_path / name,
        [
            {
                "id": "report",
                "type": "report-template",
                "title": "House report",
                "files": report_files(),
            },
            {"id": "deck", "type": "deck-template", "title": "House deck", "files": deck_files()},
        ],
        bundle_id="house-style",
        version=version,
    )


def ddi(tmp_path, ref_extra=None):
    return write_bundle(
        tmp_path / "ddi",
        [
            {
                "id": "ddi",
                "type": "profile",
                "title": "DDI",
                "files": {
                    "drug-interaction.yaml": PROFILE.replace(
                        b"id: dose-escalation", b"id: drug-interaction"
                    )
                },
                "links": ["report"],
            },
            {
                "id": "report",
                "type": "report-template",
                "title": "House report",
                "ref": {
                    "bundle": "house-style",
                    "version": ">=1.0, <2",
                    "component": "report",
                    **(ref_extra or {}),
                },
            },
        ],
        bundle_id="ddi",
    )


def test_a_bundle_reference_resolves_once_the_house_style_is_imported(api, study, tmp_path):
    preview = inspect(api, ddi(tmp_path)).json()
    assert preview["problems"] == []
    report = next(c for c in preview["components"] if c["id"] == "report")
    assert report["resolved"] is None
    assert report["hint"] == "Import the standards bundle house-style >=1.0, <2."
    # Unresolved: the rest installs; selecting it is refused.
    refused = api.post(
        "/api/standards/install", json={"preview_id": preview["preview_id"], "select": ["report"]}
    )
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "unresolved"
    api.post(
        "/api/standards/install",
        json={"preview_id": inspect(api, ddi(tmp_path)).json()["preview_id"]},
    ).raise_for_status()

    for version in ("1.0.0", "1.2.0", "2.0.0"):
        p = inspect(api, house(tmp_path, version, f"house-{version}")).json()
        api.post("/api/standards/install", json={"preview_id": p["preview_id"]}).raise_for_status()
    listing = api.get("/api/standards").json()
    snap = next(s for s in listing["snapshots"] if s.get("bundle_id") == "ddi")
    resolved = next(c for c in snap["components"] if c["id"] == "report")["resolved"]
    assert resolved["snapshot"].startswith("house-style/1.2.0-")  # highest in range

    ddi_key = snap["key"]
    api.put(
        "/api/standards/selected",
        json={"type": "report-template", "snapshot": ddi_key, "component": "report"},
    ).raise_for_status()
    chosen = state(study)["selected"]["report-template"]
    assert chosen["snapshot"] == resolved["snapshot"]
    assert chosen["via"] == {"snapshot": ddi_key, "component": "report"}
    used = S.material(study, "report-template")
    assert used.provenance["version"] == "1.2.0"
    assert used.provenance["via"]["component"] == "report"


def test_a_pinned_bundle_reference_needs_the_exact_files(api, tmp_path):
    p = inspect(api, house(tmp_path)).json()
    api.post("/api/standards/install", json={"preview_id": p["preview_id"]}).raise_for_status()
    pinned = inspect(api, ddi(tmp_path, {"inventory_sha256": "0" * 64})).json()
    report = next(c for c in pinned["components"] if c["id"] == "report")
    assert report["resolved"] is None and "pinned hash" in report["hint"]
    exact = inspect(api, ddi(tmp_path, {"inventory_sha256": p["inventory_sha256"]})).json()
    report = next(c for c in exact["components"] if c["id"] == "report")
    assert report["resolved"]["snapshot"] == p["snapshot"]


def test_a_profile_can_not_carry_a_reference(api, tmp_path):
    src = write_bundle(
        tmp_path / "src",
        [
            {
                "id": "p",
                "type": "profile",
                "title": "P",
                "ref": {"bundle": "x", "version": ">=1", "component": "y"},
            }
        ],
    )
    assert any("only a template" in p for p in inspect(api, src).json()["problems"])


# --- tool-native template sources ----------------------------------------------------------


def native_deck(folder, typed=None):
    prefix = f"templates/{typed}/" if typed else ""
    for path, data in deck_files(prefix).items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
    (folder / "README.md").write_text("A deck template.\n")
    (folder / "notes.txt").write_text("not a template file\n")
    return folder


def test_a_deckifyr_template_folder_imports_as_its_own_snapshot(api, study, tmp_path):
    src = native_deck(tmp_path / "deck-src", typed="nca")
    preview = inspect(api, src, template={"tool": "deckifyr", "type": "nca"}).json()
    assert preview["problems"] == []
    assert preview["kind"] == "template" and preview["snapshot"].startswith("template-deckifyr/")
    assert any("not copied" in n for n in preview["notes"])
    done = api.post(
        "/api/standards/install", json={"preview_id": preview["preview_id"], "select": ["template"]}
    ).json()
    used = S.material(study, "deck-template")
    assert used.native_type == "nca"
    assert "templates/nca/presentation.yaml" in used.files
    assert used.provenance["tool"] == "deckifyr" and used.snapshot == done["snapshot"]
    assert state(study)["snapshots"][done["snapshot"]]["source"]["type"] == "nca"


def test_a_native_reference_with_a_hash_pin_resolves_to_a_local_copy(api, tmp_path):
    src = native_deck(tmp_path / "deck-src")
    p = inspect(api, src, template={"tool": "deckifyr"}).json()
    api.post("/api/standards/install", json={"preview_id": p["preview_id"]}).raise_for_status()
    bundle = write_bundle(
        tmp_path / "b",
        [
            {
                "id": "deck",
                "type": "deck-template",
                "title": "Org deck",
                "ref": {
                    "tool": "deckifyr",
                    "repo": "ghe.example.org/org/deck-templates",
                    "commit": "a" * 40,
                    "sha256": p["inventory_sha256"],
                },
            }
        ],
    )
    preview = inspect(api, bundle).json()
    assert preview["components"][0]["resolved"]["snapshot"] == p["snapshot"]
    wrong_tool = write_bundle(
        tmp_path / "c",
        [
            {
                "id": "deck",
                "type": "deck-template",
                "title": "x",
                "ref": {"tool": "quartifyr", "repo": "org/r"},
            }
        ],
    )
    assert inspect(api, wrong_tool).json()["problems"]


def test_a_template_pin_mismatch_is_refused(api, tmp_path):
    src = native_deck(tmp_path / "deck-src")
    preview = inspect(api, src, template={"tool": "deckifyr"}, expect={"sha256": "1" * 64}).json()
    assert any("pins" in p for p in preview["problems"])


def test_a_quartifyr_template_uses_its_report_files_and_lists_the_rest(api, study, tmp_path):
    src = tmp_path / "q"
    src.mkdir()
    (src / "_quarto.yml").write_text("project:\n  type: default\n")
    (src / "report.qmd").write_text("---\ntitle: x\n---\n")
    (src / "style.yaml").write_text("equation:\n  font: Cambria Math\n")
    (src / "org-reference.docx").write_bytes(report_files()["org-reference.docx"])
    preview = inspect(api, src, template={"tool": "quartifyr"}).json()
    assert preview["problems"] == []
    notes = " ".join(preview["notes"])
    assert "not used: report.qmd" in notes
    assert "1 other file(s)" in notes  # _quarto.yml: not copied at all
    assert "no standard_footnotes.yaml: the report keeps the bundled" in notes


@pytest.mark.parametrize(
    ("files", "problem"),
    [
        ({"presentation.yaml": b"slides: []\n"}, "design.base must name a file"),
        (
            {**deck_files(), "house-layouts.yaml": b"layouts:\n  blank:\n    elements: {}\n"},
            "no title-content layout",
        ),
        ({k: v for k, v in deck_files().items() if k != "logo.png"}, "background image"),
    ],
)
def test_deck_templates_must_fit_the_nca_results_deck(api, tmp_path, files, problem):
    src = write_bundle(
        tmp_path / "s", [{"id": "d", "type": "deck-template", "title": "D", "files": files}]
    )
    assert any(problem in p for p in inspect(api, src).json()["problems"])


def test_report_template_files_are_checked(api, tmp_path):
    src = write_bundle(
        tmp_path / "s",
        [
            {
                "id": "r",
                "type": "report-template",
                "title": "R",
                "files": {
                    "org-reference.docx": b"not a zip",
                    "config.yaml": b"report_dir_name: elsewhere\n",
                },
            }
        ],
    )
    problems = inspect(api, src).json()["problems"]
    assert any("not a Word document" in p for p in problems)
    assert any("config.yaml" in p for p in problems)


def test_selecting_none_goes_back_to_the_bundled_template(api, study, tmp_path):
    p = inspect(api, house(tmp_path)).json()
    api.post(
        "/api/standards/install", json={"preview_id": p["preview_id"], "select": ["deck"]}
    ).raise_for_status()
    assert S.material(study, "deck-template") is not None
    api.put(
        "/api/standards/selected",
        json={"type": "deck-template", "snapshot": None, "component": None},
    ).raise_for_status()
    assert S.material(study, "deck-template") is None


def test_routes_need_a_study_and_the_session(client, tmp_path):
    mixed_bundle(tmp_path / "src")
    body = {"source": {"kind": "path", "path": str(tmp_path / "src")}}
    assert client.post("/api/standards/inspect", json=body).status_code == 403
    client.headers.update(client.session_headers)
    assert client.post("/api/standards/inspect", json=body).status_code == 409


def test_inspect_writes_only_staging(api, study, tmp_path):
    """A preview stages inside .opncafyr/standards/ and nowhere else."""
    before = {p.relative_to(study) for p in study.rglob("*")}
    inspect(api, mixed_bundle(tmp_path / "src")).json()
    after = {p.relative_to(study) for p in study.rglob("*")} - before
    assert after and all(p.parts[:2] == (".opncafyr", "standards") for p in after)
    assert all(p.parts[2].startswith(S.STAGING_PREFIX) for p in after if len(p.parts) > 2)


def test_extract_archive_caps_and_skips(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok/file.yaml", "a: 1\n")
        zf.writestr("/abs.yaml", "a: 1\n")
        zf.writestr(".hidden/x.yaml", "a: 1\n")
    path = tmp_path / "a.zip"
    path.write_bytes(buf.getvalue())
    skipped = S.extract_archive(path, tmp_path / "out")
    assert skipped == 2
    assert [
        p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*.yaml")
    ] == ["ok/file.yaml"]
