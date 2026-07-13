"""Unit tests for the orchestrator manifest builders (Gate 4 Step 6).

The builders MAP already-observed values into the released manifest schemas;
they never shell out and never invent an unavailable value. These tests pin the
mapping, the required-key enforcement, and the transcript hash's stability.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from verifiednet.orchestrator.manifests import (
    build_environment_manifest,
    build_run_manifest,
    transcript_sha256,
)

EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
LATER = datetime(2025, 1, 1, 0, 5, tzinfo=UTC)

FULL_METADATA = {
    "os_name": "Darwin",
    "kernel": "25.5.0",
    "arch": "arm64",
    "python_version": "3.12.12",
    "container_runtime": "docker",
    "container_runtime_version": "29.1.3",
    "image_reference": "frrouting/frr:v8.4.1@sha256:" + "c" * 64,
    "image_manifest_digest": "sha256:" + "c" * 64,
    "platform_resolved_repo_digest": "frrouting/frr@sha256:" + "d" * 64,
    "frr_version": "8.4.1_git",
}


def test_transcript_sha256_is_deterministic_and_order_sensitive(
    accepted_run_inputs: object,
) -> None:
    entries = list(accepted_run_inputs.transcript_entries)  # type: ignore[attr-defined]
    assert transcript_sha256(entries) == transcript_sha256(entries)
    if len(entries) >= 2:
        swapped = [entries[1], entries[0], *entries[2:]]
        assert transcript_sha256(swapped) != transcript_sha256(entries)


def test_transcript_sha256_of_empty_is_stable() -> None:
    assert transcript_sha256([]) == transcript_sha256([])


def test_build_run_manifest_maps_incident_identity(accepted_run_inputs: object) -> None:
    incident = accepted_run_inputs.incident  # type: ignore[attr-defined]
    rm = build_run_manifest(
        incident=incident,
        git_rev="deadbeef",
        lock_hash="b" * 64,
        transcript_sha="a" * 64,
        started_at=EPOCH,
        finished_at=LATER,
    )
    assert rm.run_id == incident.run_id
    assert rm.scenario_id == incident.scenario.scenario_id
    assert rm.template_id == incident.scenario.template_id
    assert rm.topology_hash == incident.topology_hash
    assert rm.acceptance_status == incident.status
    assert rm.transcript_sha256 == "a" * 64
    assert rm.started_at == EPOCH
    assert rm.finished_at == LATER
    assert rm.seeds == {}  # never invented


def test_build_environment_manifest_maps_all_fields() -> None:
    env = build_environment_manifest(FULL_METADATA, captured_at=EPOCH)
    assert env.os_name == "Darwin"
    assert env.arch == "arm64"
    assert env.container_runtime_version == "29.1.3"
    assert env.frr_version == "8.4.1_git"
    assert env.captured_at == EPOCH


def test_build_environment_manifest_leaves_optional_frr_version_none() -> None:
    meta = {k: v for k, v in FULL_METADATA.items() if k != "frr_version"}
    env = build_environment_manifest(meta, captured_at=EPOCH)
    assert env.frr_version is None  # absent, not invented


@pytest.mark.parametrize(
    "missing", ["os_name", "kernel", "arch", "python_version", "container_runtime"]
)
def test_build_environment_manifest_requires_core_keys(missing: str) -> None:
    meta = {k: v for k, v in FULL_METADATA.items() if k != missing}
    with pytest.raises(ValueError, match="missing required keys"):
        build_environment_manifest(meta, captured_at=EPOCH)
