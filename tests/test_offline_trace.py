"""The offline CI job's strace check (#44): loopback is fine, anything else fails."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "scripts" / "offline" / "check_trace.py"
_spec = importlib.util.spec_from_file_location("check_trace", _PATH)
check_trace = importlib.util.module_from_spec(_spec)
sys.modules["check_trace"] = check_trace
_spec.loader.exec_module(check_trace)

V4 = (
    'connect(3, {sa_family=AF_INET, sin_port=htons({port}), sin_addr=inet_addr("{addr}")}, 16)'
    " = -1 ENETUNREACH (Network is unreachable)"
)
V6 = (
    "connect(5, {sa_family=AF_INET6, sin6_port=htons({port}), sin6_flowinfo=htonl(0), "
    'inet_pton(AF_INET6, "{addr}", &sin6_addr), sin6_scope_id=0}, 28) = -1 ENETUNREACH'
)


def _v4(addr, port=443):
    return V4.replace("{addr}", addr).replace("{port}", str(port))


def _v6(addr, port=443):
    return V6.replace("{addr}", addr).replace("{port}", str(port))


@pytest.mark.parametrize(
    "line",
    [
        _v4("127.0.0.1", 8123),
        _v4("127.1.2.3", 80),
        _v4("0.0.0.0", 8080),  # noqa: S104 - a strace line, not a bind
        _v6("::1", 8123),
        _v6("::ffff:127.0.0.1", 8123),
        'connect(4, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT',
        'connect(4, {sa_family=AF_UNSPEC, sa_data="\\0\\0"}, 16) = 0',
        "sendto(3, [{nlmsg_len=20, nlmsg_type=RTM_GETADDR}], 20, 0, "
        "{sa_family=AF_NETLINK, nl_pid=0, nl_groups=00000000}, 12) = 20",
        'execve("/usr/bin/uv", ["uv", "sync"], 0x7ffd /* 30 vars */) = 0',
    ],
)
def test_allowed(tmp_path, line):
    f = tmp_path / "trace.100"
    f.write_text(line + "\n")
    assert check_trace.scan_file(f) == []


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (_v4("140.82.112.3"), "non-loopback address"),
        (_v6("2606:50c0:8000::153"), "non-loopback address"),
        (_v6("::ffff:140.82.112.3"), "non-loopback address"),
        (_v4("127.0.0.53", 53), "DNS lookup"),
        (_v4("10.0.0.1", 53), "DNS lookup"),
        (
            'sendto(5, "\\x12\\x34", 40, MSG_NOSIGNAL, {sa_family=AF_INET, '
            'sin_port=htons(53), sin_addr=inet_addr("8.8.8.8")}, 16) = -1 ENETUNREACH',
            "DNS lookup",
        ),
        (_v4("151.101.0.223").replace(") = -1 ENETUNREACH", " <unfinished ...>"), None),
    ],
)
def test_blocked(tmp_path, line, reason):
    f = tmp_path / "trace.100"
    f.write_text('execve("/opt/tool", ["tool"], 0x0 /* 1 var */) = 0\n' + line + "\n")
    [attempt] = check_trace.scan_file(f)
    assert attempt.program == "/opt/tool"
    if reason:
        assert attempt.reason == reason
    assert attempt.pid == "100"


@pytest.mark.parametrize("prefix", ["{pid} ", "[pid  {pid}] "])
def test_program_per_process_and_inherited_on_fork(tmp_path, prefix):
    def p(pid):
        return prefix.format(pid=pid)

    f = tmp_path / "trace.txt"
    f.write_text(
        p(10)
        + 'execve("/usr/bin/R", ["R"], 0x0 /* 1 var */) = 0\n'
        + p(10)
        + "clone(child_stack=NULL, flags=CLONE_CHILD_SETTID|SIGCHLD) = 11\n"
        + p(11)
        + 'execve("/usr/bin/uv", ["uv", "sync"], 0x0 /* 1 var */) = 0\n'
        + p(11)
        + "clone3({flags=CLONE_VM, exit_signal=0} => {parent_tid=[12]}, 88) = 12\n"
        + p(12)
        + _v4("151.101.0.223")
        + "\n"
        + p(10)
        + _v4("8.8.8.8", 53)
        + "\n"
    )
    by_pid = {a.pid: a for a in check_trace.scan_file(f)}
    assert by_pid["12"].program == "/usr/bin/uv"
    assert by_pid["10"].program == "/usr/bin/R"
    assert by_pid["10"].reason == "DNS lookup"


def test_failed_execve_does_not_rename_the_program(tmp_path):
    f = tmp_path / "trace.7"
    f.write_text(
        'execve("/usr/bin/R", ["R"], 0x0 /* 1 var */) = 0\n'
        'execve("/nope/curl", ["curl"], 0x0 /* 1 var */) = -1 ENOENT (No such file)\n'
        + _v4("1.2.3.4")
        + "\n"
    )
    [attempt] = check_trace.scan_file(f)
    assert attempt.program == "/usr/bin/R"


def test_main_exit_codes_and_json(tmp_path, capsys):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "trace.1").write_text(_v4("127.0.0.1", 9000) + "\n")
    assert check_trace.main([str(clean)]) == 0

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "trace.1").write_text(_v4("127.0.0.1", 9000) + "\n")
    (dirty / "trace.2").write_text(_v4("140.82.112.3") + "\n")
    report = tmp_path / "report.json"
    assert check_trace.main([str(dirty), "--json", str(report)]) == 1
    doc = json.loads(report.read_text())
    assert doc["files"] == 2
    assert [a["address"] for a in doc["attempts"]] == ["140.82.112.3"]
    assert "NETWORK ATTEMPT" in capsys.readouterr().err


def test_main_usage_errors(tmp_path):
    assert check_trace.main([str(tmp_path / "missing")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert check_trace.main([str(empty)]) == 2


def test_allow_rule_tolerates_a_listed_command(tmp_path, capsys):
    trace = tmp_path / "trace.txt"
    trace.write_text(
        '5 execve("/opt/R/bin/exec/R", ["R", "--no-echo", "--file=tools/cacert.R"], 0x0) = 0\n'
        "5 " + _v4("10.255.255.254", 53) + "\n"
        '6 execve("/usr/bin/curl", ["curl", "https://example.org"], 0x0) = 0\n'
        "6 " + _v4("93.184.215.14") + "\n"
    )
    rules = tmp_path / "allow.toml"
    rules.write_text(
        '[[allow]]\ncommand_contains = "--file=tools/cacert.R"\nreason = "r"\nissue = "#44"\n'
    )
    report = tmp_path / "report.json"
    assert check_trace.main([str(trace), "--allow", str(rules), "--json", str(report)]) == 1
    doc = json.loads(report.read_text())
    assert (doc["blocked"], doc["allowed"]) == (1, 1)
    allowed = [a for a in doc["attempts"] if a["allowed_by"]]
    assert allowed[0]["program"] == "/opt/R/bin/exec/R"
    assert "cacert.R" in allowed[0]["command"]

    trace.write_text(trace.read_text().split("6 execve")[0])
    assert check_trace.main([str(trace), "--allow", str(rules)]) == 0
    assert "allowed by" in capsys.readouterr().err


def test_bad_allow_file_is_a_usage_error(tmp_path):
    trace = tmp_path / "trace.txt"
    trace.write_text(_v4("127.0.0.1", 80) + "\n")
    rules = tmp_path / "allow.toml"
    rules.write_text('[[allow]]\ncommand_contains = ""\nreason = "r"\nissue = "#44"\n')
    assert check_trace.main([str(trace), "--allow", str(rules)]) == 2


def test_shipped_allow_file_is_valid():
    rules = check_trace.load_rules(_PATH.parent / "allowed_attempts.toml")
    assert rules and all(r["issue"].startswith("#") for r in rules)


@pytest.mark.parametrize(
    "resumed",
    [
        "<... clone3 resumed> => {parent_tid=[21]}, 88) = 21",
        "<... clone resumed>, child_tidptr=0x7f) = 21",
        "<... vfork resumed>) = 21",
    ],
)
def test_child_printed_before_the_parent_clone_returns(tmp_path, resumed):
    # strace -f into one file: the child's first syscalls can come before the parent's
    # "resumed ... = <child>" line (seen in CI with R's libcurl lookups).
    trace = tmp_path / "trace.txt"
    trace.write_text(
        '20 execve("/opt/R/bin/exec/R", ["R", "--file=tools/cacert.R"], 0x0) = 0\n'
        "20 clone3({flags=CLONE_VM|CLONE_THREAD, exit_signal=0}, 88 <unfinished ...>\n"
        "21 " + _v4("127.0.0.53", 53) + "\n"
        "20 " + resumed + "\n"
        "21 " + _v4("127.0.0.53", 53) + "\n"
    )
    attempts = check_trace.scan_file(trace)
    assert [a.program for a in attempts] == ["/opt/R/bin/exec/R"] * 2
    rules = [{"command_contains": "--file=tools/cacert.R", "reason": "r", "issue": "#44"}]
    assert all(a.allowed_by for a in check_trace.apply_rules(attempts, rules))


def test_an_attempt_from_an_unknown_process_stays_blocked(tmp_path):
    trace = tmp_path / "trace.txt"
    trace.write_text(
        '20 execve("/opt/R/bin/exec/R", ["R", "--file=tools/cacert.R"], 0x0) = 0\n'
        "33 " + _v4("127.0.0.53", 53) + "\n"
        "20 clone(child_stack=NULL, flags=SIGCHLD) = 34\n"
    )
    [attempt] = check_trace.scan_file(trace)
    assert attempt.program is None and attempt.command is None
    rules = check_trace.load_rules(_PATH.parent / "allowed_attempts.toml")
    assert check_trace.apply_rules([attempt], rules)[0].allowed_by is None


@pytest.mark.parametrize(
    ("argv", "allowed"),
    [
        ('["/usr/bin/curl", "--version"]', True),
        ('["/usr/bin/curl", "https://cloud.r-project.org"]', False),
        ('["/usr/bin/curl", "--version", "--url", "https://example.org"]', False),
        ('["curl", "--version"]', False),
    ],
)
def test_shipped_rules_allow_only_renvs_curl_version_probe(tmp_path, argv, allowed):
    trace = tmp_path / "trace.txt"
    trace.write_text(
        f'9 execve("/usr/bin/curl", {argv}, 0x0 /* 3 vars */) = 0\n'
        "9 " + _v4("127.0.0.53", 53) + "\n"
    )
    rules = check_trace.load_rules(_PATH.parent / "allowed_attempts.toml")
    [attempt] = check_trace.apply_rules(check_trace.scan_file(trace), rules)
    assert (attempt.allowed_by is not None) is allowed
