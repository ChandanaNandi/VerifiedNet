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
    two_router_frr_topology_v4,
    two_router_frr_topology_v5,
    two_router_frr_topology_v6,
)
from verifiednet.orchestrator.catalog import ScenarioCase, case_by_id
from verifiednet.schemas.topology import TopologySpec

#: The approved expansion topology variants, in deterministic order.
#: FROZEN at Gate 14's three variants: ``build_expansion_matrix`` (the v2
#: campaign) is immutable history — Gate 14B variants live in the v3 map.
EXPANSION_TOPOLOGY_FACTORIES: dict[str, Callable[[], TopologySpec]] = {
    "2r-v1": two_router_frr_topology,
    "2r-v2": two_router_frr_topology_v2,
    "2r-v3": two_router_frr_topology_v3,
}

#: Gate 14B (corpus v3): all six approved two-router variants. The three new
#: variants (v4/v5/v6) use disjoint ASNs, subnets, and loopbacks, so each has
#: a distinct ``topology_hash`` — a genuinely new identity dimension value.
GATE14B_TOPOLOGY_FACTORIES: dict[str, Callable[[], TopologySpec]] = {
    **EXPANSION_TOPOLOGY_FACTORIES,
    "2r-v4": two_router_frr_topology_v4,
    "2r-v5": two_router_frr_topology_v5,
    "2r-v6": two_router_frr_topology_v6,
}

#: Per-topology PF case-id suffix ("" is the original v1-topology pair).
_PF_SUFFIX_BY_TOPOLOGY: dict[str, str] = {
    "2r-v1": "", "2r-v2": "-t2", "2r-v3": "-t3",
    "2r-v4": "-t4", "2r-v5": "-t5", "2r-v6": "-t6",
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
    """The approved topology for one variant id; fail closed on unknown ids.

    Resolves over the FULL approved map (Gate 14 + Gate 14B variants); the
    Gate 14 matrix itself still iterates only its own frozen three-variant map.
    """
    try:
        return GATE14B_TOPOLOGY_FACTORIES[topology_id]()
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


# ---------------------------------------------------------------------------
# Gate 14B (corpus v3): the COMPLETE candidate identity pool
# ---------------------------------------------------------------------------

#: The ten approved RAS parameter combinations (Gate 14B adds alt3..alt8 —
#: six new valid ``wrong_asn`` values/orientations colliding with no approved
#: topology ASN).
GATE14B_RAS_CASE_IDS: tuple[str, ...] = (
    "ras-ref", "ras-rev", "ras-alt", "ras-alt2", "ras-alt3", "ras-alt4",
    "ras-alt5", "ras-alt6", "ras-alt7", "ras-alt8")

#: Rejected-coverage plan for v3: per topology variant, both rejected target
#: orientations, two runs each (12 abstention identities, 24 runs). The
#: Gate 6 rejected projection supports precondition-phase rejections ONLY —
#: abstention diversity comes from identities, never from unsupported codes.
GATE14B_REJECTED_RUNS_PER_IDENTITY = 2
GATE14B_REJECTED_TARGETS: tuple[str, ...] = GATE14_REJECTED_TARGETS


def build_v3_candidate_pool() -> tuple[ExpansionScenario, ...]:
    """The COMPLETE Gate 14B candidate pool, in deterministic order.

    Per approved topology variant (all six): the ten approved RAS parameter
    combinations, both NR orientations, both IF orientations, and the two
    per-topology PF cases — 96 candidate stable identities. ``planned_runs``
    is a placeholder of 1: run allocation is the identity-first planner's
    job (per-partition run rules from the frozen identity-coverage policy),
    never this pool's.
    """
    out: list[ExpansionScenario] = []
    for topology_id in sorted(GATE14B_TOPOLOGY_FACTORIES):
        suffix = _PF_SUFFIX_BY_TOPOLOGY[topology_id]
        groups: tuple[tuple[str, tuple[ScenarioCase, ...]], ...] = (
            ("bgp_remote_as_mismatch",
             tuple(case_by_id(c) for c in GATE14B_RAS_CASE_IDS)),
            ("bgp_neighbor_removal",
             (case_by_id("nr-ref"), case_by_id("nr-rev"))),
            ("iface_admin_shutdown",
             (case_by_id("if-ref"), case_by_id("if-rev"))),
            ("bgp_prefix_withdrawal",
             (case_by_id(f"pf{suffix}-ref"), case_by_id(f"pf{suffix}-rev"))),
        )
        for fault_family, cases in groups:
            for case in cases:
                out.append(ExpansionScenario(
                    case=case, topology_id=topology_id,
                    fault_family=fault_family, planned_runs=1))
    return tuple(out)
