import os

import pytest

from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root


def test_relative_path_resolves_inside(tmp_path):
    assert resolve_in_root(tmp_path, "data/pk.csv") == tmp_path.resolve() / "data" / "pk.csv"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../outside.csv",
        "a/../../outside.csv",
        "/etc/passwd",
        "C:\\Windows",
        "\\\\server\\share",
    ],
)
def test_escapes_rejected(tmp_path, bad):
    with pytest.raises(WorkspaceAccessError):
        resolve_in_root(tmp_path, bad)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(WorkspaceAccessError):
        resolve_in_root(root, "link/secret.csv")
