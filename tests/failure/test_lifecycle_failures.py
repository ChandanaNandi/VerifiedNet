"""Failure-path tests for the fault lifecycle and incident record consistency."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from verifiednet.common.errors import (
    InjectFailedError,
    OnsetNotVerifiedError,
    PhaseTransitionError,
    PreconditionFailedError,
    RestoreFailedError,
)
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.common.runctx import RunContext
from verifiednet.faults.bgp_remote_as_mismatch import BgpRemoteAsMismatchScenario
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.faults.scenario import PreconditionResultsError
from verifiednet.incidents.builder import build_rejected_record
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.schemas import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    IncidentRecord,
    Phase,
    ProvenanceInfo,
    RejectionCode,
    RejectionInfo,
    RestorationMetadata,
    ScenarioDefinition,
    SealedBundleViolation,
    TopologySpec,
    Verdict,
)
from verifiednet.verifiers.claims import ClaimVerifier

pytestmark = pytest.mark.failure

BASELINE_SHA = "c0ffee" + "0" * 58
PEER_IP = "172.30.0.2"

HEALTHY: list[tuple[str, dict[str, Any]]] = [
    (
        "router_a",
        {
            f"bgp.peer.{PEER_IP}.state": "Established",
            f"bgp.peer.{PEER_IP}.remote_as": "65002",
            "iface.eth1.oper": "up",
            f"ping.{PEER_IP}.all_success": "true",
            "route.10.255.0.2/32.present": "true",
        },
    ),
    ("router_b", {"config.sha256": BASELINE_SHA}),
]

ONSET: list[tuple[str, dict[str, Any]]] = [
    (
        "router_a",
        {
            f"bgp.peer.{PEER_IP}.state": "Idle",
            f"bgp.peer.{PEER_IP}.remote_as": "65999",
            "iface.eth1.oper": "up",
            f"ping.{PEER_IP}.all_success": "true",
        },
    ),
    ("router_b", {"config.sha256": BASELINE_SHA}),
]

UNHEALTHY_BASELINE: list[tuple[str, dict[str, Any]]] = [
    (
        "router_a",
        {
            f"bgp.peer.{PEER_IP}.state": "Idle",  # session down at baseline
            f"bgp.peer.{PEER_IP}.remote_as": "65002",
            "iface.eth1.oper": "up",
            f"ping.{PEER_IP}.all_success": "true",
            "route.10.255.0.2/32.present": "true",
        },
    ),
]


def mk_bundle(
    run_ctx: RunContext, phase: Phase, data: list[tuple[str, dict[str, Any]]]
) -> EvidenceBundle:
    records = []
    for target, normalized in data:
        payload = json.dumps(normalized, sort_keys=True)
        seq = run_ctx.next_seq()
        records.append(
            EvidenceRecord(
                evidence_id=run_ctx.content_id(
                    "ev", {"phase": phase, "target": target, "n": normalized, "seq": seq}
                ),
                phase=phase,
                source=EvidenceSource(collector="fake.collector", target=target, trusted=True),
                raw_sha256=sha256_bytes(payload.encode("utf-8")),
                raw_payload=payload,
                normalized=normalized,
                captured_at=run_ctx.now(),
                run_seq=seq,
            )
        )
    return EvidenceBundle(
        bundle_id=run_ctx.content_id("bundle", {"phase": phase}),
        phase=phase,
        records=tuple(records),
    )


class FakeMutationExec:
    def __init__(self, fail_calls: dict[int, ExecStatus] | None = None) -> None:
        self.calls: list[tuple[str, tuple[str, ...], float]] = []
        self._fail_calls = fail_calls or {}

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        index = len(self.calls)
        self.calls.append((target, tuple(argv), timeout_s))
        status = self._fail_calls.get(index, ExecStatus.OK)
        ok = status is ExecStatus.OK
        return ExecResult(
            status=status,
            target=target,
            argv=tuple(argv),
            exit_code=0 if ok else 1,
            stdout="",
            stderr="" if ok else "vtysh: command failed",
            truncated=False,
            duration_s=0.01,
            seq=index + 1,
            transcript_ok=True,
            detail="" if ok else f"scripted {status}",
        )


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def build_scenario(
    run_ctx: RunContext,
    topology: TopologySpec,
    scenario: ScenarioDefinition,
    *,
    mutation: FakeMutationExec | None = None,
    evidence: dict[Phase, EvidenceBundle] | None = None,
) -> tuple[BgpRemoteAsMismatchScenario, Ledger, FakeMutationExec]:
    if evidence is None:
        evidence = {
            "precondition": mk_bundle(run_ctx, "precondition", HEALTHY),
            "onset": mk_bundle(run_ctx, "onset", ONSET),
        }
    mutation = mutation or FakeMutationExec()
    ledger = Ledger(run_ctx)
    time = FakeTime()

    def provider(phase: Phase) -> Sequence[EvidenceBundle]:
        bundle = evidence.get(phase)
        return () if bundle is None else (bundle,)

    subject = BgpRemoteAsMismatchScenario(
        topology=topology,
        scenario=scenario,
        mutation=mutation,
        ledger=ledger,
        run_ctx=run_ctx,
        evidence_provider=provider,
        verifier=ClaimVerifier(run_ctx),
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    return subject, ledger, mutation


def mk_provenance() -> ProvenanceInfo:
    return ProvenanceInfo(
        generator="verifiednet.faults.bgp_remote_as_mismatch",
        generator_version="0.1.0",
        code_commit="deadbeef",
    )


# --------------------------------------------------------- lifecycle misuse


def test_inject_before_preconditions_raises(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    subject, ledger, mutation = build_scenario(run_ctx, two_router_topology, scenario)
    with pytest.raises(PhaseTransitionError):
        subject.inject()
    assert ledger.current is LifecyclePhase.PENDING
    assert mutation.calls == []  # nothing executed


def test_inject_twice_fails_loudly(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    subject, _, mutation = build_scenario(run_ctx, two_router_topology, scenario)
    subject.validate_preconditions()
    subject.inject()
    calls_after_first = len(mutation.calls)
    with pytest.raises(PhaseTransitionError):
        subject.inject()
    assert len(mutation.calls) == calls_after_first  # no second mutation


def test_restore_twice_is_safe_noop(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    subject, ledger, mutation = build_scenario(run_ctx, two_router_topology, scenario)
    subject.validate_preconditions()
    subject.inject()
    subject.verify_onset()
    first = subject.restore()
    calls_after_first = len(mutation.calls)
    ledger_len = len(ledger.records)
    second = subject.restore()
    assert second is first  # identical metadata object, same values
    assert len(mutation.calls) == calls_after_first  # no extra mutation commands
    assert len(ledger.records) == ledger_len  # no extra ledger entries


# --------------------------------------------------------- command failures


def test_inject_command_failure(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    mutation = FakeMutationExec(fail_calls={0: ExecStatus.NONZERO_EXIT})
    subject, ledger, _ = build_scenario(
        run_ctx, two_router_topology, scenario, mutation=mutation
    )
    subject.validate_preconditions()
    with pytest.raises(InjectFailedError, match="nonzero_exit"):
        subject.inject()
    # Ledger stays visibly in INJECTING — the failure is not papered over.
    assert ledger.current is LifecyclePhase.INJECTING
    # The mutation-failure recovery path is still open: restore is legal.
    restoration = subject.restore()
    assert restoration.completed
    assert ledger.current is LifecyclePhase.RESTORED


def test_restore_failure_and_rejected_record(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    # Call 0 = inject OK, call 1 = revert fails.
    mutation = FakeMutationExec(fail_calls={1: ExecStatus.NONZERO_EXIT})
    subject, ledger, _ = build_scenario(
        run_ctx, two_router_topology, scenario, mutation=mutation
    )
    pre_results = subject.validate_preconditions()
    fault = subject.inject()
    onset_results = subject.verify_onset()
    with pytest.raises(RestoreFailedError, match="nonzero_exit"):
        subject.restore()
    assert ledger.current is LifecyclePhase.RESTORING  # visible, not hidden

    record = build_rejected_record(
        run_ctx=run_ctx,
        scenario=scenario,
        topology=two_router_topology,
        baseline=mk_bundle(run_ctx, "baseline", HEALTHY),
        rejection_code=RejectionCode.RESTORE_FAILED,
        details="restore command failed with nonzero_exit",
        failed_phase="restore",
        fault=fault,
        onset=mk_bundle(run_ctx, "onset", ONSET),
        precondition_results=pre_results,
        onset_results=onset_results,
        restoration=RestorationMetadata(
            method="vtysh-remote-as-revert",
            forced_reset_used=False,
            completed=False,
            attempted=True,
            failure_reason="revert command exited nonzero",
        ),
        provenance=mk_provenance(),
        completed_phases=("precondition", "inject", "onset"),
        cleanup_status="restore_failed",
    )
    assert record.status == "rejected"
    assert record.rejection is not None
    assert record.rejection.code is RejectionCode.RESTORE_FAILED
    assert record.ground_truth is None
    assert record.restoration is not None
    assert record.restoration.attempted and not record.restoration.completed
    assert record.fault == fault  # the injection is retained on rejection
    IncidentRecord.model_validate_json(record.model_dump_json())


# ------------------------------------------------------------- onset/pre


def test_onset_never_satisfied(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    # Onset evidence stays healthy (Established): the fault never manifests.
    evidence = {
        "precondition": mk_bundle(run_ctx, "precondition", HEALTHY),
        "onset": mk_bundle(run_ctx, "onset", [(t, dict(n)) for t, n in HEALTHY]),
    }
    subject, ledger, _ = build_scenario(
        run_ctx, two_router_topology, scenario, evidence=evidence
    )
    subject.validate_preconditions()
    subject.inject()
    with pytest.raises(OnsetNotVerifiedError):
        subject.verify_onset()
    assert ledger.current is LifecyclePhase.INJECTED  # caller restores + rejects
    restoration = subject.restore()  # cleanup path stays open
    assert restoration.completed


def test_precondition_failure_builds_rejected_record(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    baseline = mk_bundle(run_ctx, "precondition", UNHEALTHY_BASELINE)
    subject, ledger, mutation = build_scenario(
        run_ctx, two_router_topology, scenario, evidence={"precondition": baseline}
    )
    with pytest.raises(PreconditionFailedError) as excinfo:
        subject.validate_preconditions()
    assert isinstance(excinfo.value, PreconditionResultsError)
    results = excinfo.value.results
    assert "bgp_established" in str(excinfo.value)
    assert ledger.current is LifecyclePhase.PENDING
    assert mutation.calls == []  # nothing was injected

    record = build_rejected_record(
        run_ctx=run_ctx,
        scenario=scenario,
        topology=two_router_topology,
        baseline=baseline,
        rejection_code=RejectionCode.PRECONDITION_FAILED,
        details=str(excinfo.value),
        failed_phase="precondition",
        precondition_results=results,
        provenance=mk_provenance(),
        completed_phases=(),
        cleanup_status="clean",
    )
    assert record.status == "rejected"
    assert record.rejection is not None
    assert record.rejection.code is RejectionCode.PRECONDITION_FAILED
    assert record.fault is None
    assert record.completed_phases == ()
    # Baseline evidence is retained (sealed) even on rejection.
    assert record.baseline_evidence.sealed
    assert record.baseline_evidence.evidence_ids == baseline.evidence_ids
    assert len(record.precondition_results) == 4


def test_insufficient_evidence_precondition_path(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    empty = mk_bundle(run_ctx, "precondition", [])
    subject, _, _ = build_scenario(
        run_ctx, two_router_topology, scenario, evidence={"precondition": empty}
    )
    with pytest.raises(PreconditionResultsError) as excinfo:
        subject.validate_preconditions()
    assert all(result.verdict is Verdict.INSUFFICIENT for result in excinfo.value.results)
    assert not any(result.committable for result in excinfo.value.results)


# ----------------------------------------------------------- schema guards


def test_sealed_bundle_mutation_raises(run_ctx: RunContext) -> None:
    bundle = mk_bundle(run_ctx, "baseline", HEALTHY).seal()
    record = bundle.records[0]
    with pytest.raises(SealedBundleViolation):
        bundle.with_record(record)


def _accepted_kwargs(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> dict[str, Any]:
    return {
        "incident_id": "inc-0000000000000000",
        "run_id": run_ctx.run_id,
        "scenario": scenario,
        "backend": two_router_topology.backend,
        "topology": two_router_topology,
        "topology_hash": sha256_canonical(two_router_topology),
        "fault": None,
        "ground_truth": None,
        "baseline_evidence": mk_bundle(run_ctx, "baseline", HEALTHY).seal(),
        "onset_evidence": mk_bundle(run_ctx, "onset", ONSET).seal(),
        "provenance": mk_provenance(),
        "created_at": run_ctx.now(),
        "status": "accepted",
        "rejection": None,
    }


def test_accepted_record_rejects_rejection_info(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    kwargs = _accepted_kwargs(run_ctx, two_router_topology, scenario)
    kwargs["rejection"] = RejectionInfo(code=RejectionCode.INTERNAL_ERROR)
    with pytest.raises(ValidationError, match="rejection"):
        IncidentRecord(**kwargs)


def test_accepted_record_requires_ground_truth(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    kwargs = _accepted_kwargs(run_ctx, two_router_topology, scenario)
    with pytest.raises(ValidationError, match="ground truth"):
        IncidentRecord(**kwargs)


def test_accepted_record_requires_sealed_baseline(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    from verifiednet.incidents.oracle import build_ground_truth

    # Assemble an otherwise-complete accepted record with an UNSEALED baseline.
    subject, _, _ = build_scenario(run_ctx, two_router_topology, scenario)
    subject.validate_preconditions()
    fault = subject.inject()
    onset_results = subject.verify_onset()
    restoration = subject.restore()
    kwargs = _accepted_kwargs(run_ctx, two_router_topology, scenario)
    kwargs["fault"] = fault
    kwargs["ground_truth"] = build_ground_truth(
        fault=fault,
        verdicts=onset_results,
        accepted_evidence_ids=(),
        root_cause_label="bgp_remote_as_mismatch",
    )
    kwargs["restoration"] = restoration
    kwargs["baseline_evidence"] = mk_bundle(run_ctx, "baseline", HEALTHY)  # NOT sealed
    with pytest.raises(ValidationError, match="sealed"):
        IncidentRecord(**kwargs)
