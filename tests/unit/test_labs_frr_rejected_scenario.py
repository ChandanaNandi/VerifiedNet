"""Offline tests for the deliberately-rejected precondition run (no Docker).

A healthy read-only ``HealthySim`` answers the collectors; the impossible route
``203.0.113.99/32`` is reported absent, so the ``route_present`` check FAILs
deterministically and the adapter builds an honest rejected record — with the
ledger left at ``PENDING`` and a mutation spy never called.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from verifiednet.common.errors import ParserError, PhaseTransitionError
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.incidents.manifests import incident_to_json_bytes
from verifiednet.labs.frr.rejected_scenario import (
    DEFAULT_IMPOSSIBLE_PREFIX,
    ImpossiblePreconditionSatisfiedError,
    NonDeterministicRejectionError,
    RejectedPreconditionRun,
)
from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.schemas import IncidentRecord, ProvenanceInfo, ScenarioDefinition, ScenarioTimeouts
from verifiednet.schemas.evidence import EvidenceBundle, Phase
from verifiednet.verifiers import checks
from verifiednet.verifiers.claims import ClaimVerifier

pytestmark = pytest.mark.unit

IMPOSSIBLE = DEFAULT_IMPOSSIBLE_PREFIX


def scenario_def() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": 65999, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0, command_s=10.0, poll_interval_s=1.0
        ),
    )


class HealthySim:
    """Read-only healthy FRR simulator (route to IMPOSSIBLE is always absent)."""

    def __init__(self, *, present_impossible: bool = False, malformed_routes: bool = False,
                 raise_on_routes: bool = False) -> None:
        self.present_impossible = present_impossible
        self.malformed_routes = malformed_routes
        self.raise_on_routes = raise_on_routes
        self._seq = 0

    def _bgp(self, service: str) -> str:
        peer = "172.30.0.2" if service == "router_a" else "172.30.0.1"
        remote = 65002 if service == "router_a" else 65001
        return json.dumps(
            {"ipv4Unicast": {"as": 65001 if service == "router_a" else 65002,
                             "peers": {peer: {"state": "Established", "remoteAs": remote}}}}
        )

    def _routes(self, service: str) -> str:
        table: dict[str, list[dict[str, object]]] = {
            "10.255.0.1/32": [{"protocol": "connected" if service == "router_a" else "bgp"}],
            "10.255.0.2/32": [{"protocol": "bgp" if service == "router_a" else "connected"}],
        }
        if self.present_impossible:
            table[IMPOSSIBLE] = [{"protocol": "static"}]
        return json.dumps(table)

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        self._seq += 1
        logical = list(argv)
        cmd = logical[-1]
        if logical[0] == "ping":
            out = "1 received"
        elif cmd == "show ip bgp summary json":
            out = self._bgp(target)
        elif cmd == "show interface json":
            out = json.dumps({"eth1": {"administrativeStatus": "up", "operationalStatus": "up"},
                              "lo": {"administrativeStatus": "up", "operationalStatus": "up"}})
        elif cmd == "show ip route json":
            if self.raise_on_routes:
                raise ParserError("simulated route read failure")
            out = "{not json" if self.malformed_routes else self._routes(target)
        elif cmd == "show running-config":
            out = f"hostname {target}\nrouter bgp 65001\n"
        else:
            raise AssertionError(cmd)
        return ExecResult(
            status=ExecStatus.OK, target=target, argv=tuple(argv), exit_code=0,
            stdout=out, stderr="", duration_s=0.01, seq=self._seq,
        )


class MutationSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(
        self, target: str, argv: Sequence[str], timeout_s: float
    ) -> ExecResult:  # pragma: no cover
        self.calls.append((target, tuple(argv)))
        raise AssertionError("mutation must never run on the rejected path")


def make_run(sim: HealthySim, run_ctx: RunContext, ledger: Ledger) -> RejectedPreconditionRun:
    return RejectedPreconditionRun(
        executor=sim,
        topology=two_router_frr_topology(),
        scenario=scenario_def(),
        run_ctx=run_ctx,
        ledger=ledger,
        verifier=ClaimVerifier(run_ctx),
        target_node="router_a",
        peer_node="router_b",
    )


def provenance() -> ProvenanceInfo:
    return ProvenanceInfo(
        generator="verifiednet.labs.frr.rejected_scenario",
        generator_version="0.1.0",
        code_commit="offline",
    )


# --- verifier-level determinism ---------------------------------------------


def test_impossible_route_check_fails_deterministically(run_ctx: RunContext) -> None:
    sim = HealthySim()
    run = make_run(sim, run_ctx, Ledger(run_ctx))
    baseline = run.collect_baseline()
    result = ClaimVerifier(run_ctx).verify(
        checks.route_present("router_a", IMPOSSIBLE, Phase.PRECONDITION), (baseline,)
    )
    from verifiednet.schemas.verification import Verdict

    assert result.verdict is Verdict.FAIL
    assert result.observed == ("false",)


def test_empty_evidence_is_insufficient_not_fail(run_ctx: RunContext) -> None:
    from verifiednet.schemas.verification import Verdict

    empty = EvidenceBundle(bundle_id="b", phase=Phase.PRECONDITION, records=()).seal()
    result = ClaimVerifier(run_ctx).verify(
        checks.route_present("router_a", IMPOSSIBLE, Phase.PRECONDITION), (empty,)
    )
    assert result.verdict is Verdict.INSUFFICIENT
    assert not result.committable


# --- the rejected run --------------------------------------------------------


def test_execute_builds_rejected_record_and_keeps_ledger_pending(
    run_ctx: RunContext,
) -> None:
    sim = HealthySim()
    ledger = Ledger(run_ctx)
    spy = MutationSpy()
    record = make_run(sim, run_ctx, ledger).execute(provenance=provenance())

    assert ledger.current is LifecyclePhase.PENDING  # never PRECHECKED
    assert spy.calls == []  # mutation spy untouched
    assert record.status == "rejected"
    assert record.rejection is not None
    assert record.rejection.code.value == "precondition_failed"
    assert record.rejection.failed_phase == "precondition"
    assert IMPOSSIBLE in record.rejection.details
    assert record.fault is None
    assert record.ground_truth is None
    assert record.onset_evidence is None
    assert record.recovery_evidence is None
    assert record.restoration is None
    assert record.completed_phases == ()
    assert record.cleanup_status == "clean"
    assert record.baseline_evidence.sealed
    # baseline retains healthy facts AND the impossible-route observation
    metrics = {
        k for r in record.baseline_evidence.records for k in r.normalized
    }
    assert "bgp.peer.172.30.0.2.state" in metrics
    assert f"route.{IMPOSSIBLE}.present" in metrics
    assert len(record.precondition_results) == 1
    assert not record.precondition_results[0].committable


def test_rejected_record_round_trips_and_id_deterministic(run_ctx: RunContext) -> None:
    def build() -> IncidentRecord:
        ctx = RunContext("run-test-0001", clock=run_ctx.now)
        return make_run(HealthySim(), ctx, Ledger(ctx)).execute(provenance=provenance())

    record = build()
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert incident_to_json_bytes(record) == incident_to_json_bytes(reparsed)
    # same fixed inputs -> same deterministic incident id
    assert build().incident_id == record.incident_id
    assert record.incident_id.startswith("inc-")


def test_impossible_route_present_raises_setup_error(run_ctx: RunContext) -> None:
    sim = HealthySim(present_impossible=True)
    ledger = Ledger(run_ctx)
    with pytest.raises(ImpossiblePreconditionSatisfiedError):
        make_run(sim, run_ctx, ledger).execute(provenance=provenance())
    assert ledger.current is LifecyclePhase.PENDING  # still no rejection record


# --- failure paths: infra/evidence problems never become PRECONDITION_FAILED -


def test_malformed_route_json_propagates_not_rejection(run_ctx: RunContext) -> None:
    sim = HealthySim(malformed_routes=True)
    with pytest.raises(ParserError):
        make_run(sim, run_ctx, Ledger(run_ctx)).execute(provenance=provenance())


def test_route_read_failure_propagates(run_ctx: RunContext) -> None:
    sim = HealthySim(raise_on_routes=True)
    with pytest.raises(ParserError):
        make_run(sim, run_ctx, Ledger(run_ctx)).execute(provenance=provenance())


def test_missing_route_observation_is_not_a_rejection(run_ctx: RunContext) -> None:
    # A bundle without any route.<impossible>.present metric -> INSUFFICIENT ->
    # NonDeterministicRejectionError, never a PRECONDITION_FAILED record.
    run = make_run(HealthySim(), run_ctx, Ledger(run_ctx))

    def only_healthy() -> EvidenceBundle:
        provider = LiveScenarioEvidenceProvider(
            executor=HealthySim(), topology=two_router_frr_topology(), run_ctx=run_ctx,
            target_node="router_a", peer_node="router_b",
        )
        return provider(Phase.PRECONDITION)[0]  # no impossible-prefix record

    run.collect_baseline = only_healthy  # type: ignore[method-assign]
    with pytest.raises(NonDeterministicRejectionError):
        run.validate_preconditions()


def test_ledger_not_pending_raises(run_ctx: RunContext) -> None:
    ledger = Ledger(run_ctx)
    ledger.append(LifecyclePhase.PRECHECKED, "external")
    with pytest.raises(PhaseTransitionError):
        make_run(HealthySim(), run_ctx, ledger).execute(provenance=provenance())
