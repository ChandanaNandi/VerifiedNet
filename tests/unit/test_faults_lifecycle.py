"""THE CORE TEST: full BGP remote-as mismatch lifecycle with fakes only.

Drives preconditions -> inject -> verify_onset -> restore -> verify_recovery
against a scripted mutation executor and a fake evidence provider, then
assembles and round-trips the accepted IncidentRecord.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from verifiednet.common.hashing import sha256_bytes
from verifiednet.common.runctx import RunContext
from verifiednet.faults.bgp_remote_as_mismatch import BgpRemoteAsMismatchScenario
from verifiednet.faults.frr_commands import clear_bgp_argv, set_remote_as_argv
from verifiednet.faults.ledger import Ledger, LifecyclePhase
from verifiednet.incidents.builder import build_accepted_record
from verifiednet.incidents.oracle import ORACLE_VERSION, build_ground_truth
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.schemas import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    IncidentRecord,
    Phase,
    ProvenanceInfo,
    ScenarioDefinition,
    TopologySpec,
)
from verifiednet.verifiers.claims import ClaimVerifier

pytestmark = pytest.mark.unit

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

RECOVERY: list[tuple[str, dict[str, Any]]] = [
    (
        "router_a",
        {
            f"bgp.peer.{PEER_IP}.state": "Established",
            f"bgp.peer.{PEER_IP}.remote_as": "65002",
            "route.10.255.0.2/32.present": "true",
        },
    ),
    ("router_b", {"route.10.255.0.1/32.present": "true"}),
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


class FakeEvidenceProvider:
    """Phase-keyed prebuilt bundles; records which phases were requested."""

    def __init__(self, bundles: dict[Phase, EvidenceBundle]) -> None:
        self._bundles = bundles
        self.requests: list[Phase] = []

    def __call__(self, phase: Phase) -> Sequence[EvidenceBundle]:
        self.requests.append(phase)
        bundle = self._bundles.get(phase)
        return () if bundle is None else (bundle,)


class FakeMutationExec:
    """Scripted executor: records every call, returns OK unless told otherwise."""

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
) -> tuple[BgpRemoteAsMismatchScenario, Ledger, FakeMutationExec, FakeEvidenceProvider]:
    if evidence is None:
        evidence = {
            "precondition": mk_bundle(run_ctx, "precondition", HEALTHY),
            "onset": mk_bundle(run_ctx, "onset", ONSET),
            "recovery": mk_bundle(run_ctx, "recovery", RECOVERY),
        }
    mutation = mutation or FakeMutationExec()
    ledger = Ledger(run_ctx)
    provider = FakeEvidenceProvider(evidence)
    time = FakeTime()
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
    return subject, ledger, mutation, provider


def test_full_lifecycle(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    subject, ledger, mutation, provider = build_scenario(
        run_ctx, two_router_topology, scenario
    )

    pre_results = subject.validate_preconditions()
    fault = subject.inject()
    onset_results = subject.verify_onset()
    restoration = subject.restore()
    recovery_results = subject.verify_recovery()

    # Ledger walked the full legal lifecycle, in order.
    assert [record.phase for record in ledger.records] == [
        LifecyclePhase.PRECHECKED,
        LifecyclePhase.INJECTING,
        LifecyclePhase.INJECTED,
        LifecyclePhase.ONSET_VERIFIED,
        LifecyclePhase.RESTORING,
        LifecyclePhase.RESTORED,
        LifecyclePhase.RECOVERY_VERIFIED,
    ]

    # Mutation calls: inject, revert, forced reset — exact argv, right target.
    assert mutation.calls == [
        ("router_a", set_remote_as_argv(65001, PEER_IP, 65999), 10.0),
        ("router_a", set_remote_as_argv(65001, PEER_IP, 65002), 10.0),
        ("router_a", clear_bgp_argv(PEER_IP), 10.0),
    ]

    # FaultInjection captures the exact before/after values.
    assert fault.before_value == "65002"
    assert fault.after_value == "65999"
    assert fault.method == "vtysh-remote-as"
    assert fault.parameter_name == "remote_as"
    assert fault.target_node == "router_a"
    assert fault.target_session == "a-b"
    assert fault.transcript_refs == (1,)

    # Restoration metadata records the forced reset.
    assert restoration.forced_reset_used is True
    assert restoration.forced_reset_command == f"clear bgp {PEER_IP}"
    assert restoration.completed is True
    assert restoration.attempted is True
    assert restoration.failure_reason == ""
    assert restoration.transcript_refs == (2, 3)

    # Every verification result committed (PASS).
    assert len(pre_results) == 4
    assert len(onset_results) == 5  # 2 onset + iface + ping + config_unchanged
    assert len(recovery_results) == 4  # 2 recovery + both loopback routes
    for result in (*pre_results, *onset_results, *recovery_results):
        assert result.committable, f"{result.check_id}: {result.verdict} {result.detail}"

    # Evidence was pulled per phase (onset/recovery polled at least twice).
    assert provider.requests.count("precondition") == 1
    assert provider.requests.count("onset") >= 2
    assert provider.requests.count("recovery") >= 2


def test_accepted_record_round_trips_and_id_deterministic(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    subject, _, _, _ = build_scenario(run_ctx, two_router_topology, scenario)
    pre_results = subject.validate_preconditions()
    fault = subject.inject()
    onset_results = subject.verify_onset()
    restoration = subject.restore()
    recovery_results = subject.verify_recovery()

    baseline = mk_bundle(run_ctx, "baseline", HEALTHY)
    onset_bundle = mk_bundle(run_ctx, "onset", ONSET)
    recovery_bundle = mk_bundle(run_ctx, "recovery", RECOVERY)
    ground_truth = build_ground_truth(
        fault=fault,
        verdicts=(*onset_results, *recovery_results),
        accepted_evidence_ids=onset_bundle.evidence_ids,
        root_cause_label="bgp_remote_as_mismatch",
    )
    provenance = ProvenanceInfo(
        generator="verifiednet.faults.bgp_remote_as_mismatch",
        generator_version="0.1.0",
        code_commit="deadbeef",
    )

    def build(ctx: RunContext) -> IncidentRecord:
        return build_accepted_record(
            run_ctx=ctx,
            scenario=scenario,
            topology=two_router_topology,
            fault=fault,
            ground_truth=ground_truth,
            baseline=baseline,
            onset=onset_bundle,
            recovery=recovery_bundle,
            precondition_results=pre_results,
            onset_results=onset_results,
            recovery_results=recovery_results,
            restoration=restoration,
            provenance=provenance,
            completed_phases=(
                "precondition",
                "inject",
                "onset",
                "restore",
                "recovery",
            ),
            cleanup_status="clean",
        )

    record = build(run_ctx)
    assert record.status == "accepted"
    assert record.oracle_version == ORACLE_VERSION
    assert record.baseline_evidence.sealed
    assert record.onset_evidence is not None and record.onset_evidence.sealed
    assert record.recovery_evidence is not None and record.recovery_evidence.sealed
    assert record.rejection is None
    assert record.topology_hash

    # JSON round-trip is lossless.
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record

    # incident_id is content-derived: rebuilding yields the same id.
    again = build(run_ctx)
    assert again.incident_id == record.incident_id
    assert record.incident_id.startswith("inc-")
