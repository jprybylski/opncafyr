"""The NCA workflow pack: import, clean, map columns, pick one analyte, run opnca-r.

This is the clin pharm layer on top of the tool-agnostic core. Core finds it by package
scan (``core.registry.load_workflows``) and calls ``workflow()``.

- ``importers/csv.py``: the CSV importer behind ``core.datasets.Importer`` (#21).
- ``analytes.py``: candidate analyte columns and exact values for the preview (#21).

Planned ports from makecoolplots (Rust -> Python/R):
- column-role aliases and value checks: crates/mcp-inspect/src/roles.rs
- canonical routes, unit normalisation, BLQ parsing: crates/mcp-inspect/src/nca_input.rs
- analyte choice and "ask, don't guess": crates/mcp-codegen/src/endpoint.rs,
  crates/mcp-core/src/error.rs (needs_choice)
"""

from __future__ import annotations

from opncafyr.core.registry import WorkflowPack


def workflow() -> WorkflowPack:
    from opncafyr.workflows.nca.analytes import AnalyteAnnotator
    from opncafyr.workflows.nca.importers.csv import CsvImporter

    return WorkflowPack(id="nca", importers=(CsvImporter(),), annotators=(AnalyteAnnotator(),))
