"""Unit tests for one-call assembly (Gate 4 Step 6).

``assemble_verified_run`` is fed already-produced run data (no Docker): it builds
both manifests, writes the canonical run directory, verifies it, adds it to the
index, verifies the index, and loads the run back THROUGH the index. These tests
drive it directly from the shared offline ``RunInputs`` fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from verifiednet.artifacts import load_verified_run_from_index, verify_run_index
from verifiednet.orchestrator import assemble_verified_run

EPOCH = datetime(2025, 1, 1, tzinfo=UTC)
LATER = datetime(2025, 1, 1, 0, 5, tzinfo=UTC)

METADATA = {
    "os_name": "Darwin",
    "kernel": "25.5.0",
    "arch": "arm64",
    "python_version": "3.12.12",
    "container_runtime": "docker",
    "container_runtime_version": "29.1.3",
    "image_reference": "frrouting/frr:v8.4.1@sha256:" + "c" * 64,
    "image_manifest_digest": "sha256:" + "c" * 64,
    "frr_version": "8.4.1_git",
}


def _assemble(inputs: object, out_root: Path) -> object:
    return assemble_verified_run(
        out_root=out_root,
        incident=inputs.incident,  # type: ignore[attr-defined]
        environment_metadata=METADATA,
        transcript_entries=inputs.transcript_entries,  # type: ignore[attr-defined]
        ledger_records=inputs.ledger_records,  # type: ignore[attr-defined]
        git_rev="deadbeef",
        lock_hash="b" * 64,
        started_at=EPOCH,
        finished_at=LATER,
    )


def test_assemble_writes_verifies_indexes_and_loads(
    accepted_run_inputs: object, tmp_path: Path
) -> None:
    assembled = _assemble(accepted_run_inputs, tmp_path)

    assert assembled.run_dir.is_dir()  # type: ignore[attr-defined]
    assert assembled.loaded.incident.status == "accepted"  # type: ignore[attr-defined]
    assert assembled.index_entry.run_id == assembled.run_id  # type: ignore[attr-defined]
    # the index it produced verifies, and the run loads back through it
    assert verify_run_index(tmp_path).verified is True
    reloaded = load_verified_run_from_index(tmp_path, assembled.run_id)  # type: ignore[attr-defined]
    assert reloaded.run_digest == assembled.run_digest  # type: ignore[attr-defined]


def test_assemble_is_deterministic_for_identical_inputs(
    make_accepted_inputs: object, tmp_path: Path
) -> None:
    # Same logical inputs written to two roots yield the same run digest.
    a = _assemble(make_accepted_inputs("run-det-1"), tmp_path / "a")  # type: ignore[operator]
    b = _assemble(make_accepted_inputs("run-det-1"), tmp_path / "b")  # type: ignore[operator]
    assert a.run_digest == b.run_digest  # type: ignore[attr-defined]


def test_assemble_two_runs_into_one_root(
    make_accepted_inputs: object, make_rejected_inputs: object, tmp_path: Path
) -> None:
    _assemble(make_accepted_inputs("run-asm-acc"), tmp_path)  # type: ignore[operator]
    _assemble(make_rejected_inputs("run-asm-rej"), tmp_path)  # type: ignore[operator]

    assert verify_run_index(tmp_path).verified is True
    assert load_verified_run_from_index(tmp_path, "run-asm-acc").incident.status == "accepted"
    assert load_verified_run_from_index(tmp_path, "run-asm-rej").incident.status == "rejected"


def test_assemble_rejects_incomplete_environment_metadata(
    accepted_run_inputs: object, tmp_path: Path
) -> None:
    bad = {k: v for k, v in METADATA.items() if k != "container_runtime"}
    with pytest.raises(ValueError, match="missing required keys"):
        assemble_verified_run(
            out_root=tmp_path,
            incident=accepted_run_inputs.incident,  # type: ignore[attr-defined]
            environment_metadata=bad,
            transcript_entries=accepted_run_inputs.transcript_entries,  # type: ignore[attr-defined]
            ledger_records=accepted_run_inputs.ledger_records,  # type: ignore[attr-defined]
            git_rev="deadbeef",
            lock_hash="b" * 64,
            started_at=EPOCH,
            finished_at=LATER,
        )
