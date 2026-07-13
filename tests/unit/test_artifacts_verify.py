"""Unit tests for the run-artifact integrity verifier (structured results)."""

from __future__ import annotations

import pytest

from verifiednet.artifacts import verify_run_dir

pytestmark = pytest.mark.unit


def _rules(result) -> dict:
    return {c.rule: c for c in result.checks}


def test_accepted_run_verifies_with_all_structural_rules(
    accepted_run_inputs, write_inputs, tmp_path
) -> None:
    wr = write_inputs(accepted_run_inputs, tmp_path)
    result = verify_run_dir(wr.root)
    assert result.verified
    rules = _rules(result)
    for rule in (
        "dir_name_equals_run_id", "scenario_id_matches", "template_id_matches",
        "topology_hash_matches", "incident_id_reconstructs", "acceptance_status_matches",
        "accepted_has_ground_truth", "accepted_has_baseline", "accepted_has_onset",
        "accepted_has_recovery", "ground_truth_evidence_resolves", "mutation_pairs_complete",
        "mutation_ids_paired", "ledger_legal_transitions", "accepted_final_recovery_verified",
        "run_digest_matches", "no_unindexed_files", "no_run_dir_path_leak",
        "transcript_no_env_dump", "no_incomplete_marker",
    ):
        assert rules[rule].passed, f"{rule}: {rules[rule].detail}"


def test_rejected_run_verifies_with_rejected_rules(
    rejected_run_inputs, write_inputs, tmp_path
) -> None:
    wr = write_inputs(rejected_run_inputs, tmp_path)
    result = verify_run_dir(wr.root)
    assert result.verified
    rules = _rules(result)
    for rule in (
        "rejected_has_no_ground_truth", "rejected_has_baseline_only",
        "rejected_precondition_evidence_resolves", "rejected_zero_mutation",
        "rejected_final_pending",
    ):
        assert rules[rule].passed, f"{rule}: {rules[rule].detail}"


def test_verification_report_written_and_matches(
    accepted_run_inputs, write_inputs, tmp_path
) -> None:
    from verifiednet.artifacts.layout import ArtifactVerificationResult

    wr = write_inputs(accepted_run_inputs, tmp_path)
    report = ArtifactVerificationResult.model_validate_json(
        (wr.root / "verification_report.json").read_bytes()
    )
    assert report.verified
    assert report.run_digest == wr.run_digest
    assert report.run_id == "run-test-acc1"


def test_missing_layout_returns_unverified(tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    result = verify_run_dir(tmp_path / "empty")
    assert not result.verified
    assert any(c.rule == "layout_parses" and not c.passed for c in result.checks)
