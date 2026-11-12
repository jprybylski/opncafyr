"""Study workflow profiles (#68, ADR 0010): a study team's journey through the NCA steps.

A profile is a YAML file the team writes in the study's ``profiles/`` folder (examples ship
read-only in ``profiles/`` beside this module and are copied in to be used). It lists
steps of six types:

- ``instruction`` and ``checkpoint``: text the user reads and confirms;
- ``checklist``: items the user ticks;
- ``action``: a pointer to a control on the Analysis or Publish page (a guidance target),
  with an optional condition shown as a hint;
- ``run``: a slot the user fills with one **completed** run they choose;
- ``publication``: one recorded publication the user chooses, whose runs must be exactly
  the runs in the named slots.

A profile is guidance. It holds no analyte value, dose, unit, BLQ rule, exclusion or
comparison, never starts or changes a run, and never picks anything: every slot and
publication is the user's explicit choice, checked here.

Progress is kept per profile file in ``.opncafyr/study-profile.json``
(``study-profile-state-v1``) with the SHA-256 of the text followed. When the file's text
changes, progress can't change until the user adopts the new text; progress for steps that
still exist is kept. A run or publication records a profile step (``workflow_context``)
only when the user chose that step while making it; a sealed record is never changed.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from opncafyr.core import runs as runs_core
from opncafyr.core.guidance import (
    Catalog,
    list_study_files,
    parse_guidance,
    read_study_file,
    sha256_bytes,
    shipped_files,
)
from opncafyr.core.manifest import atomic_write_bytes, atomic_write_json, validate
from opncafyr.core.workspace import WORKSPACE_META_DIR, resolve_in_root

PROFILES_DIR = "profiles"
STATE_FILE = f"{WORKSPACE_META_DIR}/study-profile.json"
SCHEMA = "study-profile-v1"
STATE_SCHEMA = "study-profile-state-v1"
EXAMPLES = ("opncafyr.workflows.nca", "profiles")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# What each step type takes besides id, type and title. Required fields are also in the
# schema; this adds the fields a type must not have, with a message a person can act on.
FIELDS = {
    "instruction": {"text"},
    "checklist": {"text", "items"},
    "action": {"text", "target", "hint_condition"},
    "run": {"text", "slot", "suggested_label"},
    "checkpoint": {"text"},
    "publication": {"text", "slots"},
}
COMMON = {"id", "type", "title"}

_lock = threading.Lock()


class ProfileError(Exception):
    """A request about profiles that can't be done. ``code`` maps to an HTTP status."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# --- reading and checking a profile ------------------------------------------------------


def check_rules(doc: dict[str, Any], catalog: Catalog) -> list[str]:
    """Rules the schema can't say: fields per type, unique ids, slots, catalog ids.

    Problems name the step by position and id and the field by name. Field names come from
    the schema's closed list and ids match a strict pattern, so neither carries data.
    """
    problems: list[str] = []
    seen: set[str] = set()
    slots: dict[str, int] = {}
    refs: list[tuple[str, str, str]] = []
    for n, step in enumerate(doc["steps"]):
        where = f"steps/{n} ({step['id']})"
        if step["id"] in seen:
            problems.append(f"{where}: another step has the same id")
        seen.add(step["id"])
        extra = sorted(set(step) - COMMON - FIELDS[step["type"]])
        if extra:
            problems.append(f"{where}: a {step['type']} step doesn't take {', '.join(extra)}")
        if step["type"] == "checklist":
            ids = [i["id"] for i in step["items"]]
            if len(set(ids)) != len(ids):
                problems.append(f"{where}: two checklist items have the same id")
        if step["type"] == "run":
            if step["slot"] in slots:
                problems.append(f"{where}: slot {step['slot']} is already used by another step")
            slots[step["slot"]] = n
        if "target" in step:
            refs.append((f"{where}/target", "target", step["target"]))
        if "hint_condition" in step:
            refs.append((f"{where}/hint_condition", "condition", step["hint_condition"]))
    for n, step in enumerate(doc["steps"]):
        if step["type"] != "publication":
            continue
        for slot in step["slots"]:
            if slot not in slots:
                problems.append(f"steps/{n} ({step['id']}): no run step has slot {slot}")
            elif slots[slot] > n:
                problems.append(f"steps/{n} ({step['id']}): slot {slot} comes after this step")
    return problems + catalog.unknown(refs)


def examine(data: bytes, label: str, catalog: Catalog) -> dict[str, Any]:
    """A profile file as the API lists it: what it is, its hash and its problems."""
    doc, problems = parse_guidance(data, SCHEMA, label)
    if doc is not None:
        problems = check_rules(doc, catalog)
    out: dict[str, Any] = {"sha256": sha256_bytes(data), "problems": problems}
    if doc is not None:
        out.update(id=doc["id"], version=doc["version"], title=doc["title"])
        out["description"] = doc.get("description")
        out["profile"] = doc
    return out


def _study_profile(root: Path, name: str, catalog: Catalog) -> tuple[dict[str, Any], bytes]:
    try:
        data = read_study_file(root, PROFILES_DIR, name)
    except FileNotFoundError:
        raise ProfileError("not_found", "There is no such profile file in profiles/.") from None
    except ValueError as exc:
        raise ProfileError("profile_invalid", str(exc)) from None
    return examine(data, f"{PROFILES_DIR}/{name}", catalog), data


def listing(root: Path | None, catalog: Catalog) -> dict[str, Any]:
    """The shipped examples and, with a study open, the study's own profile files."""
    examples = [
        {"name": name, **_public(examine(data, name, catalog))}
        for name, data in shipped_files(EXAMPLES).items()
    ]
    study: list[dict[str, Any]] = []
    if root is not None:
        for name in list_study_files(root, PROFILES_DIR):
            try:
                found, _ = _study_profile(root, name, catalog)
            except ProfileError as exc:
                found = {"problems": [exc.message]}
            study.append({"name": name, **_public(found)})
    return {"examples": examples, "study": study}


def _public(found: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in found.items() if k != "profile"}


def copy_example(root: Path, name: str) -> str:
    """Copy a shipped example into the study's ``profiles/``. Never overwrites a file."""
    examples = shipped_files(EXAMPLES)
    if name not in examples:
        raise ProfileError("not_found", "There is no such example profile.")
    target = resolve_in_root(root, f"{PROFILES_DIR}/{name}")
    with _lock:
        if target.exists():
            raise ProfileError(
                "exists",
                f"{PROFILES_DIR}/{name} already exists. It was not replaced; edit it, or "
                "rename it and copy the example again.",
            )
        atomic_write_bytes(target, examples[name])
    return f"{PROFILES_DIR}/{name}"


# --- progress ----------------------------------------------------------------------------


def read_state(root: Path) -> dict[str, Any]:
    from jsonschema import ValidationError

    path = resolve_in_root(root, STATE_FILE)
    if not path.is_file():
        return {"schema_version": "1.0", "selected": None, "profiles": {}}
    try:
        doc = json.loads(path.read_text("utf-8"))
        validate(doc, STATE_SCHEMA)
    except (OSError, ValueError, ValidationError) as exc:
        raise ProfileError(
            "state_invalid",
            f"{STATE_FILE} is not valid, so profile progress can't be read or changed. "
            "Restore it from a backup or remove it to start the progress again.",
        ) from exc
    return doc


def _write_state(root: Path, doc: dict[str, Any]) -> None:
    atomic_write_json(resolve_in_root(root, STATE_FILE), doc, STATE_SCHEMA)


def select(root: Path, name: str, catalog: Catalog) -> dict[str, Any]:
    """Follow ``profiles/<name>``. Progress made earlier with the same text is kept."""
    with _lock:
        found, _ = _study_profile(root, name, catalog)
        if found["problems"]:
            raise ProfileError(
                "profile_invalid", "Fix this profile first.", problems=found["problems"]
            )
        state = read_state(root)
        known = state["profiles"].get(name)
        if known is None or known["id"] != found["id"]:
            state["profiles"][name] = _fresh(found)
        state["selected"] = name
        _write_state(root, state)
    return status(root, catalog)


def _fresh(found: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": found["id"],
        "version": found["version"],
        "sha256": found["sha256"],
        "step_ids": [s["id"] for s in found["profile"]["steps"]],
        "selected_at": runs_core.now(),
        "history": [],
        "steps": {},
        "slots": {},
        "publications": {},
    }


def clear(root: Path) -> None:
    """Stop following a profile. Its progress stays, for when it is chosen again."""
    with _lock:
        state = read_state(root)
        state["selected"] = None
        _write_state(root, state)


def _current(root: Path, catalog: Catalog) -> tuple[dict[str, Any], str, dict[str, Any], dict]:
    state = read_state(root)
    name = state["selected"]
    if name is None:
        raise ProfileError("no_profile", "Choose a profile first.")
    found, _ = _study_profile(root, name, catalog)
    return state, name, found, state["profiles"][name]


def _require_unchanged(found: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    if found["problems"]:
        raise ProfileError(
            "profile_invalid",
            "The profile file has problems. Fix it before changing progress.",
            problems=found["problems"],
        )
    if found["sha256"] != progress["sha256"]:
        raise ProfileError(
            "profile_changed",
            "The profile file changed since it was adopted. Review the changes and adopt "
            "the new text before changing progress.",
        )
    return found["profile"]


def _step(profile: dict[str, Any], step_id: str, *types: str) -> dict[str, Any]:
    for step in profile["steps"]:
        if step["id"] == step_id:
            if types and step["type"] not in types:
                raise ProfileError("bad_step", "That step can't be used for this.")
            return step
    raise ProfileError("not_found", "The profile has no such step.")


def mark_step(
    root: Path, catalog: Catalog, step_id: str, done: bool, items: list[str] | None
) -> dict[str, Any]:
    """Tick or untick a step the user confirms (instruction, action, checkpoint) or the
    items of a checklist. A checklist is done when every item is ticked."""
    with _lock:
        state, _, found, progress = _current(root, catalog)
        profile = _require_unchanged(found, progress)
        step = _step(profile, step_id, "instruction", "action", "checkpoint", "checklist")
        entry: dict[str, Any] = {"done": done, "at": runs_core.now()}
        if step["type"] == "checklist":
            known = [i["id"] for i in step["items"]]
            ticked = list(dict.fromkeys(items or []))
            if any(i not in known for i in ticked):
                raise ProfileError("bad_step", "That checklist has no such item.")
            entry = {"done": len(ticked) == len(known), "at": entry["at"], "items": ticked}
        elif items is not None:
            raise ProfileError("bad_step", "Only a checklist has items.")
        progress["steps"][step_id] = entry
        _write_state(root, state)
    return status(root, catalog)


def _completed_run(root: Path, run_id: str) -> dict[str, Any]:
    if not runs_core.RUN_ID_RE.match(run_id):
        raise ProfileError("not_found", "There is no such run.")
    try:
        manifest = runs_core.read_manifest(runs_core.run_dir(root, run_id), run_id)
    except runs_core.RunError:
        raise ProfileError("not_found", "There is no such run.") from None
    if manifest["state"] != "completed":
        raise ProfileError(
            "run_not_completed", "Only a completed run can fill a slot. Complete it first."
        )
    return manifest


def assign_run(root: Path, catalog: Catalog, slot: str, run_id: str | None) -> dict[str, Any]:
    """Put the completed run the user chose in a slot, or empty the slot."""
    with _lock:
        state, _, found, progress = _current(root, catalog)
        profile = _require_unchanged(found, progress)
        if not any(s["type"] == "run" and s["slot"] == slot for s in profile["steps"]):
            raise ProfileError("not_found", "The profile has no such slot.")
        if run_id is None:
            progress["slots"].pop(slot, None)
        else:
            _completed_run(root, run_id)
            other = [k for k, v in progress["slots"].items() if v["run_id"] == run_id and k != slot]
            if other:
                raise ProfileError(
                    "run_in_other_slot",
                    "That run already fills another slot. Each slot is its own run.",
                )
            progress["slots"][slot] = {"run_id": run_id, "at": runs_core.now()}
        _write_state(root, state)
    return status(root, catalog)


def _publication_runs(root: Path, publication_id: str) -> list[str]:
    from opncafyr.workflows.nca.publish import curator

    try:
        doc = curator.read_publication(root, publication_id)
    except curator.PublishError as exc:
        raise ProfileError("not_found", exc.message) from None
    return [r["run_id"] for r in doc["runs"]]


def assign_publication(
    root: Path, catalog: Catalog, step_id: str, publication_id: str | None
) -> dict[str, Any]:
    """Record the publication the user chose for a publication step, or clear it.

    Its runs must be exactly the runs in the step's slots, every slot filled: the grouping
    is the user's, made explicitly on the Publish page, and only checked here.
    """
    with _lock:
        state, _, found, progress = _current(root, catalog)
        profile = _require_unchanged(found, progress)
        step = _step(profile, step_id, "publication")
        if publication_id is None:
            progress["publications"].pop(step_id, None)
        else:
            empty = [s for s in step["slots"] if s not in progress["slots"]]
            if empty:
                raise ProfileError(
                    "slots_empty", f"Fill these slots first: {', '.join(empty)}.", slots=empty
                )
            wanted = {progress["slots"][s]["run_id"] for s in step["slots"]}
            runs = set(_publication_runs(root, publication_id))
            if runs != wanted:
                raise ProfileError(
                    "runs_differ",
                    "This publication's runs are not exactly the runs in the step's slots. "
                    "Publish a selection from exactly those runs, then choose it here.",
                    missing=sorted(wanted - runs),
                    extra=sorted(runs - wanted),
                )
            progress["publications"][step_id] = {
                "publication_id": publication_id,
                "at": runs_core.now(),
            }
        _write_state(root, state)
    return status(root, catalog)


def adopt(root: Path, catalog: Catalog, sha256: str) -> dict[str, Any]:
    """Follow the profile file's new text. ``sha256`` is the text the user reviewed.

    Progress is kept for steps, slots and publication steps that still exist; the rest is
    dropped. The text followed before is kept in ``history``.
    """
    with _lock:
        state, _, found, progress = _current(root, catalog)
        if found["problems"]:
            raise ProfileError(
                "profile_invalid", "Fix this profile first.", problems=found["problems"]
            )
        if found["sha256"] != sha256:
            raise ProfileError(
                "profile_changed", "The file changed again. Review it once more, then adopt it."
            )
        if found["id"] != progress["id"]:
            raise ProfileError(
                "profile_replaced",
                "This file now holds a different profile. Choose it again to start its progress.",
            )
        if found["sha256"] != progress["sha256"]:
            profile = found["profile"]
            steps = {s["id"]: s for s in profile["steps"]}
            slots = {s["slot"] for s in profile["steps"] if s["type"] == "run"}
            progress["history"].append(
                {
                    "sha256": progress["sha256"],
                    "version": progress["version"],
                    "until": runs_core.now(),
                }
            )
            progress.update(sha256=found["sha256"], version=found["version"], step_ids=list(steps))
            progress["steps"] = {k: v for k, v in progress["steps"].items() if k in steps}
            progress["slots"] = {k: v for k, v in progress["slots"].items() if k in slots}
            progress["publications"] = {
                k: v
                for k, v in progress["publications"].items()
                if k in steps and steps[k]["type"] == "publication"
            }
            _write_state(root, state)
    return status(root, catalog)


# --- what the page shows -----------------------------------------------------------------


def status(root: Path, catalog: Catalog) -> dict[str, Any]:
    """The followed profile, its progress, and what changed in its file since adoption."""
    state = read_state(root)
    name = state["selected"]
    if name is None:
        return {"selected": None}
    progress = state["profiles"][name]
    try:
        found, _ = _study_profile(root, name, catalog)
    except ProfileError as exc:
        return {"selected": name, "missing": True, "message": exc.message, "progress": progress}
    out: dict[str, Any] = {
        "selected": name,
        "path": f"{PROFILES_DIR}/{name}",
        "sha256": found["sha256"],
        "adopted_sha256": progress["sha256"],
        "changed": found["sha256"] != progress["sha256"],
        "problems": found["problems"],
        "progress": progress,
    }
    profile = found.get("profile")
    if profile is None:
        return out
    out["profile"] = profile
    ids = [s["id"] for s in profile["steps"]]
    if out["changed"]:
        before = progress["step_ids"]
        out["added"] = [i for i in ids if i not in before]
        out["dropped"] = [i for i in before if i not in ids]
    out["steps"] = [_step_status(root, step, progress) for step in profile["steps"]]
    out["complete"] = all(s["complete"] for s in out["steps"])
    return out


def _step_status(root: Path, step: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": step["id"], "type": step["type"]}
    if step["type"] == "run":
        filled = progress["slots"].get(step["slot"])
        out["run_id"] = filled and filled["run_id"]
        out["complete"] = bool(filled) and _still_completed(root, filled["run_id"])
    elif step["type"] == "publication":
        chosen = progress["publications"].get(step["id"])
        out["publication_id"] = chosen and chosen["publication_id"]
        out["complete"] = bool(chosen) and _still_matches(root, step, progress, chosen)
    else:
        entry = progress["steps"].get(step["id"], {})
        out["complete"] = bool(entry.get("done"))
        if step["type"] == "checklist":
            out["items"] = entry.get("items", [])
    return out


def _still_completed(root: Path, run_id: str) -> bool:
    try:
        _completed_run(root, run_id)
    except ProfileError:
        return False
    return True


def _still_matches(
    root: Path, step: dict[str, Any], progress: dict[str, Any], chosen: dict[str, Any]
) -> bool:
    if any(s not in progress["slots"] for s in step["slots"]):
        return False
    try:
        runs = set(_publication_runs(root, chosen["publication_id"]))
    except ProfileError:
        return False
    return runs == {progress["slots"][s]["run_id"] for s in step["slots"]}


def candidates(root: Path, catalog: Catalog) -> dict[str, Any]:
    """Completed runs and recorded publications the user may choose from. Nothing is
    preselected; a run made for a step is marked so, as a hint only."""
    from opncafyr.core.artifacts import list_publications, list_runs

    runs = []
    for run in list_runs(root):
        if run.get("state") != "completed":
            continue
        try:
            manifest = runs_core.read_manifest(runs_core.run_dir(root, run["run_id"]))
        except runs_core.RunError:
            continue
        runs.append({**run, "workflow_context": manifest.get("workflow_context")})
    pubs = []
    for pub in list_publications(root):
        if pub.get("state") != "recorded":
            continue
        try:
            pub_runs = _publication_runs(root, pub["publication_id"])
        except ProfileError:
            continue
        pubs.append({**pub, "runs": pub_runs})
    return {"runs": runs, "publications": pubs}


def context_for(root: Path, catalog: Catalog, step_id: str, kind: str) -> dict[str, Any]:
    """The ``workflow_context`` for a new run (``kind="run"``) or publication made for a
    step of the followed profile, as adopted. The profile must be unchanged."""
    state, name, found, progress = _current(root, catalog)
    profile = _require_unchanged(found, progress)
    _step(profile, step_id, kind)
    return {
        "profile_id": profile["id"],
        "profile_version": profile["version"],
        "profile_sha256": found["sha256"],
        "profile_path": f"{PROFILES_DIR}/{name}",
        "step_id": step_id,
    }


def valid_id(value: str) -> bool:
    """A step, slot or item id as the schema allows it."""
    return bool(ID_RE.match(value))
