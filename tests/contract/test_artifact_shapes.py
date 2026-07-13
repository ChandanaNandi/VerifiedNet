"""Contract tests: artifact-owned schemas round-trip canonically and stay frozen."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from verifiednet.artifacts.layout import (
    LAYOUT_SCHEMA_VERSION,
    ArtifactEntry,
    ArtifactHash,
    ArtifactHashIndex,
    ArtifactRole,
    ArtifactVerificationResult,
    CheckOutcome,
    RunLayout,
)
from verifiednet.common.hashing import sha256_canonical

pytestmark = pytest.mark.contract

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def test_run_layout_round_trips() -> None:
    layout = RunLayout(
        run_id="run-test-0001", acceptance_status="accepted",
        artifacts=(
            ArtifactEntry(relative_path="layout.json", role=ArtifactRole.LAYOUT),
            ArtifactEntry(relative_path="incident.json", role=ArtifactRole.INCIDENT),
        ),
    )
    assert RunLayout.model_validate_json(layout.model_dump_json()) == layout
    assert layout.layout_schema_version == LAYOUT_SCHEMA_VERSION


def test_hash_index_round_trips_and_hashes_stably() -> None:
    index = ArtifactHashIndex(
        run_id="run-test-0001", run_digest="a" * 64,
        entries=(ArtifactHash(relative_path="incident.json", role=ArtifactRole.INCIDENT,
                              sha256="b" * 64, size=42),),
    )
    assert ArtifactHashIndex.model_validate_json(index.model_dump_json()) == index
    assert sha256_canonical(index) == sha256_canonical(index)


def test_verification_result_round_trips() -> None:
    result = ArtifactVerificationResult(
        run_id="run-test-0001", verified=True, run_digest="c" * 64,
        checks=(
            CheckOutcome(rule="x", passed=True),
            CheckOutcome(rule="y", passed=False, detail="d"),
        ),
        verified_at=EPOCH,
    )
    assert ArtifactVerificationResult.model_validate_json(result.model_dump_json()) == result
    assert result.failures == (CheckOutcome(rule="y", passed=False, detail="d"),)


def test_artifact_models_are_frozen() -> None:
    entry = ArtifactEntry(relative_path="a.json", role=ArtifactRole.INCIDENT)
    with pytest.raises((TypeError, ValueError)):
        entry.relative_path = "b.json"  # type: ignore[misc]


def test_artifact_models_forbid_extra_fields() -> None:
    with pytest.raises(ValueError):
        ArtifactEntry.model_validate(
            {"relative_path": "a.json", "role": "incident", "surprise": 1}
        )
