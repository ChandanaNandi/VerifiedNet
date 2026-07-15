"""Gate 16A contract tests: cross-layer byte-equality with the frozen Gate 8
prompt, v1 pins, target parser round-trip, and unchanged-component pins."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from verifiednet.datasets.features import (
    DatasetFeatures,
    FeatureEvidenceRef,
    FeaturePolicy,
)
from verifiednet.evaluation import diagnosis_prompt_template, diagnosis_task
from verifiednet.training import (
    TRAINING_CANDIDATE_FAMILIES,
    contract_aligned_input_template,
    diagnosis_input_template,
    diagnosis_target_template,
)

pytestmark = pytest.mark.contract

_TASK = diagnosis_task()
_FEATURE_POLICY_ID = FeaturePolicy().policy_id

#: Pinned frozen identities (Gate 8 / Gate 10A / Gate 15). A change here is a
#: measurement-contract change and must fail loudly.
PINNED_PROMPT_TEMPLATE_ID = "prompt-93808d932655a347"
PINNED_V1_INPUT_TEMPLATE_ID = "traintmpl-d9ace87210088ece"
PINNED_TARGET_TEMPLATE_ID = "traintgt-286e4ecdff06833e"
PINNED_V1_TRAINING_POLICY_ID = "trainpolicy-47cd597b27119125"
PINNED_TASK_ID = "task-2210abdbdd7e0d1c"
PINNED_OBJECTIVE_POLICY_ID = "objpol-e5f36da1a1292f3d"
PINNED_INTERPRETATION_POLICY_ID = "interp-6a0d81d82b2b8d16"
PINNED_SUCCESS_POLICY_ID = "esucc-ab21b8d6e2ab7a70"

#: The byte-frozen Gate 10A v1 rendering for one fixed feature payload.
PINNED_V1_RENDER = (
    "You are a deterministic network fault-diagnosis classifier. You are "
    "given only observation metadata about one verified network run. Decide "
    "which fault family the observed fault belongs to, strictly from the "
    "candidate list.\n\n"
    "Candidate fault families: bgp_neighbor_removal, bgp_prefix_withdrawal, "
    "bgp_remote_as_mismatch, iface_admin_shutdown\n\n"
    "Observation metadata:\n"
    "- backend: frr-compose\n"
    "- topology_hash: " + "a" * 64 + "\n"
    "- baseline_evidence: present\n"
    "- onset_evidence: present\n\n"
    'Output: One canonical JSON object: {"fault_family": <candidate family>, '
    '"prediction_type": "diagnosis"} with keys sorted and no whitespace.'
)


def _features(*, onset: bool, backend: str = "frr-compose",
              topology_hash: str = "a" * 64) -> DatasetFeatures:
    return DatasetFeatures(
        feature_policy_id=_FEATURE_POLICY_ID, topology_hash=topology_hash,
        backend=backend,
        baseline_evidence=FeatureEvidenceRef(
            relative_path="evidence/baseline.json"),
        onset_evidence=FeatureEvidenceRef(
            relative_path="evidence/onset.json") if onset else None)


def test_v2_render_is_byte_identical_to_the_deployed_prompt() -> None:
    prompt = diagnosis_prompt_template()
    v2 = contract_aligned_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    for onset in (True, False):
        for backend in ("frr-compose", "sim", "x" * 40):
            for topology in ("a" * 64, "0" * 64, "deadbeef" * 8):
                features = _features(onset=onset, backend=backend,
                                     topology_hash=topology)
                assert v2.render(features) == prompt.render(features), (
                    onset, backend, topology)


def test_v2_mirrors_the_prompt_text_and_class_space_exactly() -> None:
    prompt = diagnosis_prompt_template()
    v2 = contract_aligned_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    assert v2.instructions == prompt.instructions
    assert v2.candidate_families == prompt.candidate_families
    assert v2.candidate_families == TRAINING_CANDIDATE_FAMILIES
    # the schema sentence appears verbatim as the rendered tail
    rendered = v2.render(_features(onset=True))
    assert rendered.endswith(prompt.response_schema)


def test_v1_render_and_identities_remain_pinned() -> None:
    assert _TASK.task_id == PINNED_TASK_ID
    v1 = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    assert v1.input_template_id == PINNED_V1_INPUT_TEMPLATE_ID
    assert v1.render(_features(onset=True)) == PINNED_V1_RENDER
    target = diagnosis_target_template(task_id=_TASK.task_id)
    assert target.target_template_id == PINNED_TARGET_TEMPLATE_ID
    from verifiednet.training import diagnosis_training_policy

    policy = diagnosis_training_policy(
        task_id=_TASK.task_id, input_template=v1, target_template=target)
    assert policy.training_data_policy_id == PINNED_V1_TRAINING_POLICY_ID


def test_v1_training_artifacts_still_parse_and_verify(
    tmp_path: Path, plan_pipeline,
) -> None:
    from verifiednet.training import verify_training_corpus

    ctx = plan_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    assert verify_training_corpus(ctx.corpus_root).verified is True


def test_target_round_trips_through_the_frozen_parser() -> None:
    from verifiednet.evaluation.prediction import DiagnosisPrediction
    from verifiednet.evaluation.slm import parse_backend_response

    target = diagnosis_target_template(task_id=_TASK.task_id)
    normalized = frozenset(
        _TASK.normalization.normalize(f) for f in TRAINING_CANDIDATE_FAMILIES)
    for family in TRAINING_CANDIDATE_FAMILIES:
        prediction = parse_backend_response(
            target.render(family), baseline_id="baseline-contract",
            task_id=_TASK.task_id,
            features_payload={"feature_policy_id": _FEATURE_POLICY_ID},
            normalization=_TASK.normalization,
            normalized_candidates=normalized)
        assert isinstance(prediction, DiagnosisPrediction), family
        assert prediction.fault_family == \
            _TASK.normalization.normalize(family)


def test_frozen_component_identities_are_unchanged() -> None:
    from verifiednet.evaluation.comparison import (
        build_default_interpretation_policy,
    )
    from verifiednet.experiment import build_success_policy
    from verifiednet.training import build_causal_lm_objective_policy

    assert diagnosis_prompt_template().prompt_template_id \
        == PINNED_PROMPT_TEMPLATE_ID
    assert build_causal_lm_objective_policy().objective_policy_id \
        == PINNED_OBJECTIVE_POLICY_ID
    assert build_default_interpretation_policy().interpretation_policy_id \
        == PINNED_INTERPRETATION_POLICY_ID
    assert build_success_policy().success_policy_id \
        == PINNED_SUCCESS_POLICY_ID
    assert _TASK.scoring_policy_version == 1


def test_production_training_code_imports_no_evaluation() -> None:
    package = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
               / "training")
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not module.startswith("verifiednet.evaluation"), (
                    path.name, module)
