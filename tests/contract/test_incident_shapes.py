"""Contract tests: incident/ground-truth/manifest JSON shapes round-trip."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.common.runctx import RunContext
from verifiednet.incidents.oracle import ORACLE_VERSION, build_ground_truth
from verifiednet.schemas import (
    EnvironmentManifest,
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    FaultInjection,
    GroundTruth,
    IncidentRecord,
    Phase,
    ProvenanceInfo,
    RejectionCode,
    RejectionInfo,
    RestorationMetadata,
    RunManifest,
    ScenarioDefinition,
    TopologySpec,
    Verdict,
    VerificationResult,
)

pytestmark = pytest.mark.contract

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def mk_bundle(run_ctx: RunContext, phase: Phase, *, sealed: bool = True) -> EvidenceBundle:
    normalized = {"bgp.peer.172.30.0.2.state": "Idle"}
    payload = json.dumps(normalized, sort_keys=True)
    seq = run_ctx.next_seq()
    record = EvidenceRecord(
        evidence_id=run_ctx.content_id("ev", {"phase": phase, "seq": seq}),
        phase=phase,
        source=EvidenceSource(collector="fake.collector", target="router_a", trusted=True),
        raw_sha256=sha256_bytes(payload.encode("utf-8")),
        raw_payload=payload,
        normalized=normalized,
        captured_at=EPOCH,
        run_seq=seq,
    )
    return EvidenceBundle(
        bundle_id=run_ctx.content_id("bundle", {"phase": phase}),
        phase=phase,
        records=(record,),
        sealed=sealed,
    )


def mk_fault() -> FaultInjection:
    return FaultInjection(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        template_id="bgp_remote_as_mismatch",
        target_node="router_a",
        target_session="a-b",
        method="vtysh-remote-as",
        parameter_name="remote_as",
        before_value="65002",
        after_value="65999",
        transcript_refs=(1,),
        injected_at_seq=5,
        injected_at=EPOCH,
    )


def mk_result(check_id: str = "bgp_not_established:router_a:x:onset") -> VerificationResult:
    return VerificationResult(
        check_id=check_id,
        verdict=Verdict.PASS,
        phase="onset",
        evidence_ids=("ev-abc",),
        observed=("Idle",),
        evaluated_at_seq=6,
        evaluated_at=EPOCH,
    )


def mk_provenance() -> ProvenanceInfo:
    return ProvenanceInfo(
        generator="verifiednet.faults.bgp_remote_as_mismatch",
        generator_version="0.1.0",
        code_commit="deadbeef",
    )


def mk_accepted(
    run_ctx: RunContext, topology: TopologySpec, scenario: ScenarioDefinition
) -> IncidentRecord:
    fault = mk_fault()
    ground_truth = build_ground_truth(
        fault=fault,
        verdicts=(mk_result(),),
        accepted_evidence_ids=("ev-abc",),
        root_cause_label="bgp_remote_as_mismatch",
    )
    return IncidentRecord(
        incident_id="inc-0000000000000001",
        run_id=run_ctx.run_id,
        scenario=scenario,
        backend=topology.backend,
        topology=topology,
        topology_hash=sha256_canonical(topology),
        fault=fault,
        ground_truth=ground_truth,
        baseline_evidence=mk_bundle(run_ctx, "baseline"),
        onset_evidence=mk_bundle(run_ctx, "onset"),
        recovery_evidence=mk_bundle(run_ctx, "recovery"),
        precondition_results=(mk_result("pre:router_a:x:precondition"),),
        onset_results=(mk_result(),),
        recovery_results=(mk_result("rec:router_a:x:recovery"),),
        restoration=RestorationMetadata(
            method="vtysh-remote-as-revert",
            forced_reset_used=True,
            forced_reset_command="clear bgp 172.30.0.2",
            transcript_refs=(2, 3),
            completed=True,
        ),
        provenance=mk_provenance(),
        oracle_version=ORACLE_VERSION,
        completed_phases=("precondition", "inject", "onset", "restore", "recovery"),
        cleanup_status="clean",
        created_at=EPOCH,
        status="accepted",
    )


def mk_rejected(
    run_ctx: RunContext, topology: TopologySpec, scenario: ScenarioDefinition
) -> IncidentRecord:
    return IncidentRecord(
        incident_id="inc-0000000000000002",
        run_id=run_ctx.run_id,
        scenario=scenario,
        backend=topology.backend,
        topology=topology,
        topology_hash=sha256_canonical(topology),
        baseline_evidence=mk_bundle(run_ctx, "baseline"),
        provenance=mk_provenance(),
        completed_phases=(),
        cleanup_status="clean",
        created_at=EPOCH,
        status="rejected",
        rejection=RejectionInfo(
            code=RejectionCode.PRECONDITION_FAILED,
            details="bgp session down at baseline",
            failed_phase="precondition",
        ),
    )


def test_accepted_record_round_trips(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    record = mk_accepted(run_ctx, two_router_topology, scenario)
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert reparsed.status == "accepted"
    assert reparsed.ground_truth is not None


def test_rejected_record_round_trips(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    record = mk_rejected(run_ctx, two_router_topology, scenario)
    reparsed = IncidentRecord.model_validate_json(record.model_dump_json())
    assert reparsed == record
    assert reparsed.rejection is not None
    assert reparsed.rejection.code is RejectionCode.PRECONDITION_FAILED


def test_schema_version_is_one_everywhere(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    record = mk_accepted(run_ctx, two_router_topology, scenario)
    dumped: dict[str, Any] = json.loads(record.model_dump_json())
    assert dumped["schema_version"] == 1
    assert dumped["scenario"]["schema_version"] == 1
    assert dumped["topology"]["schema_version"] == 1
    assert dumped["fault"]["schema_version"] == 1
    assert dumped["ground_truth"]["schema_version"] == 1
    assert dumped["baseline_evidence"]["schema_version"] == 1
    assert dumped["baseline_evidence"]["records"][0]["schema_version"] == 1
    for result in dumped["precondition_results"]:
        assert result["schema_version"] == 1


def test_ground_truth_requires_at_least_one_verdict() -> None:
    with pytest.raises(ValidationError):
        GroundTruth(
            oracle_version=ORACLE_VERSION,
            fault=mk_fault(),
            verdicts=(),
            root_cause_label="bgp_remote_as_mismatch",
        )
    with pytest.raises(ValueError, match="at least one"):
        build_ground_truth(
            fault=mk_fault(),
            verdicts=(),
            accepted_evidence_ids=(),
            root_cause_label="bgp_remote_as_mismatch",
        )


def test_ground_truth_rejects_free_text_label() -> None:
    with pytest.raises(ValidationError, match="machine label"):
        GroundTruth(
            oracle_version=ORACLE_VERSION,
            fault=mk_fault(),
            verdicts=(mk_result(),),
            root_cause_label="the bgp session went down",
        )


def test_ground_truth_round_trips() -> None:
    truth = build_ground_truth(
        fault=mk_fault(),
        verdicts=(mk_result(),),
        accepted_evidence_ids=("ev-abc",),
        root_cause_label="bgp_remote_as_mismatch",
    )
    assert truth.oracle_version == ORACLE_VERSION
    assert GroundTruth.model_validate_json(truth.model_dump_json()) == truth


def test_run_manifest_round_trips(two_router_topology: TopologySpec) -> None:
    manifest = RunManifest(
        run_id="run-test-0001",
        git_rev="deadbeef",
        lock_hash="a" * 64,
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        template_id="bgp_remote_as_mismatch",
        topology_hash=sha256_canonical(two_router_topology),
        started_at=EPOCH,
    )
    reparsed = RunManifest.model_validate_json(manifest.model_dump_json())
    assert reparsed == manifest
    assert reparsed.schema_version == 1
    assert reparsed.acceptance_status == "incomplete"


def test_environment_manifest_round_trips() -> None:
    manifest = EnvironmentManifest(
        os_name="Linux",
        kernel="6.8.0",
        arch="x86_64",
        python_version="3.12.4",
        container_runtime="docker",
        container_runtime_version="27.0.3",
        image_reference="frrouting/frr:v8.4.1@sha256:" + "c" * 64,
        captured_at=EPOCH,
    )
    reparsed = EnvironmentManifest.model_validate_json(manifest.model_dump_json())
    assert reparsed == manifest
    assert reparsed.schema_version == 1


def test_rejection_codes_enumerate_expected_set() -> None:
    assert {code.value for code in RejectionCode} == {
        "precondition_failed",
        "inject_failed",
        "onset_not_verified",
        "restore_failed",
        "recovery_not_verified",
        "transcript_incomplete",
        "evidence_insufficient",
        "evidence_contradictory",
        "internal_error",
    }
