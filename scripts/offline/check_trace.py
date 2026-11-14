"""Fail when an strace log shows a connection attempt that leaves the machine (#44).

The offline CI job runs every step under ``strace -f -e trace=%network,%process -o
trace.txt``. Inside a network namespace those attempts fail anyway; this check makes a
failed attempt visible instead of letting a tool swallow it and carry on.

    python scripts/offline/check_trace.py <trace files or dirs> [--json report.json]
        [--allow scripts/offline/allowed_attempts.toml]

Allowed:
- Unix sockets, netlink and ``AF_UNSPEC`` (getaddrinfo disconnecting a UDP socket).
- Loopback: ``127.0.0.0/8``, ``::1``, ``::ffff:127.x.x.x`` and the unspecified address,
  because the launcher and its children talk over loopback.

Not allowed:
- Any other IPv4 or IPv6 address.
- Port 53 on any address, loopback included: a DNS lookup (for example to the
  systemd-resolved stub at 127.0.0.53) means a tool is trying to reach the network.

Tolerated: an attempt matched by an ``--allow`` rule (a substring of the command line
that made it). Each rule names its reason and issue, and allowed attempts are still
printed and written to the JSON report.

Exit status: 0 when clean, 1 when something tried to leave, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ADDRESS_SYSCALLS = ("connect", "sendto", "sendmsg", "sendmmsg")

_PID = re.compile(r"^\s*(?:\[pid\s+(\d+)\]|(\d+))\s+")
_CALL = re.compile(r"^(?:\d+\s+)?(?P<name>\w+)\(")
_FAMILY = re.compile(r"sa_family=(?P<family>AF_\w+)")
_V4 = re.compile(r'sin_port=htons\((?P<port>\d+)\),\s*sin_addr=inet_addr\("(?P<addr>[^"]+)"\)')
_V6 = re.compile(
    r'sin6_port=htons\((?P<port>\d+)\).*?inet_pton\(AF_INET6,\s*"(?P<addr>[^"]+)"', re.DOTALL
)
_EXEC = re.compile(r'execve\("(?P<path>[^"]+)",\s*(?P<argv>\[.*?\])')
_SPAWN = re.compile(r"\b(?:clone3?|v?fork)\b.*=\s*(?P<child>\d+)\s*$")


@dataclass(frozen=True)
class Attempt:
    file: str
    pid: str | None
    syscall: str
    family: str
    address: str
    port: int
    program: str | None
    command: str | None
    reason: str
    allowed_by: str | None = None


def _is_loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def classify(line: str) -> tuple[str, str, str, int] | None:
    """Return (syscall, family, address, port) for an inet address in ``line``."""
    call = _CALL.match(line.strip())
    if call is None or call.group("name") not in ADDRESS_SYSCALLS:
        return None
    family = _FAMILY.search(line)
    if family is None:
        return None
    fam = family.group("family")
    if fam == "AF_INET":
        m = _V4.search(line)
    elif fam == "AF_INET6":
        m = _V6.search(line)
    else:
        return None
    if m is None:
        return None
    return call.group("name"), fam, m.group("addr"), int(m.group("port"))


def scan_file(path: Path) -> list[Attempt]:
    """Scan one ``strace -f`` log (``-ff`` per-process files work too).

    The program of each attempt is the last successful ``execve`` of that process, or of
    its parent when it forked without exec (trace ``%process`` to see forks).

    With ``-f`` into one file, a new child or thread can run, and its syscalls be printed,
    before the parent's ``<... clone3 resumed> = <child>`` line. Such an attempt waits in
    ``pending`` until that line names its parent; it was made before the child's own
    ``execve``, so it is the parent's program. An attempt whose process never turns up
    keeps no program, and no allow rule matches it.
    """
    attempts: list[Attempt] = []
    programs: dict[str | None, tuple[str, str]] = {}
    pending: dict[str, list[int]] = {}
    pid_from_name = path.suffix[1:] if path.suffix[1:].isdigit() else None
    for line in path.read_text("utf-8", errors="replace").splitlines():
        pid_match = _PID.match(line)
        pid = (pid_match.group(1) or pid_match.group(2)) if pid_match else pid_from_name
        body = line[pid_match.end() :] if pid_match else line
        exe = _EXEC.search(body)
        if exe and body.lstrip().startswith("execve(") and "= -1" not in body:
            programs[pid] = (exe.group("path"), exe.group("argv"))
            continue
        spawn = _SPAWN.search(body)
        if spawn and pid in programs:
            child = spawn.group("child")
            programs.setdefault(child, programs[pid])
            program, command = programs[pid]
            for i in pending.pop(child, []):
                attempts[i] = replace(attempts[i], program=program, command=command)
            continue
        found = classify(body)
        if found is None:
            continue
        syscall, family, address, port = found
        if port == 53:
            reason = "DNS lookup"
        elif not _is_loopback(address):
            reason = "non-loopback address"
        else:
            continue
        program, command = programs.get(pid, (None, None))
        if program is None and pid is not None:
            pending.setdefault(pid, []).append(len(attempts))
        attempts.append(
            Attempt(str(path), pid, syscall, family, address, port, program, command, reason)
        )
    return attempts


def load_rules(path: Path) -> list[dict[str, str]]:
    rules = tomllib.loads(path.read_text("utf-8")).get("allow", [])
    for rule in rules:
        missing = {"command_contains", "reason", "issue"} - rule.keys()
        if missing or not rule["command_contains"]:
            raise ValueError(f"{path}: every [[allow]] needs command_contains, reason, issue")
    return rules


def apply_rules(attempts: list[Attempt], rules: list[dict[str, str]]) -> list[Attempt]:
    out = []
    for a in attempts:
        rule = next((r for r in rules if r["command_contains"] in (a.command or "")), None)
        out.append(replace(a, allowed_by=rule["command_contains"]) if rule else a)
    return out


def trace_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.iterdir() if f.is_file()))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(p)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path, help="strace output files or dirs")
    parser.add_argument("--json", type=Path, help="also write the findings as JSON")
    parser.add_argument("--allow", type=Path, help="TOML file of [[allow]] rules")
    args = parser.parse_args(argv)
    try:
        files = trace_files(args.paths)
        rules = load_rules(args.allow) if args.allow else []
    except (FileNotFoundError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"check_trace: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("check_trace: no trace files found", file=sys.stderr)
        return 2
    attempts = apply_rules([a for f in files for a in scan_file(f)], rules)
    blocked = [a for a in attempts if a.allowed_by is None]
    if args.json:
        doc = {
            "files": len(files),
            "blocked": len(blocked),
            "allowed": len(attempts) - len(blocked),
            "attempts": [asdict(a) for a in attempts],
        }
        args.json.write_text(json.dumps(doc, indent=2) + "\n", "utf-8")
    for a in attempts:
        label = f"allowed by {a.allowed_by!r}" if a.allowed_by else "NOT ALLOWED"
        print(
            f"NETWORK ATTEMPT ({a.reason}, {label}): {a.syscall} {a.family}"
            f" {a.address}:{a.port} pid={a.pid} program={a.program or '?'}",
            file=sys.stderr,
        )
    print(
        f"check_trace: {len(files)} trace file(s), {len(blocked)} network attempt(s),"
        f" {len(attempts) - len(blocked)} allowed"
    )
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
