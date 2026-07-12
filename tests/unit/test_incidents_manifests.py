"""Unit tests for manifest writers and canonical incident serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from verifiednet.common.hashing import sha256_canonical, sha256_file
from verifiednet.common.runctx import RunContext
from verifiednet.incidents.manifests import (
    incident_to_json_bytes,
    write_environment_manifest,
    write_run_manifest,
)
from verifiednet.schemas import (
    EnvironmentManifest,
    EvidenceBundle,
    IncidentRecord,
    ProvenanceInfo,
    RejectionCode,
    RejectionInfo,
    RunManifest,
    ScenarioDefinition,
    TopologySpec,
)

pytestmark = pytest.mark.unit

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def mk_run_manifest(topology: TopologySpec) -> RunManifest:
    return RunManifest(
        run_id="run-test-0001",
        git_rev="deadbeef",
        lock_hash="a" * 64,
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        template_id="bgp_remote_as_mismatch",
        topology_hash=sha256_canonical(topology),
        image_digests={"frr": "sha256:" + "b" * 64},
        seeds={"scenario": 7},
        started_at=EPOCH,
        finished_at=EPOCH,
        acceptance_status="accepted",
    )


def mk_env_manifest() -> EnvironmentManifest:
    return EnvironmentManifest(
        os_name="Linux",
        kernel="6.8.0",
        arch="x86_64",
        python_version="3.12.4",
        container_runtime="docker",
        container_runtime_version="27.0.3",
        image_reference="frrouting/frr:v8.4.1@sha256:" + "c" * 64,
        frr_version="8.4.1",
        captured_at=EPOCH,
    )


def test_write_run_manifest_returns_sha_of_file_bytes(
    tmp_path: Path, two_router_topology: TopologySpec
) -> None:
    path = tmp_path / "run_manifest.json"
    digest = write_run_manifest(mk_run_manifest(two_router_topology), path)
    assert path.exists()
    assert digest == sha256_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-test-0001"
    assert data["schema_version"] == 1
    assert data["started_at"] == "2026-01-01T00:00:00Z"
    assert list(data) == sorted(data)  # canonical: sorted keys


def test_write_environment_manifest_returns_sha_of_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "environment_manifest.json"
    digest = write_environment_manifest(mk_env_manifest(), path)
    assert digest == sha256_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["os_name"] == "Linux"
    assert list(data) == sorted(data)


def test_manifest_writes_deterministic(
    tmp_path: Path, two_router_topology: TopologySpec
) -> None:
    manifest = mk_run_manifest(two_router_topology)
    digest_one = write_run_manifest(manifest, tmp_path / "one.json")
    digest_two = write_run_manifest(manifest, tmp_path / "two.json")
    assert digest_one == digest_two
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()

    env = mk_env_manifest()
    assert write_environment_manifest(env, tmp_path / "e1.json") == write_environment_manifest(
        env, tmp_path / "e2.json"
    )


def test_write_failure_propagates(
    tmp_path: Path, two_router_topology: TopologySpec
) -> None:
    missing_dir = tmp_path / "does-not-exist" / "m.json"
    with pytest.raises(OSError):
        write_run_manifest(mk_run_manifest(two_router_topology), missing_dir)


def _rejected_record(
    run_ctx: RunContext, topology: TopologySpec, scenario: ScenarioDefinition
) -> IncidentRecord:
    baseline = EvidenceBundle(bundle_id="bundle-base", phase="baseline", sealed=True)
    return IncidentRecord(
        incident_id="inc-0000000000000000",
        run_id=run_ctx.run_id,
        scenario=scenario,
        backend=topology.backend,
        topology=topology,
        topology_hash=sha256_canonical(topology),
        baseline_evidence=baseline,
        provenance=ProvenanceInfo(
            generator="verifiednet.faults.bgp_remote_as_mismatch",
            generator_version="0.1.0",
            code_commit="deadbeef",
        ),
        created_at=EPOCH,
        status="rejected",
        rejection=RejectionInfo(
            code=RejectionCode.PRECONDITION_FAILED,
            details="bgp session down at baseline",
            failed_phase="precondition",
        ),
    )


def test_incident_canonical_bytes_stable(
    run_ctx: RunContext, two_router_topology: TopologySpec, scenario: ScenarioDefinition
) -> None:
    record = _rejected_record(run_ctx, two_router_topology, scenario)
    first = incident_to_json_bytes(record)
    second = incident_to_json_bytes(record)
    assert first == second
    parsed = json.loads(first)
    assert parsed["status"] == "rejected"
    assert parsed["rejection"]["code"] == "precondition_failed"
    assert list(parsed) == sorted(parsed)
    # Round-trip through the canonical bytes reproduces the record.
    assert IncidentRecord.model_validate_json(first) == record
