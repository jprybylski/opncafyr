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
# PowerShell) with argv arrays.
SUBPROCESS_ALLOWED = {"core/proctree.py", "core/picker.py"}
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
