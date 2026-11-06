"""Workspace inspection, initialization, opening and migrations (#17)."""

import json
import os
from pathlib import Path

import pytest

from opncafyr.core import migrations
from opncafyr.core.manifest import validate
from opncafyr.core.workspace import (
    RECENT_MAX,
    RecentWorkspaces,
    WorkspaceError,
    init_workspace,
    inspect_folder,
    open_workspace,
)

META = ".opncafyr"


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root`` outside .opncafyr/, with file contents."""
    out: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] == META:
            continue
        out[rel.as_posix()] = path.read_bytes() if path.is_file() else None
    return out


def _write_meta(root: Path, document: object) -> Path:
    (root / META).mkdir()
    path = root / META / "workspace.json"
    path.write_text(json.dumps(document), "utf-8")
    return path


V0 = {
    "schema_version": "0",
    "created": "2026-01-02T03:04:05Z",
    "source_files": [{"path": "source-data/pk.csv", "sha256": "a" * 64, "bytes": 1234}],
    "prefs": {"theme": "dark"},
}


def test_init_on_a_non_empty_folder_creates_only_the_metadata_folder(tmp_path):
    (tmp_path / "pk.csv").write_text("USUBJID,TIME,CONC\nS1,0,1\n", "utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("keep me", "utf-8")
    before = _snapshot(tmp_path)
    top_before = sorted(p.name for p in tmp_path.iterdir())

    ws = init_workspace(tmp_path, "9.9.9")

    assert _snapshot(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*top_before, META])
    assert [p.name for p in (tmp_path / META).iterdir()] == ["workspace.json"]
    doc = json.loads((tmp_path / META / "workspace.json").read_text("utf-8"))
    validate(doc, "workspace-v1")
    assert doc["schema_version"] == migrations.CURRENT_VERSION
    assert doc["app_version"] == "9.9.9"
    assert doc["sources"] == []
    assert ws.root == tmp_path.resolve()
    assert ws.document == doc


def test_inspect_shows_the_path_and_counts_without_writing(tmp_path):
    (tmp_path / "a.csv").write_text("x", "utf-8")
    (tmp_path / "b").mkdir()
    info = inspect_folder(tmp_path)
    assert info.state == "new"
    assert info.path == str(tmp_path.resolve())
    assert info.entries == 2
    assert info.writable is True
    assert ".opncafyr/" in info.message
    assert not (tmp_path / META).exists()


def test_inspect_states(tmp_path):
    assert inspect_folder(tmp_path / "nope").state == "missing"
    (tmp_path / "file.txt").write_text("x", "utf-8")
    assert inspect_folder(tmp_path / "file.txt").state == "missing"

    incomplete = tmp_path / "incomplete"
    (incomplete / META).mkdir(parents=True)
    assert inspect_folder(incomplete).state == "incomplete"

    broken = tmp_path / "broken"
    broken.mkdir()
    _write_meta(broken, "not json object")
    assert inspect_folder(broken).state == "invalid"
    (broken / META / "workspace.json").write_text("{not json", "utf-8")
    assert inspect_folder(broken).state == "invalid"

    bad_schema = tmp_path / "bad_schema"
    bad_schema.mkdir()
    _write_meta(bad_schema, {"schema_version": "1.0", "created_at": "x"})
    assert inspect_folder(bad_schema).state == "invalid"

    meta_file = tmp_path / "meta_file"
    meta_file.mkdir()
    (meta_file / META).write_text("", "utf-8")
    assert inspect_folder(meta_file).state == "invalid"

    current = tmp_path / "current"
    current.mkdir()
    init_workspace(current, "1.2.3")
    info = inspect_folder(current)
    assert (info.state, info.schema_version, info.app_version) == ("workspace", "1.0", "1.2.3")


def test_an_unreadable_folder_is_not_reported_as_missing(tmp_path, monkeypatch):
    # Windows: a deny rule can block opening a folder that exists (WinError 5).
    folder = tmp_path / "locked"
    folder.mkdir()
    real_resolve = Path.resolve

    def resolve(self, strict=False):
        if self.name == "locked":
            raise PermissionError(5, "Access is denied")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    info = inspect_folder(folder)
    assert info.state == "unreadable"
    assert "permission" in info.message
    with pytest.raises(WorkspaceError) as err:
        open_workspace(folder)
    assert err.value.code == "unreadable"
    with pytest.raises(WorkspaceError) as err:
        init_workspace(folder, "1")
    assert err.value.code == "unreadable"
    assert not (folder / META).exists()


def test_inspect_reports_an_enclosing_workspace(tmp_path):
    init_workspace(tmp_path, "1")
    inner = tmp_path / "study" / "sub"
    inner.mkdir(parents=True)
    info = inspect_folder(inner)
    assert info.state == "new"
    assert info.enclosing_workspace == str(tmp_path.resolve())
    assert "inside another workspace" in info.message


def test_init_completes_an_incomplete_workspace_and_refuses_an_existing_one(tmp_path):
    (tmp_path / META / "jobs").mkdir(parents=True)
    init_workspace(tmp_path, "1")
    assert inspect_folder(tmp_path).state == "workspace"
    with pytest.raises(WorkspaceError) as err:
        init_workspace(tmp_path, "1")
    assert err.value.code == "exists"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions")
def test_init_without_write_permission_is_a_clear_error(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert inspect_folder(locked).writable is False
        with pytest.raises(WorkspaceError) as err:
            init_workspace(locked, "1")
        assert err.value.code == "permission"
        assert not (locked / META).exists()
    finally:
        locked.chmod(0o700)


def test_open_refuses_a_future_schema_with_a_clear_message(tmp_path):
    path = _write_meta(
        tmp_path,
        {"schema_version": "2.0", "created_at": "2030-01-01T00:00:00Z", "app_version": "3.1.0"},
    )
    original = path.read_bytes()
    info = inspect_folder(tmp_path)
    assert info.state == "newer"
    with pytest.raises(WorkspaceError) as err:
        open_workspace(tmp_path, allow_migrate=True)
    assert err.value.code == "newer"
    message = err.value.message
    assert "newer version of opncafyr" in message
    assert "schema 2.0" in message
    assert "opncafyr 3.1.0" in message
    assert "Update opncafyr" in message
    assert path.read_bytes() == original
    assert not (tmp_path / META / "backups").exists()


def test_a_newer_minor_version_is_also_refused(tmp_path):
    _write_meta(tmp_path, {"schema_version": "1.1", "created_at": "2030-01-01T00:00:00Z"})
    assert inspect_folder(tmp_path).state == "newer"


def test_migration_from_v0_produces_valid_v1_and_a_backup(tmp_path):
    path = _write_meta(tmp_path, V0)
    original = path.read_bytes()
    assert inspect_folder(tmp_path).state == "needs_migration"

    with pytest.raises(WorkspaceError) as err:
        open_workspace(tmp_path)
    assert err.value.code == "needs_migration"
    assert path.read_bytes() == original

    ws = open_workspace(tmp_path, allow_migrate=True)
    doc = json.loads(path.read_text("utf-8"))
    validate(doc, "workspace-v1")
    assert doc == {
        "schema_version": "1.0",
        "created_at": "2026-01-02T03:04:05Z",
        "sources": [
            {"id": "source-1", "path": "source-data/pk.csv", "sha256": "a" * 64, "size_bytes": 1234}
        ],
        "ui": {"theme": "dark"},
    }
    assert ws.document == doc
    assert ws.migrated_from == "0"
    (backup,) = ws.backups
    assert backup.parent == tmp_path.resolve() / META / "backups"
    assert backup.name.startswith("workspace-v0-")
    assert backup.read_bytes() == original
    assert inspect_folder(tmp_path).state == "workspace"


def test_a_failing_migration_leaves_the_original_untouched(tmp_path):
    path = _write_meta(tmp_path, {**V0, "surprise": 1})
    original = path.read_bytes()
    with pytest.raises(WorkspaceError) as err:
        open_workspace(tmp_path, allow_migrate=True)
    assert err.value.code == "migration_failed"
    assert path.read_bytes() == original


def test_an_old_version_with_no_upgrade_path_is_invalid(tmp_path):
    _write_meta(tmp_path, {"schema_version": "0.5", "created": "x"})
    info = inspect_folder(tmp_path)
    assert info.state == "invalid"
    assert "no upgrade path" in info.message


def test_migrations_form_one_chain_to_the_current_version():
    froms = [m.from_version for m in migrations.MIGRATIONS]
    assert len(froms) == len(set(froms))
    for m in migrations.MIGRATIONS:
        assert migrations.parse_version(m.from_version) < migrations.parse_version(m.to_version)
        assert migrations.chain(m.from_version)[-1].to_version == migrations.CURRENT_VERSION


@pytest.mark.parametrize("text", ["1", "1.0", "10.2", "0"])
def test_parse_version_accepts(text):
    migrations.parse_version(text)


@pytest.mark.parametrize("text", [None, 1, "", "v1", "1.0.0", "01", "1.x"])
def test_parse_version_rejects(text):
    with pytest.raises(ValueError):
        migrations.parse_version(text)


def test_recent_list_dedups_caps_and_survives_a_bad_file(tmp_path):
    store = RecentWorkspaces(tmp_path / "state" / "recent-workspaces.json")
    assert store.load() == []
    for i in range(RECENT_MAX + 3):
        store.remember(tmp_path / f"ws{i}")
    store.remember(tmp_path / "ws5")
    paths = store.paths()
    assert len(paths) == RECENT_MAX
    assert paths[0] == str(tmp_path / "ws5")
    assert paths.count(str(tmp_path / "ws5")) == 1
    validate(json.loads(store.path.read_text("utf-8")), "recent-workspaces-v1")

    store.path.write_text("{broken", "utf-8")
    assert store.load() == []
    store.remember(tmp_path / "again")
    assert store.paths() == [str(tmp_path / "again")]
