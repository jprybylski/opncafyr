import os
import sys
import time

import psutil
import pytest

from opncafyr.core.proctree import DENIED_ENV, child_env, spawn, system_path_dirs

BASE = {
    "HOME": "/home/u",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "C",
    "SECRET_TOKEN": "x",
    "OPNCAFYR_RUNTIME_FILE": "/r.json",
    "OPNCA_APP_ALLOWED_HOSTS": "*",
    "RSTUDIO_PROXY_PATH": "/p",
    "VSCODE_PROXY_URI": "https://x/{{port}}",
    "RS_SERVER_URL": "https://x",
    "PATH": "/evil/bin",
    "SYSTEMROOT": r"C:\Windows",
}


def test_env_is_allow_listed():
    env = child_env({"TOOL_TOKEN": "t"}, ["/pinned/bin"], base=BASE)
    assert env["TOOL_TOKEN"] == "t"
    assert "SECRET_TOKEN" not in env
    assert not any(k in env for k in DENIED_ENV)
    assert not any(k.startswith("OPNCAFYR_") for k in env)
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == "/pinned/bin"
    assert "/evil/bin" not in parts
    assert parts[1:] == system_path_dirs()


@pytest.mark.skipif(os.name == "nt", reason="POSIX allow-list")
def test_env_keeps_locale_on_posix():
    env = child_env(base=BASE)
    assert env["HOME"] == "/home/u"
    assert env["LC_ALL"] == "C"


def test_denied_vars_cannot_come_back_through_extra():
    env = child_env({"RS_SERVER_URL": "x", "OPNCAFYR_X": "y"}, base=BASE)
    assert "RS_SERVER_URL" not in env
    assert "OPNCAFYR_X" not in env


def test_path_only_through_path_dirs():
    with pytest.raises(ValueError):
        child_env({"PATH": "/x"}, base=BASE)


def test_spawn_requires_absolute_executable(tmp_path):
    with pytest.raises(ValueError):
        spawn(["python", "-c", "pass"], env=child_env(), cwd=tmp_path)


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_kill_tree_kills_a_new_session_grandchild(tmp_path):
    # The child starts a grandchild in its own session (like processx and deckifyr do),
    # prints its pid, then waits.
    code = (
        "import os, subprocess, sys, time\n"
        "kw = {'start_new_session': True} if os.name != 'nt' else {}\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], **kw)\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(600)\n"
    )
    proc = spawn([sys.executable, "-c", code], env=child_env(), cwd=tmp_path)
    grandchild = int(proc.popen.stdout.readline())
    assert _alive(grandchild)
    proc.kill_tree()
    deadline = time.monotonic() + 10
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(grandchild)
    assert proc.poll() is not None
    proc.popen.stdout.close()
