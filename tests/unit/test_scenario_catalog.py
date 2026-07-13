"""Gate 5.5 scenario catalog: validation + full offline matrix (no Docker).

The symmetric ``CatalogLabSim`` (tests/conftest.py) models both routers' mutable
state independently, so every catalog case — including the reverse-orientation
``router_b`` cases — runs end to end through the REAL ``run_accepted_case`` path
offline. All cases are asserted into ONE shared run index.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.artifacts import load_run_index, load_verified_run_from_index, verify_run_index
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.orchestrator import (
    SCENARIO_CATALOG,
    ScenarioCase,
    ScenarioValidationError,
    validate_scenario_case,
)
from verifiednet.orchestrator.catalog import cases_for_template
from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts

pytestmark = pytest.mark.unit


# --- validation ---------------------------------------------------------------


def test_catalog_cases_all_validate() -> None:
    topo = two_router_frr_topology()
    for case in SCENARIO_CATALOG:
        validate_scenario_case(case, topo)


def test_catalog_case_ids_unique_and_counts() -> None:
    ids = [c.case_id for c in SCENARIO_CATALOG]
    assert len(ids) == len(set(ids))
    for template in ("bgp_remote_as_mismatch", "bgp_neighbor_removal",
                     "iface_admin_shutdown", "bgp_prefix_withdrawal"):
        n = len(cases_for_template(template))
        assert 2 <= n <= 4, f"{template} has {n} cases"


def _mini_timeouts() -> ScenarioTimeouts:
    return ScenarioTimeouts(precondition_s=1, onset_s=1, recovery_s=1,
                            command_s=1, poll_interval_s=1)


def _ras_case(target: str, wrong: int) -> ScenarioCase:
    return ScenarioCase(
        case_id="tmp", expected_target=target,
        scenario=ScenarioDefinition(
            scenario_id="s", family="bgp", template_id="bgp_remote_as_mismatch", version=1,
            parameters={"wrong_asn": wrong, "target_node": target, "target_session": "a-b"},
            timeouts=_mini_timeouts()))


@pytest.mark.parametrize("target,wrong,match", [
    ("router_a", 65001, "local ASN"),
    ("router_a", 65002, "actual peer ASN"),
    ("router_a", 0, "out of range"),
    ("router_b", 65002, "local ASN"),
])
def test_remote_as_validation_rejections(target: str, wrong: int, match: str) -> None:
    with pytest.raises(ScenarioValidationError, match=match):
        validate_scenario_case(_ras_case(target, wrong), two_router_frr_topology())


def test_unknown_target_and_session_rejected() -> None:
    topo = two_router_frr_topology()
    bad_node = ScenarioCase(
        case_id="x", expected_target="router_x",
        scenario=ScenarioDefinition(
            scenario_id="s", family="bgp", template_id="bgp_neighbor_removal", version=1,
            parameters={"target_node": "router_x", "target_session": "a-b"},
            timeouts=_mini_timeouts()))
    with pytest.raises(ScenarioValidationError, match="unknown target_node"):
        validate_scenario_case(bad_node, topo)
    bad_sess = ScenarioCase(
        case_id="x", expected_target="router_a",
        scenario=ScenarioDefinition(
            scenario_id="s", family="bgp", template_id="bgp_neighbor_removal", version=1,
            parameters={"target_node": "router_a", "target_session": "z-z"},
            timeouts=_mini_timeouts()))
    with pytest.raises(ScenarioValidationError, match="unknown session"):
        validate_scenario_case(bad_sess, topo)


def test_prefix_validation_rejections() -> None:
    topo = two_router_frr_topology()

    def case(prefix: str, target: str = "router_a") -> ScenarioCase:
        return ScenarioCase(
            case_id="x", expected_target=target,
            scenario=ScenarioDefinition(
                scenario_id="s", family="bgp", template_id="bgp_prefix_withdrawal", version=1,
                parameters={"target_node": target, "target_session": "a-b", "prefix": prefix},
                timeouts=_mini_timeouts()))
    with pytest.raises(ScenarioValidationError, match="malformed CIDR"):
        validate_scenario_case(case("10.255.0.1"), topo)  # no length
    with pytest.raises(ScenarioValidationError, match="advertised loopback"):
        validate_scenario_case(case("10.255.0.2/32"), topo)  # router_a doesn't own .2


# --- full offline matrix ------------------------------------------------------


@pytest.mark.parametrize("case", SCENARIO_CATALOG, ids=[c.case_id for c in SCENARIO_CATALOG])
def test_every_catalog_case_runs_offline(
    case: ScenarioCase, tmp_path: Path, run_catalog_case
) -> None:
    out_root = tmp_path / "runs"
    result = run_catalog_case(case, out_root, tmp_path)
    record = result.assembled.loaded.incident
    assert record.status == "accepted"
    assert record.scenario.template_id == case.template_id
    assert str(record.scenario.parameters["target_node"]) == case.expected_target
    assert record.ground_truth is not None
    assert record.ground_truth.root_cause_label == case.template_id
    assert record.fault is not None and record.fault.target_node == case.expected_target
    assert all(v.committable for v in record.ground_truth.verdicts)
    reloaded = load_verified_run_from_index(out_root, result.assembled.run_id)
    assert reloaded.incident == record


def test_all_catalog_cases_share_one_index(tmp_path: Path, run_catalog_case) -> None:
    out_root = tmp_path / "runs"
    for case in SCENARIO_CATALOG:
        run_catalog_case(case, out_root, tmp_path)
    index = load_run_index(out_root)
    assert len(index.entries) == len(SCENARIO_CATALOG)
    assert verify_run_index(out_root).verified is True
    by_run = {f"run-{c.case_id}": c for c in SCENARIO_CATALOG}
    for entry in index.entries:
        case = by_run[entry.run_id]
        assert entry.template_id == case.template_id
        loaded = load_verified_run_from_index(out_root, entry.run_id)
        assert str(loaded.incident.scenario.parameters["target_node"]) == case.expected_target
