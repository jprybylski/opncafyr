"""The external data pack (#83, ADR 0012): datum-managed data a study depends on.

A study team declares the external data it uses (an https file, a file on a share, one
file from a Git repository) in ``.data.yaml``; the "External data" page fetches it into
``source-data/`` on request, after a review, and checks whether upstream changed. Every
datum call goes through ``opncafyr.adapters.datum``, which this pack alone imports.

The pack is optional and removable: leaving datum out at install time shows the page as
unavailable; leaving this pack and the adapter out of a build changes nothing else. The
only link to the analysis flow is generic: fetched files land in ``source-data/``, which
the dataset scan already lists, and ``FetchedAnnotator`` labels them.

- ``api.py``: routes under ``/api/datafetch/``.
- ``annotate.py``: the dataset-scan label ("fetched by datum").
- ``web/external-data.js``: the page.
"""

from __future__ import annotations

from opncafyr.core.registry import GuideTarget, WorkflowPack, WorkflowView

VIEW = "external-data"

TARGETS = (
    GuideTarget("datafetch.status", "External data: Datasets", VIEW),
    GuideTarget("datafetch.setup", "External data: Set up", VIEW),
    GuideTarget("datafetch.add", "External data: Add a dataset", VIEW),
    GuideTarget("datafetch.check", "External data: Check for updates", VIEW),
    GuideTarget("datafetch.fetch", "External data: Fetch", VIEW),
    GuideTarget("datafetch.review", "External data: Review", VIEW),
    GuideTarget("datafetch.apply", "External data: Apply", VIEW),
)


def workflow() -> WorkflowPack:
    from opncafyr.workflows.datafetch import api
    from opncafyr.workflows.datafetch.annotate import FetchedAnnotator

    annotator = FetchedAnnotator()  # one per app: mount binds it to that app's workspace

    def mount(app: object) -> None:
        annotator.bind(app)
        api.mount(app)

    return WorkflowPack(
        id="datafetch",
        annotators=(annotator,),
        mount=mount,
        static=("opncafyr.workflows.datafetch", "web"),
        targets=TARGETS,
        views=(
            WorkflowView(
                id=VIEW,
                title="External data",
                module="external-data.js",
                icon="folder",
                summary="Declare, fetch and check data the study depends on (datum).",
                requires_workspace=True,
            ),
        ),
    )
