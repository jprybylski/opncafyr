"""Native folder picker argv and result handling, with subprocess mocked (#17)."""

import base64
import subprocess
from pathlib import Path

import pytest

from opncafyr.core import picker
from opncafyr.core.picker import PickerError, PickerUnavailable, pick_folder

PROMPT = 'Choose\'; do shell script "rm -rf ~" --'
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


class FakeRun:
    def __init__(self, returncode=0, stdout=b"", stderr=b"", raises=None):
        self.result = (returncode, stdout, stderr)
        self.raises = raises
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises:
            raise self.raises
        code, out, err = self.result
        return subprocess.CompletedProcess(argv, code, out, err)


def test_macos_argv_is_fixed_and_user_text_is_data(tmp_path):
    run = FakeRun(stdout=f"{tmp_path}/\n".encode())
    chosen = pick_folder(PROMPT, tmp_path, platform="darwin", run=run)
    assert chosen == tmp_path
    ((argv, kwargs),) = run.calls
    assert argv[0] == "/usr/bin/osascript"
    script = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert tuple(script) == picker.MAC_SCRIPT
    assert argv[-3:] == ["--", PROMPT, str(tmp_path)]
    assert all(PROMPT not in line and str(tmp_path) not in line for line in script)
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == picker.PICKER_TIMEOUT_S


def test_macos_without_a_start_folder(tmp_path):
    run = FakeRun(stdout=b"/Users/a/Study/\n")
    assert pick_folder("p", tmp_path / "missing", platform="darwin", run=run) == Path(
        "/Users/a/Study"
    )
    argv = run.calls[0][0]
    assert argv[-2:] == ["--", "p"]


def test_macos_cancel_returns_none():
    run = FakeRun(returncode=1, stderr=b"execution error: User canceled. (-128)\n")
    assert pick_folder("p", platform="darwin", run=run) is None


def test_macos_failure_raises():
    run = FakeRun(returncode=1, stderr=b"execution error: something else (-1700)\n")
    with pytest.raises(PickerError):
        pick_folder("p", platform="darwin", run=run)


def test_unicode_paths_round_trip():
    run = FakeRun(stdout="/Users/a/Étude 研究/\n".encode())
    assert pick_folder("p", platform="darwin", run=run) == Path("/Users/a/Étude 研究")


def test_windows_argv_uses_an_encoded_fixed_script_and_env_data(tmp_path):
    run = FakeRun(stdout="C:\\Studies\\Étude".encode())
    chosen = pick_folder(PROMPT, tmp_path, platform="win32", run=run, powershell=lambda: POWERSHELL)
    assert str(chosen) == "C:\\Studies\\Étude"
    ((argv, kwargs),) = run.calls
    assert argv[0] == POWERSHELL
    flags = ["-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass"]
    assert argv[1:7] == [*flags, "-EncodedCommand"]
    assert base64.b64decode(argv[7]).decode("utf-16-le") == picker.WINDOWS_SCRIPT
    assert PROMPT not in picker.WINDOWS_SCRIPT
    assert kwargs["env"]["PICKER_PROMPT"] == PROMPT
    assert kwargs["env"]["PICKER_START"] == str(tmp_path)
    assert kwargs["shell"] is False


def test_windows_cancel_and_failure():
    cancel = FakeRun(stdout=b"")
    assert pick_folder("p", platform="win32", run=cancel, powershell=lambda: POWERSHELL) is None
    assert cancel.calls[0][1]["env"]["PICKER_START"] == ""
    failed = FakeRun(returncode=1, stderr=b"boom")
    with pytest.raises(PickerError):
        pick_folder("p", platform="win32", run=failed, powershell=lambda: POWERSHELL)


def test_drive_root_keeps_its_backslash():
    run = FakeRun(stdout=b"D:\\")
    assert str(pick_folder("p", platform="win32", run=run, powershell=lambda: POWERSHELL)) == (
        "D:\\"
    )


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("osascript", 600), FileNotFoundError("osascript")]
)
def test_timeout_and_missing_executable_raise(error):
    with pytest.raises(PickerError):
        pick_folder("p", platform="darwin", run=FakeRun(raises=error))


def test_a_relative_result_is_rejected():
    with pytest.raises(PickerError):
        pick_folder("p", platform="darwin", run=FakeRun(stdout=b"relative/path\n"))


def test_other_platforms_are_unavailable():
    with pytest.raises(PickerUnavailable):
        pick_folder("p", platform="linux", run=FakeRun())
