"""#41: scripts/check_wheel.py refuses a wheel that carries a development-only tool."""

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_wheel", ROOT / "scripts" / "check_wheel.py")
check_wheel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_wheel)


def _wheel(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return path


def test_a_release_shaped_wheel_passes(tmp_path):
    wheel = _wheel(tmp_path / "ok.whl", list(check_wheel.REQUIRED))
    assert check_wheel.check_wheel(wheel) == []
    assert check_wheel.main([str(wheel)]) == 0


def test_the_toy_adapter_is_refused(tmp_path):
    names = [*check_wheel.REQUIRED, "opncafyr/adapters/example_echo/descriptor.yaml"]
    problems = check_wheel.check_wheel(_wheel(tmp_path / "toy.whl", names))
    assert problems == [
        "opncafyr/adapters/example_echo/descriptor.yaml must not be in a release wheel"
    ]


def test_a_missing_required_file_is_named(tmp_path):
    wheel = _wheel(tmp_path / "thin.whl", ["opncafyr/__init__.py"])
    assert "opncafyr/web/static/index.html is missing" in check_wheel.check_wheel(wheel)
    assert check_wheel.main([str(wheel)]) == 1


def test_pyproject_excludes_every_forbidden_folder():
    text = (ROOT / "pyproject.toml").read_text("utf-8")
    for prefix in check_wheel.FORBIDDEN:
        assert f'"src/{prefix.rstrip("/")}"' in text
