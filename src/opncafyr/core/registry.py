"""Registry of trusted, installed tool descriptors.

Descriptors are read only from the installed ``opncafyr.adapters`` package. Nothing in a
study workspace can register a tool, add a command, or override an executable path.

Versioning: a descriptor states the format it is written in (``schema_version``, today
"1.0" of ``tool-descriptor-v1``) and the adapter's own ``version``. Descriptors ship with
the launcher, so there is nothing to migrate: a format the registry does not know fails
startup. A new format gets a new schema file; ``tool-descriptor-v1`` is never changed
incompatibly. Optional keys may be added (``project_editor``, #52): every existing
descriptor stays valid.

Job contracts (``jobs``) name their schemas by file stem. A name is looked up first in the
adapter's own ``schemas/`` folder (``adapters/<tool>/schemas/<name>.schema.json``), then
among the launcher's ``opncafyr.schemas``; a name found in both places is refused, so a
contract never depends on lookup order (#55).
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match

from opncafyr.core.datasets import DatasetAnnotator, Importer
from opncafyr.core.manifest import load_schema, schema_problems

DESCRIPTOR_SCHEMA = "tool-descriptor-v1"
ADAPTERS_PACKAGE = "opncafyr.adapters"
WORKFLOWS_PACKAGE = "opncafyr.workflows"


class DescriptorError(ValueError):
    """An installed descriptor is invalid. Startup stops and names the file and field."""

    def __init__(self, source: str, field: str, message: str) -> None:
        self.source = source
        self.field = field
        self.message = message
        super().__init__(f"Invalid tool descriptor {source}: field '{field}': {message}")


@dataclass(frozen=True)
class HealthProbe:
    kind: str  # python_import | r_package | version_flag
    executable: str
    target: str | None = None


@dataclass(frozen=True)
class OptionalRequirement:
    name: str
    executable: str
    message: str
    capabilities: tuple[str, ...] = ()


# The folder inside an adapter package that holds the JSON Schemas it bundles.
BUNDLED_SCHEMAS = "schemas"


@dataclass(frozen=True)
class JobContract:
    """A job capability's declared input and output schemas and its timeout.

    ``output_document`` is the resolved output schema (bundled with the adapter or with
    the launcher), so a caller can check a job's output without looking it up again.
    """

    output_schema: str
    input_schema: str | None = None
    timeout_s: int | None = None
    output_document: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def check_output(self, document: Any, label: str) -> list[str]:
        """Where ``document`` breaks the output schema; empty when it conforms.

        Problems name the place and the rule, never a value (``manifest.schema_problems``).
        """
        return schema_problems(document, self.output_document, label)


@dataclass(frozen=True)
class ProjectEditorSpec:
    """A tool's opt-in to the project-file editor (#52). Ids only; the adapter bundles them."""

    capability: str
    project_root: str  # one visible folder at the top of the workspace
    file_profile: str
    settings_schema: str | None = None


# Folders a project editor may never be rooted at: the published outputs, and anything
# that differs from them only by case (case-insensitive file systems).
RESERVED_PROJECT_ROOTS = frozenset({"outputs"})


@dataclass(frozen=True)
class ToolDescriptor:
    id: str
    version: int
    runtime: str
    capabilities: tuple[str, ...]
    web_route: str | None
    raw: dict[str, Any]
    max_body_bytes: int | None = None
    display_name: str | None = None
    executables: tuple[str, ...] = ()
    health: HealthProbe | None = None
    jobs: dict[str, JobContract] | None = None
    optional_requirements: tuple[OptionalRequirement, ...] = ()
    project_editor: ProjectEditorSpec | None = None

    @classmethod
    def from_mapping(
        cls, data: Any, source: str = "<descriptor>", bundle: Traversable | Path | None = None
    ) -> ToolDescriptor:
        """Build a descriptor from its parsed YAML.

        ``bundle`` is the adapter's folder; job schemas may be resolved from its
        ``schemas/`` folder. Without it, only the launcher's schemas are available.
        """
        _check_schema(data, source)
        _check_references(data, source)
        jobs = _job_contracts(data, source, bundle)
        web = data.get("web_app") or {}
        health = data["health"]
        editor = data.get("project_editor")
        return cls(
            id=data["id"],
            version=data["version"],
            runtime=data["runtime"],
            capabilities=tuple(data["capabilities"]),
            web_route=web.get("route"),
            raw=data,
            max_body_bytes=web.get("max_body_bytes"),
            display_name=data.get("display_name"),
            executables=tuple(data["executables"]),
            health=HealthProbe(health["kind"], health["executable"], health.get("target")),
            jobs=jobs,
            optional_requirements=tuple(
                OptionalRequirement(
                    req["name"],
                    req["executable"],
                    req["message"],
                    tuple(req.get("capabilities", ())),
                )
                for req in data.get("optional_requirements", ())
            ),
            project_editor=(
                ProjectEditorSpec(
                    editor["capability"],
                    editor["project_root"],
                    editor["file_profile"],
                    editor.get("settings_schema"),
                )
                if editor
                else None
            ),
        )


def _field(path: Any) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(top level)"


def _check_schema(data: Any, source: str) -> None:
    validator = Draft202012Validator(load_schema(DESCRIPTOR_SCHEMA))
    error = best_match(validator.iter_errors(data))
    if error is None:
        return
    path = list(error.absolute_path)
    if error.validator == "required":
        # Point at the missing field itself, not its parent.
        missing = error.message.split("'")[1] if "'" in error.message else ""
        path.append(missing)
    elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        extra = sorted(k for k in error.instance if k not in allowed)
        path.append(extra[0] if extra else "")
    raise DescriptorError(source, _field(path), error.message)


def _check_references(data: dict[str, Any], source: str) -> None:
    """Cross-field rules the JSON Schema cannot express."""
    executables = set(data["executables"])
    capabilities = set(data["capabilities"])
    if data["health"]["executable"] not in executables:
        raise DescriptorError(
            source, "health.executable", "must be one of the descriptor's executables"
        )
    for name in data.get("jobs") or {}:
        if name not in capabilities:
            raise DescriptorError(source, f"jobs.{name}", "a job must be a declared capability")
    for i, req in enumerate(data.get("optional_requirements", ())):
        for j, cap in enumerate(req.get("capabilities", ())):
            if cap not in capabilities:
                raise DescriptorError(
                    source,
                    f"optional_requirements[{i}].capabilities[{j}]",
                    f"{cap!r} is not a declared capability",
                )
    editor = data.get("project_editor")
    if editor:
        if editor["capability"] not in capabilities:
            raise DescriptorError(
                source, "project_editor.capability", "must be a declared capability"
            )
        if editor["project_root"].lower() in RESERVED_PROJECT_ROOTS:
            raise DescriptorError(
                source, "project_editor.project_root", "can't be the published outputs folder"
            )


def _job_contracts(
    data: dict[str, Any], source: str, bundle: Traversable | Path | None
) -> dict[str, JobContract]:
    contracts: dict[str, JobContract] = {}
    for name, job in (data.get("jobs") or {}).items():
        documents: dict[str, dict[str, Any]] = {}
        for key in ("input_schema", "output_schema"):
            if job.get(key) is not None:
                documents[key] = _resolve_schema(job[key], bundle, source, f"jobs.{name}.{key}")
        contracts[name] = JobContract(
            output_schema=job["output_schema"],
            input_schema=job.get("input_schema"),
            timeout_s=job.get("timeout_s"),
            output_document=documents["output_schema"],
        )
    return contracts


def _resolve_schema(
    name: str, bundle: Traversable | Path | None, source: str, where: str
) -> dict[str, Any]:
    """A job schema by name: the adapter's ``schemas/`` folder, else the launcher's."""
    own = None
    if bundle is not None:
        candidate = bundle / BUNDLED_SCHEMAS / f"{name}.schema.json"
        if candidate.is_file():
            own = candidate
    try:
        launcher = load_schema(name)
    except FileNotFoundError:
        launcher = None
    if own is not None and launcher is not None:
        raise DescriptorError(
            source,
            where,
            f"{name!r} is both bundled with the adapter and a launcher schema; rename the "
            "adapter's schema",
        )
    if launcher is not None:
        return launcher
    if own is None:
        raise DescriptorError(
            source,
            where,
            f"no schema named {name!r} in the adapter's {BUNDLED_SCHEMAS}/ folder or the "
            "launcher's schemas",
        )
    try:
        schema = json.loads(own.read_text("utf-8"))
        Draft202012Validator.check_schema(schema)
    except (ValueError, SchemaError):
        raise DescriptorError(source, where, f"{name!r} is not a valid JSON Schema") from None
    return schema


def load_descriptors(root: Traversable | Path | None = None) -> dict[str, ToolDescriptor]:
    """Load every ``adapters/<tool>/descriptor.yaml`` shipped with the package.

    ``root`` exists for tests only. The launcher always reads the installed package.
    """
    found: dict[str, ToolDescriptor] = {}
    base = resources.files(ADAPTERS_PACKAGE) if root is None else root
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        desc = entry / "descriptor.yaml"
        if not desc.is_file():
            continue
        source = f"adapters/{entry.name}/descriptor.yaml"
        try:
            data = yaml.safe_load(desc.read_text("utf-8"))
        except yaml.YAMLError as exc:
            raise DescriptorError(source, "(file)", f"not valid YAML ({exc})") from None
        descriptor = ToolDescriptor.from_mapping(data, source, bundle=entry)
        if descriptor.id != entry.name:
            raise DescriptorError(source, "id", f"must match its folder name {entry.name!r}")
        found[descriptor.id] = descriptor
    return found


def load_web_adapters(descriptors: dict[str, ToolDescriptor]) -> dict[str, Any]:
    """Instantiate the web-app adapter of every descriptor that declares a ``web_app``.

    Adapters are found by package scan (``opncafyr.adapters.<id>``), never imported by name
    from core. Each such package exposes ``web_app()`` returning a ``WebAppAdapter``.
    """
    adapters: dict[str, Any] = {}
    for tool_id, descriptor in descriptors.items():
        if descriptor.web_route is None:
            continue
        module = importlib.import_module(f"{ADAPTERS_PACKAGE}.{tool_id}")
        factory = getattr(module, "web_app", None)
        if factory is None:
            raise ValueError(f"Adapter {tool_id!r} declares a web_app but has no web_app().")
        adapters[tool_id] = factory()
    return adapters


def load_project_editors(descriptors: dict[str, ToolDescriptor]) -> dict[str, Any]:
    """Instantiate the project editor of every descriptor that declares ``project_editor``.

    Found by package scan like web apps: ``opncafyr.adapters.<id>`` exposes
    ``project_editor()`` returning a ``core.adapter.ProjectEditor``. The adapter must bundle
    the file profile and settings schema the descriptor names, or startup stops.
    """
    editors: dict[str, Any] = {}
    for tool_id, descriptor in descriptors.items():
        if descriptor.project_editor is None:
            continue
        module = importlib.import_module(f"{ADAPTERS_PACKAGE}.{tool_id}")
        factory = getattr(module, "project_editor", None)
        if factory is None:
            raise ValueError(
                f"Adapter {tool_id!r} declares a project_editor but has no project_editor()."
            )
        editors[tool_id] = factory()
    check_project_editors(descriptors, editors)
    return editors


def check_project_editors(descriptors: dict[str, ToolDescriptor], editors: dict[str, Any]) -> None:
    """Every declared editor exists, implements the protocol and bundles what it names."""
    from opncafyr.core.adapter import ProjectEditor

    for tool_id, descriptor in descriptors.items():
        spec = descriptor.project_editor
        if spec is None:
            continue
        source = f"adapters/{tool_id}/descriptor.yaml"
        editor = editors.get(tool_id)
        if editor is None or not isinstance(editor, ProjectEditor):
            raise DescriptorError(
                source, "project_editor", "the adapter provides no project editor"
            )
        if spec.file_profile not in editor.profiles:
            raise DescriptorError(
                source,
                "project_editor.file_profile",
                f"the adapter bundles no file profile {spec.file_profile!r}",
            )
        if spec.settings_schema is not None and spec.settings_schema not in editor.settings_schemas:
            raise DescriptorError(
                source,
                "project_editor.settings_schema",
                f"the adapter bundles no settings schema {spec.settings_schema!r}",
            )


# --- Workflow packs ----------------------------------------------------------------


_VIEW_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class WorkflowView:
    """A page a workflow pack adds to the home shell (``#/<id>`` and a sidenav link).

    ``module`` is a file in the pack's ``static`` folder, served under
    ``/static/workflows/<pack id>/``; it exports ``init(container)``. ``icon`` names a
    symbol in the home page's sprite.
    """

    id: str
    title: str
    module: str
    icon: str = "table"
    summary: str = ""

    def check(self) -> None:
        if not _VIEW_ID_RE.match(self.id):
            raise ValueError(f"Invalid workflow view id {self.id!r}.")
        if "/" in self.module or "\\" in self.module or not self.module.endswith(".js"):
            raise ValueError(f"Workflow view module must be a .js file name: {self.module!r}")


@dataclass(frozen=True)
class WorkflowPack:
    """What a workflow package adds to core.

    - ``importers`` and ``annotators`` feed the datasets service (#21).
    - ``mount(app)`` adds the pack's own API routes (under ``/api/<pack id>/``). They
      pass through the same security middleware as every other route.
    - ``static`` is ``(package, folder)`` of the pack's page assets and ``views`` the
      pages the shell lists. Core never knows what a view does.
    - ``sensitive_values(workspace)`` returns identifier values in a study folder (such
      as subject IDs) that must never reach a support bundle (#38). Core calls it only
      to redact them, and treats a failure as "no values".
    """

    id: str
    importers: tuple[Importer, ...] = ()
    annotators: tuple[DatasetAnnotator, ...] = ()
    mount: Callable[[Any], None] | None = None
    static: tuple[str, str] | None = None
    views: tuple[WorkflowView, ...] = ()
    sensitive_values: Callable[[Path], Iterable[str]] | None = None


def load_workflows() -> list[WorkflowPack]:
    """Every installed workflow pack, in package-name order.

    Packs are found by package scan (``opncafyr.workflows.<name>``), never imported by
    name from core. A package that has no ``workflow()`` adds nothing.
    """
    package = importlib.import_module(WORKFLOWS_PACKAGE)
    packs: list[WorkflowPack] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{WORKFLOWS_PACKAGE}.{info.name}")
        factory = getattr(module, "workflow", None)
        if factory is None:
            continue
        pack = factory()
        if not isinstance(pack, WorkflowPack):
            raise ValueError(f"Workflow {info.name!r}: workflow() must return a WorkflowPack.")
        for view in pack.views:
            view.check()
        packs.append(pack)
    return packs
