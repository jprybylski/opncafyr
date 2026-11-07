"""The NCA workflow pack: import, clean, map columns, pick one analyte, run opnca-r.

This is the clin pharm layer on top of the tool-agnostic core. Core finds it by package
scan (``core.registry.load_workflows``) and calls ``workflow()``.

- ``importers/csv.py``: the CSV importer behind ``core.datasets.Importer`` (#21).
- ``analytes.py``: candidate analyte columns and exact values for the preview (#21).
- ``recipe.py``, ``clean_compile.py``, ``cleaning.py``: cleaning recipes, compiled to a
  fixed R program and run as jobs (#22).
- ``api.py``: the pack's routes under ``/api/nca/``; ``web/``: the Analysis page.
"""

from __future__ import annotations

from opncafyr.core.registry import WorkflowPack, WorkflowView


def workflow() -> WorkflowPack:
    from opncafyr.workflows.nca import api
    from opncafyr.workflows.nca.analytes import AnalyteAnnotator
    from opncafyr.workflows.nca.importers.csv import CsvImporter

    return WorkflowPack(
        id="nca",
        importers=(CsvImporter(),),
        annotators=(AnalyteAnnotator(),),
        mount=api.mount,
        static=("opncafyr.workflows.nca", "web"),
        views=(
            WorkflowView(
                id="analysis",
                title="Analysis",
                module="analysis.js",
                icon="table",
                summary="Clean, map, choose one analyte and run opnca-r.",
            ),
        ),
    )
