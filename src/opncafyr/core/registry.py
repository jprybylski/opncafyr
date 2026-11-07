"""Registry of trusted, installed tool descriptors.

Descriptors are read only from the installed ``opncafyr.adapters`` package. Nothing in a
study workspace can register a tool, add a command, or override an executable path.

Versioning: a descriptor states the format it is written in (``schema_version``, today
"1.0" of ``tool-descriptor-v1``) and the adapter's own ``version``. Descriptors ship with
the launcher, so there is nothing to migrate: a format the registry does not know fails
startup. A new format gets a new schema file; ``tool-descriptor-v1`` is never changed
incompatibly.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from opncafyr.core.datasets import DatasetAnnotator, Importer
from opncafyr.core.manifest import load_schema

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


@dataclass(frozen=True)
class JobContract:
    input_schema: str
    output_schema: str
    timeout_s: int | None = None


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

    @classmethod
    def from_mapping(cls, data: Any, source: str = "<descriptor>") -> ToolDescriptor:
        _check_schema(data, source)
        _check_references(data, source)
        web = data.get("web_app") or {}
        health = data["health"]
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
            jobs={
                name: JobContract(job["input_schema"], job["output_schema"], job.get("timeout_s"))
                for name, job in (data.get("jobs") or {}).items()
            },
            optional_requirements=tuple(
                OptionalRequirement(
                    req["name"],
                    req["executable"],
                    req["message"],
                    tuple(req.get("capabilities", ())),
                )
                for req in data.get("optional_requirements", ())
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
    for name, job in (data.get("jobs") or {}).items():
        if name not in capabilities:
            raise DescriptorError(source, f"jobs.{name}", "a job must be a declared capability")
        for key in ("input_schema", "output_schema"):
            try:
                load_schema(job[key])
            except FileNotFoundError:
                raise DescriptorError(
                    source, f"jobs.{name}.{key}", f"no bundled schema named {job[key]!r}"
                ) from None
    for i, req in enumerate(data.get("optional_requirements", ())):
        for j, cap in enumerate(req.get("capabilities", ())):
            if cap not in capabilities:
                raise DescriptorError(
                    source,
                    f"optional_requirements[{i}].capabilities[{j}]",
                    f"{cap!r} is not a declared capability",
                )


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
        descriptor = ToolDescriptor.from_mapping(data, source)
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
    """

    id: str
    importers: tuple[Importer, ...] = ()
    annotators: tuple[DatasetAnnotator, ...] = ()
    mount: Callable[[Any], None] | None = None
    static: tuple[str, str] | None = None
    views: tuple[WorkflowView, ...] = ()


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
