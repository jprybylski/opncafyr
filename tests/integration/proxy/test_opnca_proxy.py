"""#16: opnca-r 0.2.9 behind /apps/opnca/, including the attacks from the upstream review."""

from __future__ import annotations

import json
import shutil

import httpx
import pytest
from proxy_harness import EVIL, assert_stays_under, load_page, tree_pids, wait_gone

from opncafyr.core.supervisor import ChildState

pytestmark = pytest.mark.integration
ROUTE = "/apps/opnca/"
API = ROUTE + "api/v1/"

# Every role sent explicitly: "" for unmapped optional roles, and all three BLQ rules
# (as --require-blq-rules demands). In the product these come from the user (#23, #27).
PRESET = {
    "id": "USUBJID",
    "time": "ACTUAL_TIME",
    "concentration": "CONC",
    "dose": "DOSE",
    "route": "ROUTE",
    "excluded": "",
    "nominal_time": "",
    "infusion_duration": "",
    "lloq": "",
    "blq": "",
    "missing": "",
    "blq_method": "conventional",
    "blq_predose": "zero",
    "blq_postdose": "exclude",
    "blq_terminal": "exclude",
}


@pytest.fixture(scope="module")
def session(runtime, tmp_path_factory):
    if not (runtime.has("rscript") and runtime.r_library is not None):
        pytest.skip("runtime has no Rscript/opnca-r (OPNCA_R_RELEASE_TOKEN not set in CI?)")
    sample = runtime.r_library / "opnca" / "extdata" / "cohort_sample.csv"
    run = tmp_path_factory.mktemp("run") / "20261106T120000Z-h001"
    (run / "input").mkdir(parents=True)
    shutil.copyfile(sample, run / "input" / "analysis.csv")
    mapping = run / "mapping.json"
    mapping.write_text(json.dumps(PRESET))
    return {
        "data_path": str(run / "input" / "analysis.csv"),
        "output_dir": str(run),
        "run_id": run.name,
        "mapping_path": str(mapping),
        "label": "Harness PARENT",
        "context": {"analyte": "PARENT", "run_id": run.name},
    }


@pytest.fixture(scope="module")
def child(controller, session):
    child = controller.supervisor.start("opnca", session)
    yield child
    controller.supervisor.stop("opnca")


def test_page_and_assets_stay_under_the_prefix(browser, controller, child):
    record = load_page(browser, controller, ROUTE)
    assert_stays_under(record, controller, ROUTE)
    assert any("/api/v1/" in u for u in record.requests), "the page made no API calls"


def test_schema_and_calculate_through_the_proxy(controller, child):
    with controller.client() as c:
        schema = c.get(API + "dataset/schema")
        assert schema.status_code == 200, schema.text
        assert schema.json()["ok"] is True
        body = {**PRESET, "metrics": ["cmax", "tmax", "auclast"]}
        calc = c.post(API + "analysis/calculate", json=body)
        assert calc.status_code == 200, calc.text
        results = calc.json()["data"]["results"]
        assert {r["metric"] for r in results} >= {"cmax", "auclast"}


def test_blq_rules_are_required(controller, child):
    body = {k: v for k, v in PRESET.items() if not k.startswith("blq_")}
    with controller.client() as c:
        resp = c.post(API + "analysis/calculate", json=body)
    assert resp.status_code == 400


def test_proxy_needs_the_session(controller, child):
    url = controller.origin + API + "dataset/schema"
    assert httpx.get(url).status_code == 403
    with controller.client() as c:
        assert (
            c.get(API + "dataset/schema", headers={"sec-fetch-site": "cross-site"}).status_code
            == 403
        )
        assert (
            c.post(API + "analysis/calculate", json=PRESET, headers={"origin": EVIL}).status_code
            == 403
        )


def test_direct_child_port_attacks_fail(child):
    """The browser can reach the child's port directly; the child must defend itself."""
    origin = child.context.origin
    token = child.spec.auth_headers
    # A cross-site form POST (the reproduced attack excluded a subject's profile).
    form = httpx.post(
        origin + API + "analysis/calculate",
        data={"id": "USUBJID"},
        headers={"origin": EVIL, "sec-fetch-site": "cross-site"},
    )
    assert form.status_code == 403
    # A foreign Host (DNS rebinding), even with the right token.
    rebind = httpx.get(origin + API + "dataset/schema", headers={**token, "host": "evil.example"})
    assert rebind.status_code == 400
    # No token at all.
    assert httpx.get(origin + API + "dataset/schema").status_code == 403
    # Sanity: the right token and Host work, so the failures above are the guards.
    assert httpx.get(origin + API + "dataset/schema", headers=token).status_code == 200


def test_orderly_stop_and_tree_kill(controller, session):
    sup = controller.supervisor
    child = sup.start("opnca", session)
    procs = tree_pids(child.pid)
    sup.stop("opnca")
    assert child.exit_code == 0
    assert not child.context.workdir.exists()
    assert wait_gone(procs) == []

    child = sup.start("opnca", session)
    procs = tree_pids(child.pid)
    child.proc.kill_tree()
    assert wait_gone(procs) == []
    assert sup.state("opnca") is ChildState.CRASHED
    sup.stop("opnca")
