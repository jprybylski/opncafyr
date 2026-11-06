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


def test_no_shell_subprocesses():
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                assert isinstance(node.value, ast.Constant) and node.value.value is False, (
                    f"shell=True in {path}"
                )
            if isinstance(node, ast.Attribute) and node.attr in {"system", "popen"}:
                assert not (isinstance(node.value, ast.Name) and node.value.id == "os"), (
                    f"os.{node.attr} in {path}"
                )
