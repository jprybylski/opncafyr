"""The mapping preset opnca-r is started with (``serve.R --mapping``, #25, #27).

opnca-r (``R/app-launch.R``) stops on an unknown key or a column that isn't in the data,
and guesses any role left out (opnca-r#46), so the preset names every role it knows:
the four required ones always, ``""`` for every optional role that isn't mapped, the
three BLQ rules and the BLQ method the user chose (ADR 0005 Q3). Concentration, BLQ,
missing and excluded point at the snapshot's own ``NCA_*`` columns.

``units`` (opnca-r 0.3.1, opnca-r#56) declares the three unit labels of the frozen input
(``units.analysis_labels``). opnca-r locks them in its page and refuses a request with
other labels, so results are never labelled with its defaults (#57).
"""

from __future__ import annotations

from typing import Any

from opncafyr.workflows.nca import mapping as M

REQUIRED = ("id", "time", "concentration", "route")
OPTIONAL = ("dose", "excluded", "nominal_time", "infusion_duration", "lloq", "blq", "missing")
SETTINGS = ("blq_method", "blq_predose", "blq_postdose", "blq_terminal")
UNIT_LABELS = ("concentration", "time", "dose")
# opnca_units()'s label rules (opnca-r R/units.R): trimmed, not empty, at most 64
# characters, no control characters. The launcher's own labels always pass; checked anyway.
MAX_LABEL = 64
KNOWN = (*REQUIRED, *OPTIONAL, "tau", "group_by", "grouping", *SETTINGS, "units")


class PresetError(ValueError):
    pass


def build_preset(
    mapping: dict[str, Any],
    snapshot_columns: dict[str, str],
    blq: dict[str, Any],
    header: list[str],
    units: dict[str, str],
) -> dict[str, Any]:
    role = lambda name: M.role(mapping, name) or ""  # noqa: E731
    preset: dict[str, Any] = {
        "id": role("SUBJECT_ID"),
        "time": role("ACTUAL_TIME"),
        "concentration": snapshot_columns["CONC"],
        "route": role("ROUTE"),
        "dose": role("DOSE"),
        "excluded": snapshot_columns["EXCLUDED"],
        "nominal_time": role("NOMINAL_TIME"),
        "infusion_duration": role("INFUSION_DURATION"),
        "lloq": role("LLOQ"),
        "blq": snapshot_columns["BLQ"],
        "missing": snapshot_columns["MISSING"],
        "blq_method": blq["method"],
        **blq["rules"],
        "units": {name: units[name] for name in UNIT_LABELS},
    }
    if role("TAU"):
        preset["tau"] = role("TAU")
    check_preset(preset, header)
    return preset


def check_preset(preset: dict[str, Any], header: list[str]) -> None:
    """What opnca-r itself would refuse, refused before launch."""
    unknown = set(preset) - set(KNOWN)
    if unknown:
        raise PresetError(f"opnca-r does not know: {', '.join(sorted(unknown))}.")
    for key in REQUIRED:
        if not preset.get(key):
            raise PresetError(f"The preset needs {key}.")
    for key in OPTIONAL:
        if key not in preset:
            raise PresetError(f'The preset must name {key}, even as "".')
    for key in SETTINGS:
        if not preset.get(key):
            raise PresetError(f"The preset needs {key}, chosen by the user.")
    units = preset.get("units")
    if not isinstance(units, dict) or set(units) != set(UNIT_LABELS):
        raise PresetError("The preset needs units: concentration, time and dose labels.")
    for name, label in units.items():
        if (
            not isinstance(label, str)
            or not label
            or label != label.strip()
            or len(label) > MAX_LABEL
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in label)
        ):
            raise PresetError(f"The {name} unit label {label!r} is not one opnca-r accepts.")
    for key in (*REQUIRED, *OPTIONAL, "tau"):
        value = preset.get(key)
        if value and value not in header:
            raise PresetError(f"{key} names {value}, which is not in the snapshot.")
