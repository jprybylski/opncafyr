"""Registry of trusted, installed tool descriptors.

Descriptors are read only from the installed ``opncafyr.adapters`` package. Nothing in a
study workspace can register a tool, add a command, or override an executable path.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from opncafyr.core.manifest import validate

DESCRIPTOR_SCHEMA = "tool-descriptor-v1"
ADAPTERS_PACKAGE = "opncafyr.adapters"


@dataclass(frozen=True)
class ToolDescriptor:
    id: str
    version: int
    runtime: str
    capabilities: tuple[str, ...]
    web_route: str | None
    raw: dict[str, Any]
    max_body_bytes: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ToolDescriptor:
        validate(data, DESCRIPTOR_SCHEMA)
        web = data.get("web_app") or {}
        return cls(
            id=data["id"],
            version=data["version"],
            runtime=data["runtime"],
            capabilities=tuple(data["capabilities"]),
            web_route=web.get("route"),
            raw=data,
            max_body_bytes=web.get("max_body_bytes"),
        )


def load_descriptors() -> dict[str, ToolDescriptor]:
    """Load every ``adapters/<tool>/descriptor.yaml`` shipped with the package."""
    found: dict[str, ToolDescriptor] = {}
    root = resources.files(ADAPTERS_PACKAGE)
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        desc = entry / "descriptor.yaml"
        if not desc.is_file():
            continue
        descriptor = ToolDescriptor.from_mapping(yaml.safe_load(desc.read_text("utf-8")))
        if descriptor.id in found:
            raise ValueError(f"Duplicate tool id {descriptor.id!r}.")
        if descriptor.id != entry.name:
            raise ValueError(f"Descriptor id {descriptor.id!r} must match folder {entry.name!r}.")
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
