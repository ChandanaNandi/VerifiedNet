"""Explicit, immutable fault-family bindings (Gate 5.1).

ONE frozen dataclass maps a fault family to everything the composition root
needs to run it: the scenario factory, the exact mutation shapes, the
evidence phase plans, the ground-truth root-cause label, and the provenance
generator name. Approved bindings are a hand-written tuple below — there is
NO plugin system, NO runtime registration, NO decorators, NO discovery, NO
reflection, and NO dynamic imports. Adding a family means editing this file
in a reviewed commit.

Artifact naming and the expected lifecycle are deliberately NOT per-family:
every family persists through the same canonical run layout (run_id-named
directories, ADR-0016) and must traverse the same eight-phase ledger
(``faults.ledger.LEGAL_TRANSITIONS``); a family that needed different rules
would be an architectural change requiring its own ADR.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from verifiednet.common.runctx import RunContext
from verifiednet.faults.bgp_neighbor_removal import BgpNeighborRemovalScenario
from verifiednet.faults.bgp_remote_as_mismatch import BgpRemoteAsMismatchScenario
from verifiednet.faults.ledger import Ledger
from verifiednet.faults.scenario import FaultScenario, MutationExec
from verifiednet.labs.frr.scenario_evidence import NodePlan, PhasePlans
from verifiednet.runtime.policy import (
    MutationCommandShape,
    bgp_neighbor_removal_mutation_shapes,
    bgp_remote_as_mutation_shapes,
)
from verifiednet.schemas.evidence import EvidenceBundle, Phase
from verifiednet.schemas.scenario import ScenarioDefinition
from verifiednet.schemas.topology import TopologySpec
from verifiednet.verifiers.claims import ClaimVerifier


class ScenarioFactory(Protocol):
    """Build one family's FaultScenario from the already-composed pieces."""

    def __call__(
        self,
        *,
        topology: TopologySpec,
        scenario: ScenarioDefinition,
        mutation: MutationExec,
        ledger: Ledger,
        run_ctx: RunContext,
        evidence_provider: Callable[[Phase], Sequence[EvidenceBundle]],
        verifier: ClaimVerifier,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> FaultScenario: ...


#: Build per-phase evidence plans for a family: (topology, target, peer) -> plans.
PhasePlanBuilder = Callable[[TopologySpec, str, str], PhasePlans]


@dataclass(frozen=True)
class FaultFamilyBinding:
    """Everything the composition root needs to run ONE approved fault family."""

    template_id: str
    root_cause: str
    generator: str
    build_scenario: ScenarioFactory
    mutation_shapes: Callable[[], tuple[MutationCommandShape, ...]]
    #: ``None`` = the provider's built-in default plans (the Gate 4 remote-AS
    #: plans, byte-identical evidence for that family).
    build_phase_plans: PhasePlanBuilder | None = None


def _neighbor_removal_phase_plans(
    topology: TopologySpec, target_node: str, peer_node: str
) -> PhasePlans:
    """Evidence plans for the neighbor-removal family (pure data).

    Metric-key discipline (the verifier is metric-keyed and target-blind):

    - ONSET requests each loopback prefix on exactly ONE node, so
      ``route_absent`` never sees a contradicting connected route from the
      prefix's owner;
    - config is collected on the PEER only at onset (peer-invariance check)
      and on the TARGET only at recovery (byte-identical restore check), so
      each ``config_unchanged`` sees exactly one ``config.sha256``
      observation;
    - the TARGET's BGP collection always names the removed/restored peer in
      ``expected_peers`` so presence/absence is affirmative evidence.
    """
    target = topology.node(target_node)
    peer = topology.node(peer_node)
    # The neighbor address configured on the target = the peer's link address.
    target_expected = tuple(
        sep.peer_ip
        for sess in topology.sessions
        for sep in (sess.a, sess.b)
        if sep.node == target_node
    )
    full = ("bgp", "iface", "reach", "routes", "config")
    all_loopbacks = (target.loopback, peer.loopback)
    healthy = (
        NodePlan(target_node, full, all_loopbacks, expected_peers=target_expected),
        NodePlan(peer_node, full, all_loopbacks),
    )
    return {
        Phase.PRECONDITION: healthy,
        Phase.BASELINE: healthy,
        Phase.ONSET: (
            NodePlan(
                target_node,
                ("bgp", "iface", "reach", "routes"),
                (peer.loopback,),
                expected_peers=target_expected,
            ),
            NodePlan(peer_node, ("bgp", "routes", "config"), (target.loopback,)),
        ),
        Phase.RECOVERY: (
            NodePlan(
                target_node, full, all_loopbacks, expected_peers=target_expected
            ),
            NodePlan(peer_node, ("bgp", "iface", "reach", "routes"), all_loopbacks),
        ),
    }


REMOTE_AS_MISMATCH_BINDING = FaultFamilyBinding(
    template_id="bgp_remote_as_mismatch",
    root_cause="bgp_remote_as_mismatch",
    generator="verifiednet.faults.bgp_remote_as_mismatch",
    build_scenario=BgpRemoteAsMismatchScenario,
    mutation_shapes=bgp_remote_as_mutation_shapes,
    build_phase_plans=None,  # provider default: Gate 4 plans, byte-identical
)

BGP_NEIGHBOR_REMOVAL_BINDING = FaultFamilyBinding(
    template_id="bgp_neighbor_removal",
    root_cause="bgp_neighbor_removal",
    generator="verifiednet.faults.bgp_neighbor_removal",
    build_scenario=BgpNeighborRemovalScenario,
    mutation_shapes=bgp_neighbor_removal_mutation_shapes,
    build_phase_plans=_neighbor_removal_phase_plans,
)

#: The complete, hand-maintained set of approved family bindings.
APPROVED_FAMILY_BINDINGS: tuple[FaultFamilyBinding, ...] = (
    REMOTE_AS_MISMATCH_BINDING,
    BGP_NEIGHBOR_REMOVAL_BINDING,
)


def binding_for_template(template_id: str) -> FaultFamilyBinding:
    """Resolve an approved binding by exact template id (explicit lookup)."""
    for binding in APPROVED_FAMILY_BINDINGS:
        if binding.template_id == template_id:
            return binding
    raise KeyError(f"no approved fault-family binding for template {template_id!r}")
