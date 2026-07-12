"""Live evidence provider for the two-router BGP remote-AS scenario (Gate 4 Step 3).

Satisfies the ``evidence_provider: Callable[[Phase], Sequence[EvidenceBundle]]``
that ``BgpRemoteAsMismatchScenario`` depends on, backed by the LIVE lab. It runs
the existing Gate 3 collectors through the backend's READ-ONLY transport
executor — it never imports, constructs, or exposes the mutation executor.

Per-phase collection is tailored to what the frozen Gate 3 checks read, because
``ClaimVerifier`` matches evidence by metric key across ALL records in the
bundle (it does not filter by target). In particular the ONSET bundle carries a
``config.sha256`` for the PEER node only, so ``config_unchanged`` evaluates the
peer's hash and nothing else — mirroring the Gate 3 fake-lifecycle shape that is
proven by ``tests/unit/test_faults_lifecycle.py``.

Truth source for the configured remote-AS (Gate 4 Step 6, verified live): FRR
8.4.1 ``show ip bgp summary json`` keeps the peer entry and reports the
CONFIGURED ``remoteAs`` even while the session is ``Idle`` after the wrong-AS
mutation, so the existing ``BgpSummaryCollector`` metric
``bgp.peer.<ip>.remote_as`` is a deterministic observation — no new parser and
no model/string guess is involved.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass

from verifiednet.collectors.base import ReadOnlyExec
from verifiednet.collectors.frr import (
    BgpSummaryCollector,
    InterfaceStateCollector,
    ReachabilityCollector,
    RoutePresenceCollector,
    RunningConfigCollector,
)
from verifiednet.common.runctx import RunContext
from verifiednet.schemas.evidence import EvidenceBundle, EvidenceRecord, Phase
from verifiednet.schemas.topology import TopologySpec

# Which collectors to run for a node in a given phase.
_BGP = "bgp"
_IFACE = "iface"
_REACH = "reach"
_ROUTES = "routes"
_CONFIG = "config"


@dataclass(frozen=True)
class _NodePlan:
    """Collection plan for one node in one phase."""

    node: str
    collectors: tuple[str, ...]
    route_prefixes: tuple[str, ...]


class LiveScenarioEvidenceProvider:
    """Phase-keyed live evidence for the two-router remote-AS scenario.

    Callable: ``provider(phase) -> (sealed EvidenceBundle,)``.
    """

    def __init__(
        self,
        *,
        executor: ReadOnlyExec,
        topology: TopologySpec,
        run_ctx: RunContext,
        target_node: str,
        peer_node: str,
        command_timeout_s: float = 10.0,
    ) -> None:
        self._executor = executor
        self._topology = topology
        self._run_ctx = run_ctx
        self._target_node = target_node
        self._peer_node = peer_node
        self._timeout_s = command_timeout_s

        # Per-node link peer address (ping destination) and both loopbacks.
        self._reach_dst = {
            node.name: self._link_peer_ip(node.name) for node in topology.nodes
        }
        self._all_loopbacks = tuple(node.loopback for node in topology.nodes)

    # -- topology helpers ---------------------------------------------------

    def _link_peer_ip(self, node_name: str) -> str:
        for link in self._topology.links:
            for mine, theirs in ((link.a, link.b), (link.b, link.a)):
                if mine.node == node_name:
                    return str(ipaddress.ip_interface(theirs.ip).ip)
        raise ValueError(f"node {node_name!r} has no link endpoint")

    # -- phase plans --------------------------------------------------------

    def _plans(self, phase: Phase) -> tuple[_NodePlan, ...]:
        target, peer = self._target_node, self._peer_node
        full = (_BGP, _IFACE, _REACH, _ROUTES, _CONFIG)
        if phase in (Phase.PRECONDITION, Phase.BASELINE, Phase.RECOVERY):
            # Healthy/recovered: full observation of both nodes; routes cover
            # both loopbacks so both directions are provable.
            return (
                _NodePlan(target, full, self._all_loopbacks),
                _NodePlan(peer, full, self._all_loopbacks),
            )
        if phase is Phase.ONSET:
            # Target: session + link health + peer-loopback withdrawal (routes
            # cover only the peer loopback, so the withdrawal is visible).
            # Peer: config hash ONLY, so config_unchanged evaluates the peer's
            # hash and nothing else (verifier is metric-keyed, target-blind).
            peer_loopback = self._topology.node(self._peer_node).loopback
            return (
                _NodePlan(target, (_BGP, _IFACE, _REACH, _ROUTES), (peer_loopback,)),
                _NodePlan(peer, (_CONFIG,), ()),
            )
        raise ValueError(f"unsupported phase: {phase!r}")

    # -- collection ---------------------------------------------------------

    def _collect_node(self, plan: _NodePlan, phase: Phase) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        for name in plan.collectors:
            if name == _BGP:
                collector: object = BgpSummaryCollector(
                    self._executor, plan.node, self._run_ctx, self._timeout_s
                )
            elif name == _IFACE:
                collector = InterfaceStateCollector(
                    self._executor, plan.node, self._run_ctx, self._timeout_s
                )
            elif name == _REACH:
                collector = ReachabilityCollector(
                    self._executor,
                    plan.node,
                    self._run_ctx,
                    dst_ip=self._reach_dst[plan.node],
                    timeout_s=self._timeout_s,
                )
            elif name == _ROUTES:
                collector = RoutePresenceCollector(
                    self._executor,
                    plan.node,
                    self._run_ctx,
                    prefixes=plan.route_prefixes,
                    timeout_s=self._timeout_s,
                )
            elif name == _CONFIG:
                collector = RunningConfigCollector(
                    self._executor, plan.node, self._run_ctx, self._timeout_s
                )
            else:  # pragma: no cover - guarded by _plans
                raise AssertionError(f"unknown collector {name!r}")
            records.append(collector.collect(phase))  # type: ignore[attr-defined]
        return records

    def __call__(self, phase: Phase) -> Sequence[EvidenceBundle]:
        records: list[EvidenceRecord] = []
        for plan in self._plans(phase):
            records.extend(self._collect_node(plan, phase))
        bundle_id = self._run_ctx.content_id(
            "bundle",
            {"phase": str(phase), "evidence": [r.evidence_id for r in records]},
        )
        bundle = EvidenceBundle(bundle_id=bundle_id, phase=phase, records=tuple(records))
        return (bundle.seal(),)
