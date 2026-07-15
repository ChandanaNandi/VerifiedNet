"""Gate 14 corpus-expansion scenario matrix (approved, bounded, deterministic).

The Gate 14 campaign needs MORE STABLE SCENARIO IDENTITIES, not more copies
of the same nine. A stable identity (and therefore a leakage ``group_id``) is
the hash of template + scenario id + orientation + parameters + topology +
backend — so this module varies exactly the dimensions the scenario system
genuinely supports:

* topology context: three approved two-router variants (different ASNs,
  addressing, and loopbacks — distinct ``topology_hash`` per variant);
* RAS parameters: additional valid ``wrong_asn`` values and orientations;
* PF parameters: per-topology loopback prefixes (a prefix must be the
  target's own advertised loopback — validated fail-closed);
* NR / IF orientations: both targets (their templates expose no further
  parameters — honestly capped, not padded with text-only duplicates).

Selection is PARTITION-BLIND by construction: the matrix is the COMPLETE
cross product of the approved dimensions, and runs-per-identity is uniform
within each fault family (chosen only to balance family example counts).
Nothing here consults the split function, model predictions, evaluation
results, or benchmark rankings — split prediction lives in the evaluation
planner, AFTER the matrix is fixed, using the production splitter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from verifiednet.labs.frr.topologies import (
    two_router_frr_topology,
    two_router_frr_topology_v2,
    two_router_frr_topology_v3,
)
from verifiednet.orchestrator.catalog import ScenarioCase, case_by_id
from verifiednet.schemas.topology import TopologySpec

#: The approved expansion topology variants, in deterministic order.
EXPANSION_TOPOLOGY_FACTORIES: dict[str, Callable[[], TopologySpec]] = {
    "2r-v1": two_router_frr_topology,
    "2r-v2": two_router_frr_topology_v2,
    "2r-v3": two_router_frr_topology_v3,
}

#: Uniform runs-per-identity per fault family — chosen ONLY to balance the
#: family example counts (RAS has twice the identities of the others), never
#: from split outcomes.
GATE14_RUNS_PER_IDENTITY: dict[str, int] = {
    "bgp_remote_as_mismatch": 4,
    "bgp_neighbor_removal": 6,
    "iface_admin_shutdown": 6,
    "bgp_prefix_withdrawal": 6,
}


def expansion_topology(topology_id: str) -> TopologySpec:
    """The approved topology for one variant id; fail closed on unknown ids."""
    try:
        return EXPANSION_TOPOLOGY_FACTORIES[topology_id]()
    except KeyError as exc:
        raise KeyError(f"unknown expansion topology: {topology_id!r}") from exc


@dataclass(frozen=True)
class ExpansionScenario:
    """One (approved case, approved topology) pair with its planned run count."""

    case: ScenarioCase
    topology_id: str
    fault_family: str
    planned_runs: int


def build_expansion_matrix() -> tuple[ExpansionScenario, ...]:
    """The COMPLETE Gate 14 cross product, in deterministic order.

    Per topology variant: four RAS cases (three approved originals + the one
    approved expansion case), both NR orientations, both IF orientations, and
    the two per-topology PF cases. 30 stable identities total.
    """
    out: list[ExpansionScenario] = []
    for topology_id in sorted(EXPANSION_TOPOLOGY_FACTORIES):
        ras = (case_by_id("ras-ref"), case_by_id("ras-rev"),
               case_by_id("ras-alt"), case_by_id("ras-alt2"))
        nr = (case_by_id("nr-ref"), case_by_id("nr-rev"))
        iface = (case_by_id("if-ref"), case_by_id("if-rev"))
        suffix = {"2r-v1": "", "2r-v2": "-t2", "2r-v3": "-t3"}[topology_id]
        pf = (case_by_id(f"pf{suffix}-ref"), case_by_id(f"pf{suffix}-rev"))
        for case in ras:
            out.append(ExpansionScenario(
                case=case, topology_id=topology_id,
                fault_family="bgp_remote_as_mismatch",
                planned_runs=GATE14_RUNS_PER_IDENTITY["bgp_remote_as_mismatch"]))
        for case in nr:
            out.append(ExpansionScenario(
                case=case, topology_id=topology_id,
                fault_family="bgp_neighbor_removal",
                planned_runs=GATE14_RUNS_PER_IDENTITY["bgp_neighbor_removal"]))
        for case in iface:
            out.append(ExpansionScenario(
                case=case, topology_id=topology_id,
                fault_family="iface_admin_shutdown",
                planned_runs=GATE14_RUNS_PER_IDENTITY["iface_admin_shutdown"]))
        for case in pf:
            out.append(ExpansionScenario(
                case=case, topology_id=topology_id,
                fault_family="bgp_prefix_withdrawal",
                planned_runs=GATE14_RUNS_PER_IDENTITY["bgp_prefix_withdrawal"]))
    return tuple(out)


#: Rejected-run expansion plan: per topology variant, two distinct rejected
#: scenario orientations, two runs each. Gate 6's rejected projection
#: supports precondition-phase rejections ONLY (a documented contract, not a
#: defect) — so rejection-code coverage honestly stays "precondition_failed"
#: and abstention diversity comes from identities, not from unsupported codes.
GATE14_REJECTED_RUNS_PER_IDENTITY = 2
GATE14_REJECTED_TARGETS: tuple[str, ...] = ("router_a", "router_b")
