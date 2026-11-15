"""A stand-in for the datum 1.6.0 CLI (#83), enough for the adapter and page tests.

Called as ``fake_datum.py <fake root> [global flags] <command> [args]`` through a shell
wrapper that the test runtime names as the ``datum`` executable. It mimics datum's
``--json`` output, exit codes (0 ok, 1 changed or failed, 2 config error), lockfile shape
and target writes for ``file`` sources; ``http`` and ``git`` sources read their bytes
from ``<root>/remote.json`` ({key: text}, key = URL or ``url@ref:path``) with ``${NAME}``
substituted from the environment, like datum does.

Like datum, it reconciles ignore rules when its cwd is inside a Git or SVN working copy:
it runs ``git``/``svn`` from PATH. A test that puts a sentinel ``git`` on PATH therefore
catches any datum run that is not isolated from the study.

Every call is appended to ``<root>/seen.json`` (argv, cwd, environment) so tests can
assert what the adapter passed. ``<root>/mode.txt`` switches behaviour:
``echo-secret`` puts the substituted URL into each result message.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(sys.argv[1])
ARGS = sys.argv[2:]


def record() -> None:
    seen = ROOT / "seen.json"
    calls = json.loads(seen.read_text("utf-8")) if seen.is_file() else []
    calls.append({"argv": ARGS, "cwd": os.getcwd(), "env": dict(os.environ)})
    seen.write_text(json.dumps(calls, indent=1), "utf-8")


def mode() -> str:
    path = ROOT / "mode.txt"
    return path.read_text("utf-8").strip() if path.is_file() else ""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def out(doc: dict, code: int = 0) -> None:
    print(json.dumps(doc, indent=2))
    sys.exit(code)


def expand(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(0) == "$$":
            return "$"
        name = m.group(1)
        if name not in os.environ:
            raise KeyError(name)
        return os.environ[name]

    return re.sub(r"\$\$|\$\{([^}]*)\}", repl, value)


def load(config: Path) -> dict:
    data = yaml.safe_load(config.read_text("utf-8"))

    def walk(v):
        if isinstance(v, str):
            return expand(v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    try:
        return walk(data)
    except KeyError as exc:
        out({"error": f"config error: environment variable {exc.args[0]!r} is not set"}, 2)
        raise


def read_lock(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "items": {}}
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    data.setdefault("items", {})
    return data


def write_lock(path: Path, lock: dict) -> None:
    lock["version"] = 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(lock, sort_keys=True), "utf-8")
    os.replace(tmp, path)


def reconcile_vcs() -> None:
    cwd = Path.cwd()
    for folder in (cwd, *cwd.parents):
        for marker, tool in ((".git", "git"), (".svn", "svn")):
            if (folder / marker).exists():
                exe = shutil.which(tool)
                if exe:
                    subprocess.run([exe, "-C", str(folder), "ls-files"], check=False)  # noqa: S603


# --- sources ---------------------------------------------------------------------------------


def source_of(ds: dict) -> dict:
    return ds.get("source") or (ds.get("sources") or [{}])[0]


def remote(ds: dict) -> tuple[str, dict[str, bytes] | bytes]:
    """(fingerprint, content): bytes for a single file, {relpath: bytes} for a directory."""
    src = source_of(ds)
    kind = src.get("type")
    if kind == "file":
        path = Path(src["path"])
        if path.is_dir():
            files = {
                p.relative_to(path).as_posix(): p.read_bytes()
                for p in sorted(path.rglob("*"))
                if p.is_file()
            }
            listing = "".join(f"{k}:{sha(v)}\n" for k, v in sorted(files.items()))
            return "dirsha256:" + sha(listing.encode()), files
        if not path.is_file():
            raise FileNotFoundError(f"open {path}: no such file or directory")
        data = path.read_bytes()
        return "sha256:" + sha(data), data
    table = json.loads((ROOT / "remote.json").read_text("utf-8"))
    key = src["url"] if kind == "http" else f"{src['url']}@{src['ref']}:{src['path']}"
    if key not in table:
        raise ConnectionError(f"GET {src['url']}: 404 Not Found")
    data = table[key].encode("utf-8")
    prefix = "etag:" if kind == "http" else "git:"
    return prefix + sha(data)[:20], data


def local_hash(target: Path, item: dict | None) -> str:
    if target.is_file():
        return sha(target.read_bytes())
    if target.is_dir() and item and "dir_paths" in item:
        listing = "".join(
            f"{p}:{sha((target / p).read_bytes())}\n"
            for p in sorted(item["dir_paths"])
            if (target / p).is_file()
        )
        return sha(listing.encode())
    return ""


def write_target(target: Path, content, item: dict) -> None:
    if isinstance(content, bytes):
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)
        item.pop("dir_paths", None)
        return
    old = set(item.get("dir_paths") or [])
    for rel, data in content.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    for rel in old - set(content):
        (target / rel).unlink(missing_ok=True)
    item["dir_paths"] = sorted(content)


def message_for(ds: dict) -> dict:
    if mode() == "echo-secret":
        return {"message": f"fetched from {source_of(ds).get('url', '')}"}
    return {}


def run(command: str, config: Path, lock_path: Path, ids: list[str]) -> None:
    cfg = load(config)
    reconcile_vcs()
    lock = read_lock(lock_path)
    default_policy = (cfg.get("defaults") or {}).get("policy", "fail")
    results = []
    code = 0
    for ds in cfg.get("datasets") or []:
        did = ds["id"]
        if command == "fetch" and ids and did not in ids:
            continue
        item = lock["items"].get(did) or {}
        if item.get("deleted"):
            results.append({"id": did, "status": "deleted"})
            continue
        target = Path(ds["target"])
        try:
            fp, content = remote(ds)
        except (OSError, ConnectionError) as exc:
            results.append({"id": did, "status": "error", "message": str(exc), **message_for(ds)})
            code = 1
            continue
        policy = ds.get("policy") or default_policy
        stale = item.get("remote_fingerprint") != fp
        res = {"id": did, "remote_fingerprint": fp, **message_for(ds)}
        if item.get("remote_fingerprint"):
            res["lock_fingerprint"] = item["remote_fingerprint"]
        if command == "fetch" or (policy == "update" and (stale or not target.exists())):
            write_target(target, content, item)
            item.update(
                remote_fingerprint=fp,
                local_sha256=local_hash(target, item),
                checked_at=now(),
            )
            lock["items"][did] = item
            res["status"] = "fetched" if command == "fetch" else "updated"
            code = max(code, 1 if command == "check" else 0)
        elif stale:
            res["status"] = "fail" if policy == "fail" else "warn"
            code = 1
        else:
            res["status"] = "ok"
        results.append(res)
    lock["last_checked"] = now()
    write_lock(lock_path, lock)
    out({"results": results}, code)


def audit(config: Path, lock_path: Path) -> None:
    cfg = load(config)
    lock = read_lock(lock_path)
    entries = []
    ids = []
    for ds in cfg.get("datasets") or []:
        ids.append(ds["id"])
        item = lock["items"].get(ds["id"])
        status = "pending" if item is None else ("deleted" if item.get("deleted") else "ok")
        entries.append(
            {
                "id": ds["id"],
                "status": status,
                "in_config": True,
                "policy": ds.get("policy") or "fail",
                "target": ds["target"],
                **({"remote_fingerprint": item.get("remote_fingerprint")} if item else {}),
            }
        )
    for did in sorted(set(lock["items"]) - set(ids)):
        entries.append({"id": did, "status": "orphaned", "in_config": False})
    out({"entries": entries})


def lock_command(command: str, config: Path, lock_path: Path, ids: list[str]) -> None:
    lock = read_lock(lock_path)
    cfg = load(config) if command == "delete" else {}
    targets = {d["id"]: Path(d["target"]) for d in cfg.get("datasets") or []}
    for did in ids:
        item = lock["items"].get(did)
        if command == "delete":
            if did not in targets:
                print(f"unknown dataset id: {did}")
                sys.exit(2)
            item = item or {}
            target = targets[did]
            if target.is_file():
                target.unlink()
            item.update(deleted=True, deleted_at=now())
            lock["items"][did] = item
        elif command == "undelete":
            if not item or not item.get("deleted"):
                sys.exit(1)
            item.pop("deleted", None)
            item.pop("deleted_at", None)
        elif command == "unlock":
            if item is None:
                sys.exit(1)
            del lock["items"][did]
    write_lock(lock_path, lock)
    sys.exit(0)


SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["version", "datasets"],
    "properties": {
        "version": {"type": "integer", "enum": [1]},
        "datasets": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "desc", "target"]},
        },
    },
}

TYPES = [
    {"type": t, "description": f"{t} source", "fields": [{"name": "type", "required": True}]}
    for t in ("command", "file", "git", "http")
]


def main() -> None:
    record()
    args = list(ARGS)
    flags: dict[str, str] = {}
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag in ("--config", "--lock", "--timeout", "--concurrency"):
            flags[flag] = args.pop(0)
        else:
            flags[flag] = "1"
    if "--version" in flags:
        print("datum v1.6.0")
        sys.exit(0)
    command, rest = args[0], args[1:]
    config = Path(flags.get("--config", ".data.yaml"))
    lock_path = Path(flags.get("--lock", ".data.lock.yaml"))
    if command == "schema":
        out(SCHEMA)
    if command == "types":
        out({"types": [t for t in TYPES if not rest or t["type"] in rest]})
    if command in ("check", "fetch"):
        run(command, config, lock_path, rest)
    if command == "audit":
        audit(config, lock_path)
    if command in ("delete", "undelete", "unlock"):
        lock_command(command, config, lock_path, rest)
    out({"error": f"unknown command {command}"}, 2)


if __name__ == "__main__":
    main()
