"""The NCA workflow pack: import, clean, map columns, pick one analyte, run opnca-r.

This is the clin pharm layer on top of the tool-agnostic core. Core finds it by package
scan (``core.registry.load_workflows``) and calls ``workflow()``.

- ``importers/csv.py``: the CSV importer behind ``core.datasets.Importer`` (#21).
- ``analytes.py``: candidate analyte columns and exact values for the preview (#21).
- ``recipe.py``, ``clean_compile.py``, ``cleaning.py``: cleaning recipes, compiled to a
  fixed R program and run as jobs (#22).
- ``api.py``: the pack's routes under ``/api/nca/``; ``web/``: the Analysis page.
- ``TARGETS`` and ``CONDITIONS``: what tutorials and study profiles may point at and wait
  for on the pack's pages (ADR 0010). Each target id is marked in ``web/`` as
  ``data-target``; each condition is registered there and reads page state only.
"""

from __future__ import annotations

from opncafyr.core.registry import GuideCondition, GuideTarget, WorkflowPack, WorkflowView

TARGETS = (
    GuideTarget("nca.recipe.card", "Analysis: 1. Source and recipe", "analysis"),
    GuideTarget("nca.recipe.source", "Analysis: Imported file", "analysis"),
    GuideTarget("nca.recipe.recipe", "Analysis: Recipe", "analysis"),
    GuideTarget("nca.recipe.new", "Analysis: New recipe", "analysis"),
    GuideTarget("nca.steps.card", "Analysis: 2. Cleaning steps", "analysis"),
    GuideTarget("nca.steps.add", "Analysis: Add a step", "analysis"),
    GuideTarget("nca.steps.preview", "Analysis: Preview", "analysis"),
    GuideTarget("nca.steps.apply", "Analysis: Apply to all rows", "analysis"),
    GuideTarget("nca.result.card", "Analysis: 3. Result", "analysis"),
    GuideTarget("nca.mapping.card", "Analysis: 4. Column mapping", "analysis"),
    GuideTarget("nca.mapping.save", "Analysis: Save mapping", "analysis"),
    GuideTarget("nca.analyte.card", "Analysis: 5. Analyte and checks", "analysis"),
    GuideTarget("nca.analyte.value", "Analysis: Analyte (exact value)", "analysis"),
    GuideTarget("nca.analyte.check", "Analysis: Run checks", "analysis"),
    GuideTarget("nca.start.card", "Analysis: 6. Start analysis", "analysis"),
    GuideTarget("nca.start.button", "Analysis: Start analysis", "analysis"),
    GuideTarget("nca.runs.card", "Analysis: 7. Runs", "analysis"),
    GuideTarget("nca.curator.card", "Publish: 1. Choose tables and figures", "publish"),
    GuideTarget("nca.curator.check", "Publish: Check", "publish"),
    GuideTarget("nca.curator.publish", "Publish: Publish", "publish"),
    GuideTarget("nca.publications.card", "Publish: 2. Publications", "publish"),
    GuideTarget("nca.outputs.card", "Publish: 3. Published files", "publish"),
    GuideTarget("nca.report.card", "Publish: 4. Report (DOCX)", "publish"),
    GuideTarget("nca.deck.card", "Publish: 5. Deck (PPTX)", "publish"),
    GuideTarget("nca.profile.choose", "Study profile: Profile", "profile"),
    GuideTarget("nca.profile.steps", "Study profile: Steps", "profile"),
)

CONDITIONS = (
    GuideCondition("nca.recipe.chosen", "A recipe is chosen on the Analysis page"),
    GuideCondition("nca.cleaned.ready", "The recipe is applied to all rows"),
    GuideCondition("nca.mapping.ready", "The saved column mapping blocks nothing"),
    GuideCondition("nca.checks.ready", "The checks ran for one analyte with nothing blocking"),
    GuideCondition("nca.run.created", "The study has a run"),
    GuideCondition("nca.run.completed", "The study has a completed run"),
    GuideCondition("nca.publication.recorded", "The study has a recorded publication"),
)


def workflow() -> WorkflowPack:
    from opncafyr.workflows.nca import api
    from opncafyr.workflows.nca.analytes import AnalyteAnnotator
    from opncafyr.workflows.nca.importers.csv import CsvImporter
    from opncafyr.workflows.nca.sensitive import sensitive_values

    return WorkflowPack(
        id="nca",
        importers=(CsvImporter(),),
        annotators=(AnalyteAnnotator(),),
        mount=api.mount,
        static=("opncafyr.workflows.nca", "web"),
        sensitive_values=sensitive_values,
        targets=TARGETS,
        conditions=CONDITIONS,
        tutorials=("opncafyr.workflows.nca", "tutorials"),
        views=(
            WorkflowView(
                id="profile",
                title="Study profile",
                module="profile.js",
                icon="jobs",
                summary="Follow your team's steps for this kind of study (guidance only).",
                requires_workspace=True,
            ),
            WorkflowView(
                id="analysis",
                title="Analysis",
                module="analysis.js",
                icon="table",
                summary="Clean, map, choose one analyte and run opnca-r.",
            ),
            WorkflowView(
                id="publish",
                title="Publish",
                module="publish.js",
                icon="doc",
                summary="Publish completed runs' tables and figures for reports and decks.",
            ),
            WorkflowView(
                id="history",
                title="Version history",
                module="history.js",
                icon="folder",
                summary="Optional Git snapshots of files you choose (gitneighbr).",
                requires_workspace=True,
            ),
        ),
    )
