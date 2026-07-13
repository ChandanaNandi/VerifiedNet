"""Failure-path tests for run artifacts: tamper detection and loud writer errors."""

from __future__ import annotations

import json

import pytest

from verifiednet.artifacts import (
    ArtifactIntegrityError,
    ArtifactWriteError,
    load_run,
    verify_run_dir,
    write_run_artifacts,
)
from verifiednet.artifacts.layout import HASH_INDEX_FILE, INCOMPLETE_MARKER

pytestmark = pytest.mark.failure


def _failed(result) -> set:
    return {c.rule for c in result.failures}


def test_tampered_incident_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    p = wr.root / "incident.json"
    p.write_bytes(p.read_bytes().replace(b'"clean"', b'"unknown"'))
    result = verify_run_dir(wr.root)
    assert not result.verified
    assert "hash_matches:incident.json" in _failed(result)
    with pytest.raises(ArtifactIntegrityError):
        load_run(wr.root)


def test_tampered_evidence_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    p = wr.root / "evidence/onset.json"
    p.write_bytes(p.read_bytes().replace(b"Idle", b"Xdle"))
    assert not verify_run_dir(wr.root).verified


def test_tampered_transcript_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    p = wr.root / "transcript.jsonl"
    p.write_bytes(p.read_bytes().replace(b"router_a", b"router_b"))
    assert not verify_run_dir(wr.root).verified


def test_tampered_ledger_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    p = wr.root / "ledger.jsonl"
    p.write_bytes(p.read_bytes().replace(b"recovery_verified", b"restored"))
    assert not verify_run_dir(wr.root).verified


def test_missing_file_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    (wr.root / "evidence/recovery.json").unlink()
    result = verify_run_dir(wr.root)
    assert not result.verified
    assert "file_present:evidence/recovery.json" in _failed(result)


def test_extra_unindexed_file_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    (wr.root / "stray.json").write_bytes(b"{}")
    result = verify_run_dir(wr.root)
    assert not result.verified
    assert "no_unindexed_files" in _failed(result)


def test_incomplete_marker_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    (wr.root / INCOMPLETE_MARKER).write_bytes(b"x")
    result = verify_run_dir(wr.root)
    assert not result.verified
    assert "no_incomplete_marker" in _failed(result)


def test_wrong_run_dir_name_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    renamed = wr.root.parent / "wrong-name"
    wr.root.rename(renamed)
    result = verify_run_dir(renamed)
    assert not result.verified
    assert "dir_name_equals_run_id" in _failed(result)


def test_tampered_run_digest_detected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    idx = wr.root / HASH_INDEX_FILE
    data = json.loads(idx.read_text())
    data["run_digest"] = "f" * 64
    idx.write_bytes(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
    result = verify_run_dir(wr.root)
    assert not result.verified
    # the hashes.json itself is not indexed, so its own hash isn't checked; the
    # digest recomputation catches the tamper.
    assert "run_digest_matches" in _failed(result)


def test_existing_target_directory_rejected(accepted_run_inputs, write_inputs, tmp_path) -> None:
    write_inputs(accepted_run_inputs, tmp_path)
    with pytest.raises(ArtifactWriteError, match="already exists"):
        write_inputs(accepted_run_inputs, tmp_path)  # same run_id, non-empty dir


def test_run_id_manifest_incident_mismatch_rejected(
    accepted_run_inputs, rejected_run_inputs, tmp_path
) -> None:
    # incident.run_id must equal run_manifest.run_id
    with pytest.raises(ArtifactWriteError, match="run_id"):
        write_run_artifacts(
            out_root=tmp_path,
            run_manifest=rejected_run_inputs.run_manifest,  # run-test-rej1
            environment_manifest=accepted_run_inputs.environment_manifest,
            incident=accepted_run_inputs.incident,  # run-test-acc1
            transcript_entries=(),
            ledger_records=(),
        )


def test_writer_leaves_incomplete_on_failure(accepted_run_inputs, tmp_path, monkeypatch) -> None:
    # Force verification to fail after writing: patch verify to report unverified.
    import verifiednet.artifacts.writer as writer_mod

    class _Bad:
        verified = False
        failures = ()

    monkeypatch.setattr(writer_mod, "verify_run_dir", lambda *a, **k: _Bad())
    with pytest.raises(ArtifactIntegrityError):
        write_run_artifacts(
            out_root=tmp_path,
            run_manifest=accepted_run_inputs.run_manifest,
            environment_manifest=accepted_run_inputs.environment_manifest,
            incident=accepted_run_inputs.incident,
            transcript_entries=accepted_run_inputs.transcript_entries,
            ledger_records=accepted_run_inputs.ledger_records,
        )
    root = tmp_path / "run-test-acc1"
    assert (root / INCOMPLETE_MARKER).exists()  # marker preserved for diagnosis
    assert not (root / "verification_report.json").exists()


def test_unsafe_run_id_rejected(accepted_run_inputs, tmp_path) -> None:
    # RunManifest does not constrain run_id format; the writer must reject an
    # unsafe (path-traversal) run_id before creating any directory.
    bad_manifest = accepted_run_inputs.run_manifest.model_copy(update={"run_id": "../escape"})
    with pytest.raises(ArtifactWriteError, match="unsafe run_id"):
        write_run_artifacts(
            out_root=tmp_path, run_manifest=bad_manifest,
            environment_manifest=accepted_run_inputs.environment_manifest,
            incident=accepted_run_inputs.incident, transcript_entries=(), ledger_records=(),
        )


def test_accepted_missing_recovery_rejected(accepted_run_inputs, tmp_path) -> None:
    # An accepted incident with no recovery evidence must be refused by the writer.
    incident_no_recovery = accepted_run_inputs.incident.model_copy(
        update={"recovery_evidence": None}
    )
    with pytest.raises(ArtifactWriteError, match="onset and recovery"):
        write_run_artifacts(
            out_root=tmp_path, run_manifest=accepted_run_inputs.run_manifest,
            environment_manifest=accepted_run_inputs.environment_manifest,
            incident=incident_no_recovery, transcript_entries=(), ledger_records=(),
        )


def test_rejected_with_mutation_transcript_refused(rejected_run_inputs, tmp_path) -> None:
    from verifiednet.runtime.invocation import CommandInvocation
    from verifiednet.runtime.transcript import TranscriptEntry

    inv = CommandInvocation(command_id="cmd-1", target="router_a",
                            logical_argv=("vtysh",), transport_argv=("docker", "compose", "x"))
    from datetime import UTC, datetime

    mut = TranscriptEntry(seq=1, mode="mutation", stage="pending", target="router_a",
                          argv=("docker",), status="pending",
                          started_at=datetime(2026, 1, 1, tzinfo=UTC), invocation=inv)
    with pytest.raises(ArtifactIntegrityError):  # internal verify: rejected_zero_mutation
        write_run_artifacts(
            out_root=tmp_path, run_manifest=rejected_run_inputs.run_manifest,
            environment_manifest=rejected_run_inputs.environment_manifest,
            incident=rejected_run_inputs.incident, transcript_entries=(mut,), ledger_records=(),
        )
    assert (tmp_path / "run-test-rej1" / INCOMPLETE_MARKER).exists()


def test_unmatched_mutation_pending_refused(accepted_run_inputs, tmp_path) -> None:
    # drop the completed mutation entry -> internal verify fails mutation pairing
    pending_only = tuple(
        e for e in accepted_run_inputs.transcript_entries
        if not (e.mode == "mutation" and e.stage == "completed")
    )
    with pytest.raises(ArtifactIntegrityError):
        write_run_artifacts(
            out_root=tmp_path, run_manifest=accepted_run_inputs.run_manifest,
            environment_manifest=accepted_run_inputs.environment_manifest,
            incident=accepted_run_inputs.incident, transcript_entries=pending_only,
            ledger_records=accepted_run_inputs.ledger_records,
        )


def test_illegal_ledger_transition_refused(accepted_run_inputs, tmp_path) -> None:
    from datetime import UTC, datetime

    from verifiednet.faults.ledger import LedgerRecord, LifecyclePhase

    # PENDING -> RECOVERY_VERIFIED is not a legal transition
    bad = (
        LedgerRecord(seq=1, phase=LifecyclePhase.RECOVERY_VERIFIED,
                     at=datetime(2026, 1, 1, tzinfo=UTC), detail=""),
    )
    with pytest.raises(ArtifactIntegrityError):
        write_run_artifacts(
            out_root=tmp_path, run_manifest=accepted_run_inputs.run_manifest,
            environment_manifest=accepted_run_inputs.environment_manifest,
            incident=accepted_run_inputs.incident,
            transcript_entries=accepted_run_inputs.transcript_entries, ledger_records=bad,
        )
