"""A stand-in for the datum 1.7.0 CLI (#83, #85), enough for the adapter and page tests.

Called as ``fake_datum.py <fake root> [global flags] <command> [args]`` through a shell
wrapper that the test runtime names as the ``datum`` executable. It mimics datum's
``--json`` output (with ``format``), exit codes (0 ok, 1 changed or failed, 2 config or
usage error), lockfile shape (``size``, ``dir_files``) and target writes for ``file``
sources; ``http`` and ``git`` sources read their bytes from ``<root>/remote.json``
({key: text}, key = URL or ``url@ref:path``) with ``${NAME}`` substituted from the
environment, like datum does.

The managed-mode flags behave like datum's, and refuse what datum refuses (exit 2 and a
single ``{"format", "error"}`` document): ``--allow-types``, ``--https-only``,
``--git-https-only``, ``--env-allow``/``--env-prefix``, ``--root``/``--target-prefix``,
``--report-only`` (check only) and ``--stage-dir``/``--lock-out`` (fetch only, together).
``fetch IDS`` substitutes ``${NAME}`` only in those datasets; ``audit``, ``undelete`` and
``unlock`` leave unset references as written.

Without ``--no-vcs`` it reconciles ignore rules like datum when its cwd is inside a Git or
SVN working copy: it runs ``git``/``svn`` from PATH. A test that puts a sentinel ``git``
on PATH therefore catches any datum run without ``--no-vcs``.

Every call is appended to ``<root>/seen.json`` (argv, cwd, environment) so tests can
assert what the adapter passed. ``<root>/mode.txt`` switches behaviour: ``echo-secret``
puts the substituted URL into each result message; ``lock-edit-skip`` makes delete,
undelete and unlock report ``skipped``.
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
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(sys.argv[1])
ARGS = sys.argv[2:]
FORMAT = "datum-report/1"
VALUE_FLAGS = (
    "--config",
    "--lock",
    "--timeout",
    "--concurrency",
    "--allow-types",
    "--root",
    "--target-prefix",
    "--env-allow",
    "--env-prefix",
    "--cache-dir",
    "--stage-dir",
    "--lock-out",
)
FLAGS: dict[str, str] = {}


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
    print(json.dumps({"format": FORMAT, **doc}, indent=2))
    sys.exit(code)


def fail(message: str) -> None:
    out({"error": message}, 2)


def split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


# --- config ----------------------------------------------------------------------------------

REF = re.compile(r"\$\$|\$\{([^}]*)\}")


def expand(value: str, lenient: bool) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group(0) == "$$":
            return "$"
        name = m.group(1)
        if name not in os.environ:
            if lenient:
                return m.group(0)
            raise KeyError(name)
        return os.environ[name]

    return REF.sub(repl, value)


def walk(v, fn):
    if isinstance(v, str):
        return fn(v)
    if isinstance(v, dict):
        return {k: walk(x, fn) for k, x in v.items()}
    if isinstance(v, list):
        return [walk(x, fn) for x in v]
    return v


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def sources(ds: dict) -> list[dict]:
    return ds.get("sources") or [ds.get("source") or {}]


def root() -> Path:
    return Path(FLAGS.get("--root", ".")).resolve()


def check_policy(data: dict, config: Path, lock: Path) -> None:
    allow = split(FLAGS.get("--allow-types"))
    env_allow = split(FLAGS.get("--env-allow"))
    env_prefix = FLAGS.get("--env-prefix", "")
    for i, ds in enumerate(data.get("datasets") or []):
        where = f"dataset {i + 1} ({ds.get('id')})"
        if env_allow or env_prefix:
            for text in strings(ds):
                for m in REF.finditer(text):
                    name = m.group(1)
                    prefixed = bool(env_prefix) and name.startswith(env_prefix)
                    if m.group(0) != "$$" and name not in env_allow and not prefixed:
                        fail(f"config error: {where}: ${{{name}}} is not allowed")
        for src in sources(ds):
            kind = src.get("type")
            if allow and kind not in allow:
                fail(f"config error: {where}: source type {kind!r} not allowed")
            url = str(src.get("url", ""))
            https = url.lower().startswith("https://")
            if kind == "http" and "--https-only" in FLAGS and not https:
                fail(f"config error: {where}: source.url must use https://")
            if kind == "git" and not https and FLAGS.keys() & {"--https-only", "--git-https-only"}:
                fail(f"config error: {where}: source.url must use https://")
        if "--root" in FLAGS:
            target = str(ds.get("target", ""))
            pure = PurePosixPath(target)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                fail(f"config error: {where}: target must be relative, without ..")
            resolved = (root() / target).resolve()
            if not resolved.is_relative_to(root()):
                fail(f"config error: {where}: target leads outside --root")
            prefix = FLAGS.get("--target-prefix")
            if prefix and not resolved.is_relative_to((root() / prefix).resolve()):
                fail(f"config error: {where}: target must be under {prefix}")
            if resolved in (config.resolve(), lock.resolve()):
                fail(f"config error: {where}: target is the config or lockfile")


def load(config: Path, lock: Path, scope) -> dict:
    data = yaml.safe_load(config.read_text("utf-8"))
    check_policy(data, config, lock)
    datasets = []
    for ds in data.get("datasets") or []:
        lenient = scope != "strict" and (scope == "lenient" or ds["id"] not in scope)
        try:
            ds = walk(ds, lambda s, lenient=lenient: expand(s, lenient))
        except KeyError as exc:
            fail(f"config error: environment variable {exc.args[0]!r} is not set")
        if "--root" in FLAGS:
            ds["target"] = str(root() / ds["target"])
            for src in sources(ds):
                if src.get("type") == "file" and not Path(src["path"]).is_absolute():
                    src["path"] = str(root() / src["path"])
        datasets.append(ds)
    data["datasets"] = datasets
    return data


def read_lock(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "items": {}}
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    data.setdefault("items", {})
    return data


def write_lock(path: Path, lock: dict) -> None:
    lock["version"] = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(lock, sort_keys=True), "utf-8")
    os.replace(tmp, path)


def reconcile_vcs() -> None:
    if "--no-vcs" in FLAGS:
        return
    cwd = Path.cwd()
    for folder in (cwd, *cwd.parents):
        for marker, tool in ((".git", "git"), (".svn", "svn")):
            if (folder / marker).exists():
                exe = shutil.which(tool)
                if exe:
                    subprocess.run([exe, "-C", str(folder), "ls-files"], check=False)  # noqa: S603


# --- sources ---------------------------------------------------------------------------------


def remote(ds: dict) -> tuple[str, dict[str, bytes] | bytes]:
    """(fingerprint, content): bytes for a single file, {relpath: bytes} for a directory."""
    src = sources(ds)[0]
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


def listing_hash(files: dict[str, bytes]) -> str:
    return sha("".join(f"{p}:{sha(d)}\n" for p, d in sorted(files.items())).encode())


def write_target(dest: Path, content, item: dict) -> dict:
    """Write ``content`` under ``dest``; update the lock item; return the staged report."""
    if isinstance(content, bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, dest)
        for key in ("dir_paths", "dir_files"):
            item.pop(key, None)
        item.update(local_sha256=sha(content), size=len(content))
        return {"kind": "file", "sha256": sha(content), "size": len(content)}
    old = set(item.get("dir_paths") or [])
    for rel, data in content.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    removed = sorted(old - set(content))
    if "--stage-dir" not in FLAGS:
        for rel in removed:
            (dest / rel).unlink(missing_ok=True)
    item.pop("size", None)
    item["dir_paths"] = sorted(content)
    item["dir_files"] = [
        {"path": p, "sha256": sha(d), "size": len(d)} for p, d in sorted(content.items())
    ]
    item["local_sha256"] = listing_hash(content)
    return {
        "kind": "dir",
        "sha256": item["local_sha256"],
        "files": item["dir_files"],
        "removed": removed,
    }


def local_hash(target: Path, item: dict | None) -> str:
    if target.is_file():
        return sha(target.read_bytes())
    if target.is_dir() and item and "dir_paths" in item:
        return listing_hash(
            {p: (target / p).read_bytes() for p in item["dir_paths"] if (target / p).is_file()}
        )
    return ""


def message_for(ds: dict) -> dict:
    if mode() == "echo-secret":
        return {"message": f"fetched from {sources(ds)[0].get('url', '')}"}
    return {}


def run(command: str, config: Path, lock_path: Path, ids: list[str]) -> None:
    report_only = "--report-only" in FLAGS
    stage, lock_out = FLAGS.get("--stage-dir"), FLAGS.get("--lock-out")
    if report_only and command != "check":
        fail("usage error: --report-only applies to check only")
    if (stage or lock_out) and command != "fetch":
        fail("usage error: --stage-dir and --lock-out apply to fetch only")
    if bool(stage) != bool(lock_out):
        fail("usage error: --stage-dir and --lock-out must be given together")
    cfg = load(config, lock_path, set(ids) if command == "fetch" and ids else "strict")
    if not stage:
        reconcile_vcs()
    lock = read_lock(lock_path)
    default_policy = (cfg.get("defaults") or {}).get("policy", "fail")
    results = []
    code = 0
    for ds in cfg.get("datasets") or []:
        did = ds["id"]
        if command == "fetch" and ids and did not in ids:
            continue
        item = dict(lock["items"].get(did) or {})
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
        would_update = policy == "update" and (stale or not target.exists())
        if command == "check" and would_update and report_only:
            what = "target missing" if not target.exists() else "remote changed"
            res.update(status="stale", message=f"{what}; would update (--report-only)")
            code = 1
            results.append(res)
            continue
        if command == "fetch" or (policy == "update" and (stale or not target.exists())):
            rel = target.relative_to(root()).as_posix() if "--root" in FLAGS else str(target)
            dest = Path(stage) / rel if stage else target
            report = write_target(dest, content, item)
            item.update(remote_fingerprint=fp, checked_at=now())
            lock["items"][did] = item
            res["status"] = "fetched" if command == "fetch" else "updated"
            if stage:
                res["staged"] = {"target": rel, "path": str(dest), **report}
            code = max(code, 1 if command == "check" else 0)
        elif stale:
            res["status"] = "fail" if policy == "fail" else "warn"
            code = 1
        else:
            res["status"] = "ok"
        results.append(res)
    if not report_only:
        lock["last_checked"] = now()
        write_lock(Path(lock_out) if lock_out else lock_path, lock)
    out({"results": results}, code)


def drift(target: Path, item: dict) -> tuple[str, list[dict] | None]:
    if not target.exists():
        return "missing", None
    if item.get("dir_files"):
        files, overall = [], "ok"
        for f in item["dir_files"]:
            path = target / f["path"]
            d = "missing" if not path.is_file() else "ok"
            if d == "ok" and sha(path.read_bytes()) != f["sha256"]:
                d = "changed"
            if d != "ok":
                overall = "changed"
            files.append({**f, "drift": d})
        return overall, files
    if not item.get("local_sha256"):
        return "unknown", None
    return ("ok" if local_hash(target, item) == item["local_sha256"] else "changed"), None


def audit(config: Path, lock_path: Path) -> None:
    cfg = load(config, lock_path, "lenient")
    lock = read_lock(lock_path)
    entries = []
    ids = []
    for ds in cfg.get("datasets") or []:
        ids.append(ds["id"])
        item = lock["items"].get(ds["id"])
        status = "pending" if item is None else ("deleted" if item.get("deleted") else "ok")
        entry = {
            "id": ds["id"],
            "status": status,
            "in_config": True,
            "policy": ds.get("policy") or "fail",
            "target": ds["target"],
            **({"remote_fingerprint": item.get("remote_fingerprint")} if item else {}),
        }
        if "--verify-local" in FLAGS and item and status == "ok":
            entry["drift"], files = drift(Path(ds["target"]), item)
            if files is not None:
                entry["files"] = files
        entries.append(entry)
    for did in sorted(set(lock["items"]) - set(ids)):
        entries.append({"id": did, "status": "orphaned", "in_config": False})
    out({"entries": entries})


def lock_command(command: str, config: Path, lock_path: Path, ids: list[str]) -> None:
    if command in ("delete", "unlock") and "--yes" not in FLAGS:
        fail(f"usage error: {command} with --json needs --yes")
    lock = read_lock(lock_path)
    cfg = load(config, lock_path, set(ids) if command == "delete" else "lenient")
    targets = {d["id"]: Path(d["target"]) for d in cfg.get("datasets") or []}
    done = {"delete": "deleted", "undelete": "undeleted", "unlock": "unlocked"}[command]
    results, code = [], 0
    for did in ids:
        item = lock["items"].get(did)
        if mode() == "lock-edit-skip":
            results.append({"id": did, "status": "skipped", "message": "nothing to do"})
            continue
        if command == "delete":
            if did not in targets:
                results.append({"id": did, "status": "error", "message": "unknown dataset id"})
                code = 1
                continue
            item = item or {}
            if targets[did].is_file():
                targets[did].unlink()
            item.update(deleted=True, deleted_at=now())
            lock["items"][did] = item
        elif command == "undelete":
            if not item or not item.get("deleted"):
                results.append({"id": did, "status": "skipped", "message": "not deleted"})
                continue
            item.pop("deleted", None)
            item.pop("deleted_at", None)
        elif command == "unlock":
            if item is None:
                results.append({"id": did, "status": "skipped", "message": "not in the lock"})
                continue
            del lock["items"][did]
        results.append({"id": did, "status": done})
    write_lock(lock_path, lock)
    out({"results": results}, code)


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
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        FLAGS[flag] = args.pop(0) if flag in VALUE_FLAGS else "1"
    if "--version" in FLAGS:
        if "--json" in FLAGS:
            out({"version": "1.7.0", "features": ["git"]})
        print("datum v1.7.0")
        sys.exit(0)
    command, rest = args[0], args[1:]
    config = Path(FLAGS.get("--config", ".data.yaml"))
    lock_path = Path(FLAGS.get("--lock", ".data.lock.yaml"))
    if command == "schema":
        print(json.dumps(SCHEMA, indent=2))
        sys.exit(0)
    if command == "types":
        out({"types": [t for t in TYPES if not rest or t["type"] in rest]})
    if command in ("check", "fetch"):
        run(command, config, lock_path, rest)
    if command == "audit":
        audit(config, lock_path)
    if command in ("delete", "undelete", "unlock"):
        lock_command(command, config, lock_path, rest)
    fail(f"unknown command {command}")


if __name__ == "__main__":
    main()
