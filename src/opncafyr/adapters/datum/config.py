"""``.data.yaml`` under the managed-mode policy (#83, ADR 0012).

Before any datum process starts, the config is checked against datum's own schema
(``datum schema``) and these rules. A problem names the dataset and the field, never a
value.

- **Source types.** ``http``, ``file`` and ``git`` only. ``command`` runs shell commands
  from the YAML, and a workspace file may never add a command.
- **Targets.** Relative, ``/``-separated, no ``..`` and no hidden parts, under
  ``source-data/`` (D1), and still inside it after following symlinks
  (``core.workspace.resolve_in_root``). No ``${NAME}`` in a target.
- **URLs.** ``https://`` only (D4), no user name or password, and no ``${NAME}`` in the
  scheme or host. ``git@``, ``ssh://`` and ``git://`` are refused (no SSH keys or agent).
- **File sources.** A path inside the study must stay out of ``.opncafyr/`` and the datum
  files. A path outside the study needs a per-machine approval (D3, ``approvals``).
- **${NAME} references** may name only credentials the user enters for the session; a
  name must be valid and must not be one the launcher sets itself.
- **ignore** is treated as false: the launcher never lets datum touch ``.gitignore`` or
  ``svn:ignore``.

Edits are structural (D6 b): the file is parsed, changed and written again, so comments
are lost; ``has_comments`` lets the page warn before that happens.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from opncafyr.adapters.datum import CONFIG_FILE, LOCK_FILE, SOURCE_DATA
from opncafyr.core.manifest import atomic_write_text
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root
from opncafyr.paths import WORKSPACE_META_DIR

MAX_CONFIG_BYTES = 1024 * 1024
ALLOWED_TYPES = ("http", "file", "git")
POLICIES = ("fail", "update", "log")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_REF_RE = re.compile(r"\$\$|\$\{([^}]*)\}")
# Variables the launcher sets or that change how datum or Git behave: never credentials.
RESERVED_NAMES = re.compile(
    r"^(PATH|HOME|USER|LOGNAME|SHELL|LANG|TZ|LC_.*|TMP|TEMP|TMPDIR|XDG_.*|NO_COLOR"
    r"|HTTPS?_PROXY|NO_PROXY|ALL_PROXY|SSL_.*|SSH_.*|GIT_SSH.*|GIT_CONFIG.*|GIT_DIR"
    r"|GIT_EXEC_PATH|DATUM_.*|OPNCAFYR_.*|SYSTEMROOT|COMSPEC|PATHEXT|WINDIR"
    r"|APPDATA|LOCALAPPDATA|USERPROFILE)$",
    re.IGNORECASE,
)
SCAFFOLD = "version: 1\ndatasets: []\n"


class ConfigError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Problem:
    dataset: str | None
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }

    def text(self) -> str:
        where = f"Dataset {self.dataset!r}, {self.field}" if self.dataset else self.field
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class OutsideFile:
    """A ``file`` source outside the study; it needs a per-machine approval (D3)."""

    dataset: str
    path: str  # absolute, as written in the config


@dataclass
class Checked:
    problems: list[Problem] = field(default_factory=list)
    credentials: list[str] = field(default_factory=list)  # ${NAME}s, sorted
    outside: list[OutsideFile] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Loaded:
    text: str
    sha256: str
    data: dict[str, Any]

    @property
    def datasets(self) -> list[dict[str, Any]]:
        items = self.data.get("datasets")
        return [d for d in items if isinstance(d, dict)] if isinstance(items, list) else []


# --- reading -----------------------------------------------------------------------------


def config_path(study: Path) -> Path:
    return study / CONFIG_FILE


def exists(study: Path) -> bool:
    return config_path(study).is_file()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ConfigError("bad_yaml", f"{CONFIG_FILE} is not valid YAML.") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("bad_yaml", f"{CONFIG_FILE} must be a mapping at the top level.")
    return data


def load(study: Path) -> Loaded:
    path = config_path(study)
    if path.is_symlink() or not path.is_file():
        raise ConfigError("no_config", f"This study has no {CONFIG_FILE}.", 404)
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ConfigError("too_large", f"{CONFIG_FILE} is larger than 1 MB.")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("bad_yaml", f"{CONFIG_FILE} is not UTF-8 text.") from None
    return Loaded(text, hashlib.sha256(raw).hexdigest(), parse(text))


def has_comments(text: str) -> bool:
    """Whether a structural rewrite would lose comments (a ``#`` starting a comment)."""
    return any(re.search(r"(^|\s)#", line) for line in text.splitlines())


# --- checking ----------------------------------------------------------------------------


def _sources(ds: dict[str, Any]) -> list[tuple[str, Any]]:
    if "sources" in ds and isinstance(ds["sources"], list):
        return [(f"sources[{i}]", s) for i, s in enumerate(ds["sources"])]
    return [("source", ds.get("source"))]


def _refs(value: str) -> list[str]:
    return [m.group(1) for m in _REF_RE.finditer(value) if m.group(0) != "$$"]


def _strings(value: Any, where: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(where, value)]
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += _strings(v, f"{where}.{k}" if where else str(k))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out += _strings(v, f"{where}[{i}]")
        return out
    return []


def _url_problem(url: Any, kind: str) -> tuple[str, str] | None:
    if not isinstance(url, str) or not url:
        return "missing", "needs a URL."
    lower = url.lower()
    if kind == "git" and (lower.startswith(("git@", "ssh://", "git://")) or "://" not in url):
        return "ssh", "Git sources must use https://. SSH and git:// URLs are not allowed."
    if lower.startswith("http://"):
        return "not_https", "must start with https://. Plain http:// is not allowed."
    if not lower.startswith("https://"):
        return "not_https", "must start with https://."
    authority = re.split(r"[/?#]", url[len("https://") :], maxsplit=1)[0]
    if "@" in authority:
        return "credentials", (
            "must not contain a user name or password. Use a ${NAME} credential in a "
            "header or the query instead."
        )
    if not authority or "$" in authority:
        return "host", "must name its host directly, not through a ${NAME} reference."
    return None


def _target_problem(study: Path, target: Any) -> tuple[str, str] | None:
    if not isinstance(target, str) or not target.strip():
        return "missing", "needs a target path."
    if "$" in target:
        return "reference", "must not contain a ${NAME} reference."
    if "\\" in target or ":" in target:
        return "separator", "must use / between folders (for example source-data/pk.csv)."
    pure = PurePosixPath(target)
    if pure.is_absolute():
        return "absolute", f"must be a relative path under {SOURCE_DATA}/."
    parts = pure.parts
    if ".." in parts:
        return "parent", "must not contain '..'."
    if any(p.startswith(".") for p in parts):
        return "hidden", "must not contain hidden folders or files (names starting with '.')."
    if len(parts) < 2 or parts[0] != SOURCE_DATA:
        return "outside", f"must be inside {SOURCE_DATA}/ (for example {SOURCE_DATA}/pk.csv)."
    try:
        resolved = resolve_in_root(study, target)
        source = resolve_in_root(study, SOURCE_DATA)
    except WorkspaceAccessError:
        return "escape", "leads outside the study folder (through a link)."
    if not resolved.is_relative_to(source) or resolved == source:
        return "escape", f"leads outside {SOURCE_DATA}/ (through a link)."
    return None


def _file_source_problem(study: Path, path: Any) -> tuple[str, str] | None | OutsideFile:
    if not isinstance(path, str) or not path.strip():
        return "missing", "needs a path."
    if "$" in path:
        return "reference", "must not contain a ${NAME} reference."
    if Path(path).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", path) or path.startswith("\\\\"):
        root = study.resolve()
        try:
            resolved = Path(path).resolve()
        except OSError:
            resolved = Path(path)
        if not resolved.is_relative_to(root):
            return OutsideFile("", path)
        rel = resolved.relative_to(root).as_posix()
    else:
        try:
            rel = resolve_in_root(study, path).relative_to(study.resolve()).as_posix()
        except WorkspaceAccessError:
            return "escape", "leads outside the study folder. Use an absolute path instead."
    first = rel.split("/", 1)[0]
    if first == WORKSPACE_META_DIR or rel in (CONFIG_FILE, LOCK_FILE) or rel == ".":
        return "app_files", "must not point at the app's own files or the datum files."
    return None


def check(data: dict[str, Any], study: Path, schema: dict[str, Any] | None = None) -> Checked:
    """Every policy problem in ``data``, plus the credentials and approvals it needs."""
    out = Checked()
    names: set[str] = set()

    def add(dataset: str | None, where: str, code: str, message: str) -> None:
        out.problems.append(Problem(dataset, where, code, message))

    if schema is not None:
        _schema_problems(data, schema, add)
    datasets = data.get("datasets")
    if not isinstance(datasets, list):
        add(None, "datasets", "missing", "needs a list of datasets.")
        datasets = []
    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        policy = defaults.get("policy")
        if policy is not None and policy not in POLICIES:
            add(None, "defaults.policy", "policy", "must be fail, update or log.")
    seen: set[str] = set()
    for i, ds in enumerate(datasets):
        if not isinstance(ds, dict):
            add(None, f"datasets[{i}]", "invalid", "must be a mapping.")
            continue
        did = ds.get("id")
        if not isinstance(did, str) or not ID_RE.match(did):
            add(None, f"datasets[{i}].id", "id", "must be letters, digits, '-' or '_'.")
            continue
        if did in seen:
            add(did, "id", "duplicate", "is used by more than one dataset.")
        seen.add(did)
        if ds.get("policy") is not None and ds.get("policy") not in POLICIES:
            add(did, "policy", "policy", "must be fail, update or log.")
        problem = _target_problem(study, ds.get("target"))
        if problem:
            add(did, "target", *problem)
        for key in ("id", "desc"):
            if isinstance(ds.get(key), str) and "${" in ds[key]:
                add(did, key, "reference", "must not contain a ${NAME} reference.")
        for where, source in _sources(ds):
            if not isinstance(source, dict):
                add(did, where, "missing", "needs a source.")
                continue
            kind = source.get("type")
            if kind == "command":
                add(
                    did,
                    f"{where}.type",
                    "command",
                    "command sources run shell commands from the config and are not "
                    "allowed in the app. Use an https, file or git source.",
                )
                continue
            if kind not in ALLOWED_TYPES:
                add(did, f"{where}.type", "type", "must be http, file or git.")
                continue
            if kind in ("http", "git"):
                problem = _url_problem(source.get("url"), kind)
                if problem:
                    add(did, f"{where}.url", *problem)
            if kind == "file":
                result = _file_source_problem(study, source.get("path"))
                if isinstance(result, OutsideFile):
                    out.outside.append(OutsideFile(did, result.path))
                elif result:
                    add(did, f"{where}.path", *result)
            for spot, text in _strings(source, where):
                for name in _refs(text):
                    if not NAME_RE.match(name):
                        add(did, spot, "reference", "has an invalid ${NAME} reference.")
                    elif RESERVED_NAMES.match(name):
                        add(
                            did,
                            spot,
                            "reserved",
                            f"${{{name}}} is set by the app and can't be a credential.",
                        )
                    else:
                        names.add(name)
        for spot, text in _strings(
            {k: v for k, v in ds.items() if k not in ("source", "sources")}, ""
        ):
            if spot not in ("target", "id", "desc") and _refs(text):
                add(did, spot, "reference", "must not contain a ${NAME} reference.")
    out.credentials = sorted(names)
    return out


def _schema_problems(data: Any, schema: dict[str, Any], add: Any) -> None:
    from jsonschema import Draft7Validator

    datasets = data.get("datasets") if isinstance(data, dict) else None
    for error in Draft7Validator(schema).iter_errors(data):
        path = list(error.absolute_path)
        dataset = None
        if len(path) >= 2 and path[0] == "datasets" and isinstance(path[1], int):
            item = datasets[path[1]] if isinstance(datasets, list) else None
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                dataset = item["id"]
                path = path[2:]
        where = ".".join(str(p) for p in path) or "(top level)"
        # The rule only: jsonschema's own message quotes the value.
        add(dataset, where, "schema", f"does not match datum's config schema ({error.validator}).")


# --- the snapshot copy ---------------------------------------------------------------------


def managed_copy(data: dict[str, Any]) -> dict[str, Any]:
    """A deep copy with ``ignore`` false everywhere (the snapshot never touches VCS files)."""
    copy_ = copy.deepcopy(data)
    defaults = copy_.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    defaults["ignore"] = False
    copy_["defaults"] = defaults
    for ds in copy_.get("datasets") or []:
        if isinstance(ds, dict):
            ds["ignore"] = False
    return copy_


def dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


# --- editing -------------------------------------------------------------------------------


def scaffold(study: Path) -> None:
    """Write the minimal ``.data.yaml``; never overwrites (and never runs ``datum init``)."""
    path = config_path(study)
    if path.exists() or path.is_symlink():
        raise ConfigError("exists", f"This study already has a {CONFIG_FILE}.", 409)
    atomic_write_text(path, SCAFFOLD)


def dataset_from_form(form: dict[str, Any]) -> dict[str, Any]:
    """A dataset entry from the page's form, keeping only fields datum knows."""
    source = {k: v for k, v in (form.get("source") or {}).items() if v not in (None, "", {})}
    entry: dict[str, Any] = {
        "id": form.get("id"),
        "desc": form.get("desc") or "",
        "source": source,
        "target": form.get("target"),
    }
    if form.get("policy"):
        entry["policy"] = form["policy"]
    return entry


def with_dataset(
    data: dict[str, Any], entry: dict[str, Any], replace: str | None
) -> dict[str, Any]:
    new = copy.deepcopy(data)
    datasets = new.get("datasets")
    if not isinstance(datasets, list):
        datasets = []
    ids = [d.get("id") for d in datasets if isinstance(d, dict)]
    if replace is None:
        if entry["id"] in ids:
            raise ConfigError("duplicate", "A dataset with this id already exists.", 409)
        datasets.append(entry)
    else:
        if replace not in ids:
            raise ConfigError("not_found", "No dataset with this id.", 404)
        if entry["id"] != replace and entry["id"] in ids:
            raise ConfigError("duplicate", "A dataset with this id already exists.", 409)
        index = ids.index(replace)
        old = datasets[index]
        # Keep what the form doesn't edit (sources lists, ignore, unknown keys).
        merged = {k: v for k, v in old.items() if k not in ("source", "sources", "policy")}
        merged.update(entry)
        datasets[index] = merged
    new["datasets"] = datasets
    new.setdefault("version", 1)
    return new


def without_dataset(data: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    new = copy.deepcopy(data)
    datasets = new.get("datasets") if isinstance(new.get("datasets"), list) else []
    kept = [d for d in datasets if not (isinstance(d, dict) and d.get("id") == dataset_id)]
    if len(kept) == len(datasets):
        raise ConfigError("not_found", "No dataset with this id.", 404)
    new["datasets"] = kept
    return new


def save(study: Path, data: dict[str, Any], expected_sha256: str) -> Loaded:
    """Write ``data`` if the file still has ``expected_sha256`` (nobody else changed it)."""
    current = load(study)
    if current.sha256 != expected_sha256:
        raise ConfigError(
            "changed",
            f"{CONFIG_FILE} changed since it was loaded. Reload the page and try again.",
            409,
        )
    atomic_write_text(config_path(study), dump(data))
    return load(study)
