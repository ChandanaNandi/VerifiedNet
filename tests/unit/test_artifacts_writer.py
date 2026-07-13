"""Unit tests for the run-artifact writer (deterministic, atomic, verified)."""

from __future__ import annotations

import pytest

from verifiednet.artifacts.layout import (
    EVIDENCE_ONSET_FILE,
    EVIDENCE_RECOVERY_FILE,
    HASH_INDEX_FILE,
    INCOMPLETE_MARKER,
    VERIFICATION_REPORT_FILE,
)

pytestmark = pytest.mark.unit


def test_accepted_run_directory_shape(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    root = wr.root
    assert root.name == "run-test-acc1"
    names = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert names == {
        "layout.json", "incident.json", "run_manifest.json", "environment_manifest.json",
        "transcript.jsonl", "ledger.jsonl", "evidence/baseline.json", "evidence/onset.json",
        "evidence/recovery.json", HASH_INDEX_FILE, VERIFICATION_REPORT_FILE,
    }
    assert not (root / INCOMPLETE_MARKER).exists()  # removed after verification


def test_rejected_run_has_baseline_only(rejected_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(rejected_run_inputs, tmp_path)
    root = wr.root
    assert (root / "evidence/baseline.json").is_file()
    assert not (root / EVIDENCE_ONSET_FILE).exists()
    assert not (root / EVIDENCE_RECOVERY_FILE).exists()
    assert (root / "transcript.jsonl").read_text() == ""  # zero mutation
    assert (root / "ledger.jsonl").read_text() == ""  # ledger stayed PENDING (no records)


def test_write_is_byte_identical_for_fixed_inputs(
    make_accepted_inputs, write_inputs, tmp_path
) -> None:
    wr1 = write_inputs(make_accepted_inputs("run-test-acc1"), tmp_path / "a")
    wr2 = write_inputs(make_accepted_inputs("run-test-acc1"), tmp_path / "b")
    assert wr1.run_digest == wr2.run_digest
    for name in ("incident.json", "layout.json", "transcript.jsonl", "ledger.jsonl",
                 "evidence/onset.json"):
        assert (wr1.root / name).read_bytes() == (wr2.root / name).read_bytes(), name


def test_different_run_changes_digest(make_accepted_inputs, write_inputs, tmp_path) -> None:
    a = write_inputs(make_accepted_inputs("run-test-x1"), tmp_path / "x1")
    b = write_inputs(make_accepted_inputs("run-test-x2"), tmp_path / "x2")
    assert a.run_digest != b.run_digest


def test_hash_index_excludes_meta(accepted_run_inputs, write_inputs, tmp_path) -> None:
    from verifiednet.artifacts.layout import ArtifactHashIndex
    from verifiednet.artifacts.verify import compute_run_digest

    wr = write_inputs(accepted_run_inputs, tmp_path)
    idx = ArtifactHashIndex.model_validate_json((wr.root / HASH_INDEX_FILE).read_bytes())
    assert idx.run_digest == wr.run_digest
    assert compute_run_digest(idx.entries) == idx.run_digest
    indexed = {e.relative_path for e in idx.entries}
    assert HASH_INDEX_FILE not in indexed
    assert VERIFICATION_REPORT_FILE not in indexed
    assert "layout.json" in indexed  # layout IS truth-bearing
