import time

import psutil
import pytest

from opncafyr.core.proctree import free_loopback_port
from opncafyr.core.supervisor import ChildState, LaunchError
from toy_adapter import ready_info, toy_supervisor


@pytest.fixture
def sup(tmp_path):
    supervisor, adapter = toy_supervisor(tmp_path)
    supervisor.adapter = adapter
    yield supervisor
    supervisor.stop_all()


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_start_waits_for_ready_and_uses_launcher_dirs(sup, tmp_path):
    child = sup.start("toy", {})
    assert child.state is ChildState.RUNNING
    info = ready_info(child)
    assert info["url"] == child.context.base_url
    assert child.context.workdir.is_relative_to(tmp_path / "state" / "children")
    assert child.context.tmpdir.is_dir()
    assert child.log_path.exists()


def test_child_env_has_token_and_app_tmpdir_only(sup):
    child = sup.start("toy", {"env": {"RS_SERVER_URL": "https://x"}})
    log = child.log_path.read_text()
    keys = log.split("toy env keys: ")[1].splitlines()[0].split(",")
    assert "TOY_TOKEN" in keys and "TMPDIR" in keys
    assert "RS_SERVER_URL" not in keys
    assert not any(k.startswith("OPNCAFYR_") for k in keys)


def test_tmpdir_never_has_a_space(tmp_path):
    # R won't start with a temp folder whose path has a space, and the macOS app root
    # (~/Library/Application Support) has one, so opnca-r's server failed on every install.
    sup, _ = toy_supervisor(tmp_path / "Application Support")
    try:
        child = sup.start("toy", {})
        tmp = child.context.tmpdir
        assert tmp.is_dir() and not any(ch.isspace() for ch in str(tmp))
        assert child.context.workdir.is_relative_to(tmp_path / "Application Support")
        sup.stop("toy")
        assert not tmp.exists() and not child.context.workdir.exists()
    finally:
        sup.stop_all()


def test_orderly_stop_exits_zero_and_cleans_up(sup):
    child = sup.start("toy", {})
    sup.stop("toy")
    assert sup.adapter.stops == [child.pid]
    assert child.exit_code == 0
    assert child.state is ChildState.STOPPED
    assert not child.context.workdir.exists()


def test_stop_kills_grandchildren(sup):
    child = sup.start("toy", {"grandchild": True})
    grandchild = ready_info(child)["grandchild"]
    assert not _gone(grandchild)
    sup.stop("toy")
    deadline = time.monotonic() + 10
    while not _gone(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _gone(grandchild)


def test_retry_on_early_exit_uses_a_new_port(tmp_path):
    picked: list[int] = []

    def picker() -> int:
        picked.append(free_loopback_port())
        return picked[-1]

    supervisor, _ = toy_supervisor(tmp_path, port_picker=picker)
    try:
        child = supervisor.start("toy", {"fail_first": str(tmp_path / "marker")})
        assert child.state is ChildState.RUNNING
        assert len(picked) == 2
    finally:
        supervisor.stop_all()


def test_never_ready_raises_without_retry(tmp_path):
    picked: list[int] = []

    def picker() -> int:
        picked.append(free_loopback_port())
        return picked[-1]

    supervisor, _ = toy_supervisor(tmp_path, ready_timeout=1.5, port_picker=picker)
    with pytest.raises(LaunchError, match="not ready"):
        supervisor.start("toy", {"never_ready": True})
    assert supervisor.state("toy") is ChildState.STOPPED
    assert len(picked) == 1  # alive but never ready: a new port wouldn't help


def test_crash_is_detected_and_restart_works(sup):
    import httpx

    child = sup.start("toy", {})
    with pytest.raises(httpx.HTTPError):
        httpx.get(child.context.base_url + "api/crash", headers=child.spec.auth_headers)
    child.proc.wait(10)
    assert sup.state("toy") is ChildState.CRASHED
    assert sup.can_restart("toy")
    again = sup.restart("toy")
    assert again.state is ChildState.RUNNING
    assert again.pid != child.pid


def test_restart_needs_a_previous_start(sup):
    assert sup.state("toy") is ChildState.NOT_STARTED
    with pytest.raises(LaunchError):
        sup.restart("toy")
