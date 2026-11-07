import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from opncafyr.core.jobmanager import (
    OWNER_FILE,
    CommandSpec,
    JobManager,
    JobManagerError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.jobs import JobState
from opncafyr.core.manifest import validate

PY = sys.executable
HERE = Path(__file__).parent

# The child starts a grandchild in its own session, writes both pids, then waits.
TREE = (
    "import os, subprocess, sys, time\n"
    "kw = {'start_new_session': True} if os.name != 'nt' else {}\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], **kw)\n"
    "open(sys.argv[2], 'w').write(f'{os.getpid()} {p.pid}')\n"
    "time.sleep(600)\n"
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "study"
    (root / ".opncafyr").mkdir(parents=True)
    return root


@pytest.fixture
def manager(workspace):
    jm = JobManager(poll_interval=0.05)
    jm.open(workspace)
    yield jm
    jm.shutdown(timeout=20)


def py_job(code, *extra, label="toy", output=None, timeout=30, pool="default", **kw):
    """A job running ``python -c code <staging or -> *extra``."""
    return JobRequest(
        kind="toy.run",
        label=label,
        timeout_s=timeout,
        pool=pool,
        output=output,
        build=lambda ctx: CommandSpec([PY, "-c", code, str(ctx.staging or "-"), *map(str, extra)]),
        **kw,
    )


def wait_state(jm, job_id, states, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = jm.get(job_id)["state"]
        if state in states:
            return state
        time.sleep(0.05)
    raise AssertionError(f"job stayed {jm.get(job_id)['state']}, wanted {states}")


def wait_file(path, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text().strip():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def gone(pid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def job_dir(workspace, job_id):
    return workspace / ".opncafyr" / "jobs" / job_id


WRITE_RESULT = (
    "import pathlib, sys\n"
    "print('OPNCAFYR-PROGRESS: Writing the result', flush=True)\n"
    "(pathlib.Path(sys.argv[1]) / 'result.txt').write_text('ok')\n"
)


def test_success_promotes_staging(manager, workspace):
    job_id = manager.submit(py_job(WRITE_RESULT, output="runs/r1"))
    assert manager.wait(job_id, 20) is JobState.COMPLETED
    target = workspace / "runs" / "r1"
    assert (target / "result.txt").read_text() == "ok"
    assert not (target / OWNER_FILE).exists()
    assert not (workspace / "runs" / "r1.staging").exists()

    d = job_dir(workspace, job_id)
    state = json.loads((d / "state.json").read_text())
    validate(state, "job-v1")
    assert state["exit_code"] == 0
    assert not (d / "diagnostic.json").exists()
    assert not (d / "tmp").exists() and not (d / "work").exists()

    events = manager.events(job_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    states = [e["state"] for e in events]
    assert states[0] == "queued" and states[-1] == "completed"
    assert {"preparing", "running", "reviewing"} <= set(states)
    assert "Writing the result" in [e["message"] for e in events]
    assert manager.events(job_id, after=len(events) - 1) == events[-1:]


def test_nonzero_exit_fails_and_keeps_staging(manager, workspace):
    code = WRITE_RESULT + "sys.exit(3)\n"
    job_id = manager.submit(py_job(code, output="runs/r1"))
    assert manager.wait(job_id, 20) is JobState.FAILED
    job = manager.get(job_id)
    assert job["reason"] == "exit_code" and job["exit_code"] == 3
    assert "exit code 3" in job["message"]
    assert not (workspace / "runs" / "r1").exists()
    assert (workspace / "runs" / "r1.staging" / "result.txt").exists()
    diag = json.loads((job_dir(workspace, job_id) / "diagnostic.json").read_text())
    assert diag["reason"] == "exit_code" and diag["staging_kept"] == "runs/r1.staging"


def test_explain_gives_the_failure_message(manager):
    request = py_job("import sys; sys.exit(2)", explain=lambda code: f"Bad input ({code}).")
    job_id = manager.submit(request)
    manager.wait(job_id, 20)
    assert manager.get(job_id)["message"] == "Bad input (2)."


def test_diagnose_reads_the_tools_own_error_report(manager):
    code = 'import sys; sys.stderr.write(\'{"code": "E_X"}\'); sys.exit(1)'

    def diagnose(ctx, exit_code):
        report = json.loads((ctx.job_dir / "stderr.log").read_text("utf-8"))
        return f"{report['code']} ({exit_code})"

    request = py_job(code, diagnose=diagnose, explain=lambda c: "fallback")
    job_id = manager.submit(request)
    manager.wait(job_id, 20)
    assert manager.get(job_id)["message"] == "E_X (1)"
    # A diagnosis that finds nothing, or breaks, falls back to explain.
    for broken in (lambda ctx, c: None, lambda ctx, c: 1 / 0):
        job_id = manager.submit(py_job(code, diagnose=broken, explain=lambda c: "fallback"))
        manager.wait(job_id, 20)
        assert manager.get(job_id)["message"] == "fallback"


def test_ok_exit_codes_run_the_review(manager, workspace):
    seen = []

    def review(ctx):
        seen.append((ctx.job_dir / "stdout.log").read_text("utf-8"))
        return ReviewResult(True)

    code = "import sys; print('found 2'); sys.exit(1)"
    job_id = manager.submit(py_job(code, review=review, ok_exit_codes=(0, 1)))
    assert manager.wait(job_id, 20) is JobState.COMPLETED
    assert seen == ["found 2\n"]
    with pytest.raises(ValueError):
        py_job("pass", ok_exit_codes=()).check()


def test_tmpdir_never_has_a_space(tmp_path):
    # R won't start with a temp folder whose path has a space; study folders often do.
    root = tmp_path / "PK Study"
    (root / ".opncafyr").mkdir(parents=True)
    jm = JobManager(poll_interval=0.05)
    jm.open(root)
    try:
        code = "import os; t = os.environ['TMPDIR']; print(t); print(os.path.isdir(t))"
        job_id = jm.submit(py_job(code))
        assert jm.wait(job_id, 20) is JobState.COMPLETED
        out = (job_dir(root, job_id) / "stdout.log").read_text("utf-8").split()
        assert out[1] == "True" and " " not in out[0]
        assert not Path(out[0]).exists()  # removed with the job
    finally:
        jm.shutdown(timeout=20)


def test_failed_review_is_not_promoted(manager, workspace):
    request = py_job(
        WRITE_RESULT,
        output="runs/r1",
        review=lambda ctx: ReviewResult(False, "the results table is missing"),
    )
    job_id = manager.submit(request)
    assert manager.wait(job_id, 20) is JobState.FAILED
    job = manager.get(job_id)
    assert job["reason"] == "review_failed"
    assert "the results table is missing" in job["message"]
    assert not (workspace / "runs" / "r1").exists()


def test_review_sees_the_staged_outputs(manager, workspace):
    seen = []

    def review(ctx):
        seen.append((ctx.staging / "result.txt").read_text())
        return ReviewResult(True)

    job_id = manager.submit(py_job(WRITE_RESULT, output="runs/r1", review=review))
    assert manager.wait(job_id, 20) is JobState.COMPLETED
    assert seen == ["ok"]


def test_existing_output_is_never_replaced(manager, workspace):
    (workspace / "runs" / "r1").mkdir(parents=True)
    job_id = manager.submit(py_job(WRITE_RESULT, output="runs/r1"))
    assert manager.wait(job_id, 20) is JobState.FAILED
    job = manager.get(job_id)
    assert job["reason"] == "prepare_failed" and "already exists" in job["message"]
    assert job["exit_code"] is None


@pytest.mark.parametrize("path", ["../outside", "/abs/path"])
def test_output_outside_the_workspace_is_refused(manager, path):
    job_id = manager.submit(py_job(WRITE_RESULT, output=path))
    assert manager.wait(job_id, 20) is JobState.FAILED
    assert "outside the workspace" in manager.get(job_id)["message"]


def test_stale_staging_from_a_finished_job_is_replaced(manager, workspace):
    first = manager.submit(py_job("import sys; sys.exit(1)", output="runs/r1"))
    manager.wait(first, 20)
    assert (workspace / "runs" / "r1.staging").exists()
    second = manager.submit(py_job(WRITE_RESULT, output="runs/r1"))
    assert manager.wait(second, 20) is JobState.COMPLETED


def test_unknown_staging_folder_blocks_the_job(manager, workspace):
    (workspace / "runs" / "r1.staging").mkdir(parents=True)
    job_id = manager.submit(py_job(WRITE_RESULT, output="runs/r1"))
    assert manager.wait(job_id, 20) is JobState.FAILED
    assert "staging folder" in manager.get(job_id)["message"]


def test_cancel_kills_the_whole_tree(manager, workspace, tmp_path):
    pids_file = tmp_path / "pids.txt"
    job_id = manager.submit(py_job(TREE, pids_file, output="runs/r1"))
    child, grandchild = map(int, wait_file(pids_file).split())
    assert wait_state(manager, job_id, {"running"}) == "running"
    manager.cancel(job_id)
    assert manager.wait(job_id, 20) is JobState.CANCELLED
    assert gone(child) and gone(grandchild)
    job = manager.get(job_id)
    assert job["reason"] == "cancelled"
    assert not (workspace / "runs" / "r1").exists()
    diag = json.loads((job_dir(workspace, job_id) / "diagnostic.json").read_text())
    assert diag["state"] == "cancelled" and diag["state_before"] == "running"


def test_timeout_fails_with_a_message_for_a_person(manager, workspace):
    code = (
        "import sys, time\n"
        "sys.stderr.write('Error near subject S-0042-RAW\\n'); sys.stderr.flush()\n"
        "time.sleep(600)\n"
    )
    job_id = manager.submit(py_job(code, label="report render", timeout=1))
    assert manager.wait(job_id, 30) is JobState.FAILED
    job = manager.get(job_id)
    assert job["reason"] == "timeout"
    assert "report render job ran longer than its 1-second limit" in job["message"]
    events = json.dumps(manager.events(job_id))
    assert "S-0042-RAW" not in job["message"] and "S-0042-RAW" not in events
    assert "S-0042-RAW" in (job_dir(workspace, job_id) / "stderr.log").read_text()


def test_pool_limit_queues_the_second_job(manager, tmp_path):
    gate = tmp_path / "gate"
    started = tmp_path / "started"
    wait_gate = (
        "import pathlib, sys, time\n"
        "pathlib.Path(sys.argv[2]).write_text('x')\n"
        "while not pathlib.Path(sys.argv[3]).exists(): time.sleep(0.05)\n"
    )
    first = manager.submit(py_job(wait_gate, started, gate, pool="rscript"))
    second = manager.submit(py_job("pass", pool="rscript"))
    other = manager.submit(py_job("pass", pool="python"))
    wait_file(started)
    assert manager.wait(other, 20) is JobState.COMPLETED  # another pool is not held up
    assert manager.get(first)["state"] == "running"
    assert manager.get(second)["state"] == "queued"
    gate.write_text("go")
    assert manager.wait(first, 20) is JobState.COMPLETED
    assert manager.wait(second, 20) is JobState.COMPLETED


def test_cancel_a_queued_job(manager, tmp_path):
    gate = tmp_path / "gate"
    blocker = manager.submit(
        py_job(
            "import pathlib, sys, time\n"
            "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(0.05)\n",
            gate,
        )
    )
    queued = manager.submit(py_job("pass"))
    assert manager.cancel(queued) is JobState.CANCELLED
    job = manager.get(queued)
    assert job["started_at"] is None and job["exit_code"] is None
    gate.write_text("go")
    assert manager.wait(blocker, 20) is JobState.COMPLETED
    assert manager.get(queued)["state"] == "cancelled"


def test_job_runs_with_no_client_watching(manager):
    # Nothing polls or holds a connection: the worker thread owns the job (a closed tab).
    job_id = manager.submit(py_job("import time; time.sleep(0.3)"))
    time.sleep(1.5)
    assert manager.get(job_id)["state"] == "completed"


def test_shutdown_cancels_live_jobs(workspace, tmp_path):
    jm = JobManager(poll_interval=0.05)
    jm.open(workspace)
    pids_file = tmp_path / "pids.txt"
    job_id = jm.submit(py_job(TREE, pids_file))
    child, grandchild = map(int, wait_file(pids_file).split())
    jm.shutdown(timeout=20)
    job = jm.get(job_id)
    assert job["state"] == "cancelled" and job["reason"] == "launcher_stopped"
    assert gone(child) and gone(grandchild)
    with pytest.raises(JobManagerError):
        jm.submit(py_job("pass"))


def test_submit_needs_a_workspace_and_a_timeout(workspace):
    jm = JobManager()
    with pytest.raises(JobManagerError):
        jm.submit(py_job("pass"))
    jm.open(workspace)
    for bad in (0, -1, float("inf")):
        with pytest.raises(ValueError):
            jm.submit(py_job("pass", timeout=bad))


def test_open_refuses_a_plain_folder(tmp_path):
    with pytest.raises(JobManagerError):
        JobManager().open(tmp_path)


def test_open_refuses_while_jobs_are_running(manager, workspace, tmp_path):
    gate = tmp_path / "gate"
    job_id = manager.submit(
        py_job(
            "import pathlib, sys, time\n"
            "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(0.05)\n",
            gate,
        )
    )
    with pytest.raises(JobManagerError):
        manager.open(workspace)
    gate.write_text("go")
    manager.wait(job_id, 20)


def test_reopen_reads_finished_jobs_back(manager, workspace):
    job_id = manager.submit(py_job("pass"))
    manager.wait(job_id, 20)
    again = JobManager()
    assert again.open(workspace) == []
    assert again.get(job_id)["state"] == "completed"
    assert again.events(job_id) == manager.events(job_id)


def test_killing_the_launcher_mid_job(workspace, tmp_path):
    pids_file = tmp_path / "pids.txt"
    launcher = subprocess.Popen([PY, str(HERE / "job_launcher.py"), str(workspace), str(pids_file)])
    try:
        child, grandchild = map(int, wait_file(pids_file, timeout=30).split())
        jobs = list((workspace / ".opncafyr" / "jobs").iterdir())
        assert len(jobs) == 1
        state = json.loads((jobs[0] / "state.json").read_text())
        assert state["state"] == "running"
        # On Windows a venv python.exe is a small launcher that starts the real
        # interpreter as its child, so the recorded pid may be the child's parent.
        recorded = psutil.Process(state["pid"])
        assert child in {recorded.pid, *(p.pid for p in recorded.children(recursive=True))}
    finally:
        launcher.kill()  # the launcher only, not its tree
        launcher.wait(10)

    jm = JobManager()
    swept = jm.open(workspace)
    assert swept == [jobs[0].name]
    job = jm.get(jobs[0].name)
    assert job["state"] == "failed" and job["reason"] == "launcher_restarted"
    assert "launcher stopped while the orphan test job was running" in job["message"]
    assert not (workspace / "out" / "run1").exists()
    assert (workspace / "out" / "run1.staging").exists()
    assert gone(child) and gone(grandchild)
    diag = json.loads((jobs[0] / "diagnostic.json").read_text())
    assert diag["state_at_stop"] == "running"
