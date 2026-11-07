"""Static checks for the core invariants in CLAUDE.md."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "opncafyr"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_never_imports_adapters_or_workflows():
    for path in (SRC / "core").rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith(("opncafyr.adapters", "opncafyr.workflows")), (
                f"{path.name} imports {name}"
            )


# Processes start only through proctree.spawn (argv list, absolute executable from the
# pinned runtime, allow-listed env). picker.py runs fixed system dialogs (osascript,
# PowerShell) with argv arrays. installer/proc.py is the installer's equivalent of
# proctree (same rules); the installer can't use proctree, which needs psutil.
SUBPROCESS_ALLOWED = {"core/proctree.py", "core/picker.py", "installer/proc.py"}
OS_PROCESS_CALLS = {"system", "popen", "startfile"} | {
    f"{base}{suffix}"
    for base in ("exec", "spawn")
    for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")
}
FORBIDDEN_MODULES = {"pty", "commands", "popen2"}


def test_no_shell_subprocesses():
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                assert isinstance(node.value, ast.Constant) and node.value.value is False, (
                    f"shell=True in {rel}"
                )
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "os":
                    assert node.attr not in OS_PROCESS_CALLS, f"os.{node.attr} in {rel}"
                if node.value.id == "asyncio":
                    assert not node.attr.startswith("create_subprocess"), (
                        f"asyncio.{node.attr} in {rel}"
                    )
        for name in _imports(path):
            root = name.split(".")[0]
            assert root not in FORBIDDEN_MODULES, f"{rel} imports {name}"
            if root == "subprocess":
                assert rel in SUBPROCESS_ALLOWED, f"{rel} imports subprocess; use proctree.spawn"


def test_spawn_needs_an_absolute_executable(tmp_path):
    from opncafyr.core.proctree import spawn

    for argv in ([], ["python"], ["relative/bin/python"]):
        try:
            spawn(argv, env={}, cwd=tmp_path)
        except ValueError:
            continue
        raise AssertionError(f"spawn accepted {argv!r}")


def test_core_finds_workflow_packs_by_package_scan():
    # Core never imports a workflow by name; the NCA pack is found by scanning.
    from opncafyr.core.datasets import DatasetAnnotator, Importer
    from opncafyr.core.registry import load_workflows

    packs = {p.id: p for p in load_workflows()}
    assert "nca" in packs
    assert all(isinstance(i, Importer) for i in packs["nca"].importers)
    assert all(isinstance(a, DatasetAnnotator) for a in packs["nca"].annotators)
    assert ".csv" in {ext for i in packs["nca"].importers for ext in i.extensions}
    # The pack brings its own routes and page; core only lists and mounts them.
    assert callable(packs["nca"].mount)
    assert [v.id for v in packs["nca"].views] == ["analysis", "publish"]


def test_workflow_views_are_checked():
    from opncafyr.core.registry import WorkflowView

    for bad in (
        WorkflowView("Bad", "x", "a.js"),
        WorkflowView("ok", "x", "../a.js"),
        WorkflowView("ok", "x", "a.py"),
    ):
        try:
            bad.check()
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


# The installer runs under the bare pinned Python before anything is installed (#19).
INSTALLER_ALLOWED = ("opncafyr.installer", "opncafyr.core.redaction", "opncafyr.core.manifest")


def test_installer_needs_only_the_standard_library():
    import sys

    for path in (SRC / "installer").rglob("*.py"):
        if path.name == "selfcheck.py":
            continue  # runs in the new launcher environment, after the install
        for name in _imports(path):
            root = name.split(".")[0]
            if root == "opncafyr":
                assert name.startswith(INSTALLER_ALLOWED) or name == "opncafyr", (
                    f"{path.name} imports {name}"
                )
            else:
                assert root in sys.stdlib_module_names or root == "__future__", (
                    f"installer/{path.name} imports {name}, which isn't in the standard library"
                )


def test_installer_imports_cleanly_without_third_party_packages(tmp_path):
    """Import every installer module with site-packages hidden, as the bare Python would."""
    import subprocess
    import sys

    code = (
        "import sys, pkgutil, importlib; sys.path = [p for p in sys.path if 'site-packages' "
        "not in p]; sys.path.insert(0, sys.argv[1]); import opncafyr.installer as pkg; "
        "[importlib.import_module(m.name) for m in pkgutil.walk_packages(pkg.__path__, "
        "'opncafyr.installer.') if not m.name.endswith(('selfcheck', '__main__'))]; "
        "import jsonschema"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code, str(SRC.parent)], capture_output=True, text=True
    )
    # Every installer module imported; jsonschema (third party) did not.
    assert "No module named 'jsonschema'" in done.stderr, done.stderr
