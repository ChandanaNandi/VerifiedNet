"""Unit tests for the run-artifact reader (offline load + replay validation)."""

from __future__ import annotations

import pytest

from verifiednet.artifacts import load_run
from verifiednet.artifacts.layout import INCOMPLETE_MARKER
from verifiednet.artifacts.verify import ArtifactIntegrityError

pytestmark = pytest.mark.unit


def test_load_accepted_reconstructs_incident(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    loaded = load_run(wr.root)
    assert loaded.incident == accepted_run_inputs.incident
    assert loaded.run_manifest == accepted_run_inputs.run_manifest
    assert loaded.environment_manifest == accepted_run_inputs.environment_manifest
    assert loaded.transcript == accepted_run_inputs.transcript_entries
    assert tuple(loaded.ledger) == accepted_run_inputs.ledger_records
    assert loaded.run_digest == wr.run_digest
    assert set(r.value for r in loaded.evidence) == {
        "evidence_baseline", "evidence_onset", "evidence_recovery"
    }


def test_load_rejected_has_no_ground_truth(rejected_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(rejected_run_inputs, tmp_path)
    loaded = load_run(wr.root)
    assert loaded.incident.status == "rejected"
    assert loaded.incident.ground_truth is None
    assert loaded.incident.fault is None
    assert set(r.value for r in loaded.evidence) == {"evidence_baseline"}
    assert loaded.transcript == ()
    assert loaded.ledger == ()


def test_reader_refuses_incomplete_marker(accepted_run_inputs, write_inputs, tmp_path) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    (wr.root / INCOMPLETE_MARKER).write_bytes(b"x")
    with pytest.raises(ArtifactIntegrityError, match="INCOMPLETE"):
        load_run(wr.root)


def test_reader_does_no_docker_or_process(
    accepted_run_inputs, write_inputs, tmp_path, monkeypatch
) -> None:
    # Prove the reader performs no subprocess/Docker call: sabotage subprocess.run.
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("reader must not spawn a subprocess")

    wr = write_inputs(accepted_run_inputs, tmp_path)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    loaded = load_run(wr.root)  # must succeed without touching subprocess
    assert loaded.incident.status == "accepted"
