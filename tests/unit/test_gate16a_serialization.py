"""Gate 16A unit tests: v2 template/policy identities, deterministic
rendering, clean leakage audit, and the same-source corpus proof."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets.features import (
    DatasetFeatures,
    FeatureEvidenceRef,
    FeaturePolicy,
)
from verifiednet.training import (
    CONTRACT_ALIGNED_TEMPLATE_NAME,
    CONTRACT_ALIGNED_TEMPLATE_VERSION,
    audit_training_example,
    build_training_corpus,
    contract_aligned_input_template,
    contract_aligned_training_policy,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
)

pytestmark = pytest.mark.unit

_TASK_ID = "task-2210abdbdd7e0d1c"  # diagnosis_task().task_id (pinned)
_FEATURE_POLICY_ID = FeaturePolicy().policy_id


def _v2():
    return contract_aligned_input_template(
        task_id=_TASK_ID, feature_policy_id=_FEATURE_POLICY_ID)


def _features(*, onset: bool, backend: str = "frr-compose",
              topology_hash: str = "a" * 64) -> DatasetFeatures:
    return DatasetFeatures(
        feature_policy_id=_FEATURE_POLICY_ID, topology_hash=topology_hash,
        backend=backend,
        baseline_evidence=FeatureEvidenceRef(
            relative_path="evidence/baseline.json"),
        onset_evidence=FeatureEvidenceRef(
            relative_path="evidence/onset.json") if onset else None)


def test_v2_template_construction_and_deterministic_id() -> None:
    template = _v2()
    assert template.template_version == CONTRACT_ALIGNED_TEMPLATE_VERSION
    assert template.name == CONTRACT_ALIGNED_TEMPLATE_NAME
    assert template.input_template_id.startswith("traintmpl-")
    assert _v2() == template  # deterministic


def test_v1_and_v2_identities_differ_and_target_is_unchanged() -> None:
    v1 = diagnosis_input_template(
        task_id=_TASK_ID, feature_policy_id=_FEATURE_POLICY_ID)
    v2 = _v2()
    assert v1.input_template_id != v2.input_template_id
    target = diagnosis_target_template(task_id=_TASK_ID)
    # the frozen Gate 10A/15 target identity, pinned
    assert target.target_template_id == "traintgt-286e4ecdff06833e"
    v1_policy = diagnosis_training_policy(
        task_id=_TASK_ID, input_template=v1, target_template=target)
    v2_policy = contract_aligned_training_policy(
        task_id=_TASK_ID, input_template=v2, target_template=target)
    assert v1_policy.training_data_policy_id \
        != v2_policy.training_data_policy_id
    assert v2_policy.target_template_id == target.target_template_id
    # eligibility Literals identical (train-only, accepted-only, no abstention)
    assert v2_policy.allowed_partition == v1_policy.allowed_partition
    assert v2_policy.allowed_example_kind == v1_policy.allowed_example_kind
    assert v2_policy.include_abstention is False


def test_v2_rendering_is_deterministic_over_representative_features() -> None:
    template = _v2()
    for onset in (True, False):
        for backend in ("frr-compose", "sim-backend"):
            for topology in ("a" * 64, "f" * 64):
                features = _features(onset=onset, backend=backend,
                                     topology_hash=topology)
                first = template.render(features)
                assert template.render(features) == first
                assert backend in first and topology in first
                assert ("onset_evidence: present" in first) is onset
                assert "Respond with ONE JSON object and nothing else" in first
                assert "or abstain" in first  # the deployed abstention language


def test_v2_render_grows_by_a_fixed_bounded_delta() -> None:
    """Structural offline length bound: v2 adds a CONSTANT text delta versus
    v1 (the mirrored instruction + schema sentences), independent of the
    features — the authoritative token-length proof over the real corpus and
    the real tokenizer is the gated Gate 16A integration test."""
    v1 = diagnosis_input_template(
        task_id=_TASK_ID, feature_policy_id=_FEATURE_POLICY_ID)
    v2 = _v2()
    deltas = set()
    for onset in (True, False):
        for backend in ("frr-compose", "b" * 40):
            features = _features(onset=onset, backend=backend)
            deltas.add(len(v2.render(features)) - len(v1.render(features)))
    assert len(deltas) == 1  # constant, feature-independent
    delta = deltas.pop()
    assert 0 < delta < 260  # the two mirrored sentences, nothing else


def test_v2_rendered_examples_pass_the_leakage_audit(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")],
                        rejected=["run-rej"])
    prepared = ctx.loaded
    v2 = contract_aligned_input_template(
        task_id=_TASK_ID,
        feature_policy_id=prepared.manifest.feature_policy_id)
    target = diagnosis_target_template(task_id=_TASK_ID)
    policy = contract_aligned_training_policy(
        task_id=_TASK_ID, input_template=v2, target_template=target)
    corpus = build_training_corpus(
        prepared, training_data_policy=policy, input_template=v2,
        target_template=target)
    assert corpus.examples  # builder already audits; re-audit explicitly
    for example in corpus.examples:
        assert audit_training_example(example).passed is True


def test_same_source_proof_v1_versus_v2(
    tmp_path: Path, gate14b_corpus_pipeline,
) -> None:
    """The capped v2 corpus selects EXACTLY the same ordered source examples
    as the capped v1 corpus; targets and trace bindings are unchanged; the
    only intended per-example byte difference is the rendered input."""
    from verifiednet.experiment import cap_training_corpus

    ctx, _accepted, _rejected = gate14b_corpus_pipeline(tmp_path, runs_cap=1)
    prepared = ctx.loaded
    feature_policy_id = prepared.manifest.feature_policy_id
    target = diagnosis_target_template(task_id=_TASK_ID)
    v1_template = diagnosis_input_template(
        task_id=_TASK_ID, feature_policy_id=feature_policy_id)
    v1_corpus = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=diagnosis_training_policy(
            task_id=_TASK_ID, input_template=v1_template,
            target_template=target),
        input_template=v1_template, target_template=target),
        max_example_count=64)
    v2_template = contract_aligned_input_template(
        task_id=_TASK_ID, feature_policy_id=feature_policy_id)
    v2_corpus = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=contract_aligned_training_policy(
            task_id=_TASK_ID, input_template=v2_template,
            target_template=target),
        input_template=v2_template, target_template=target),
        max_example_count=64)

    v1_sources = [e.trace.source_example_id for e in v1_corpus.examples]
    v2_sources = [e.trace.source_example_id for e in v2_corpus.examples]
    assert v1_sources == v2_sources  # the exact ordered source sequence
    assert len(v1_sources) == len(set(v1_sources))
    for left, right in zip(v1_corpus.examples, v2_corpus.examples,
                           strict=True):
        assert left.target.text == right.target.text  # targets unchanged
        assert left.input.text != right.input.text  # ONLY the input differs
        assert left.trace.source_group_id == right.trace.source_group_id
        assert left.trace.feature_policy_id == right.trace.feature_policy_id
        assert left.trace.label_policy_id == right.trace.label_policy_id
        assert left.trace.target_template_id == right.trace.target_template_id
        assert left.trace.input_template_id != right.trace.input_template_id
    assert v1_corpus.training_corpus_id != v2_corpus.training_corpus_id
    # both corpora bind the SAME untouched prepared source
    assert v1_corpus.source_prepared_digest == \
        prepared.manifest.prepared_digest
    assert v2_corpus.source_prepared_digest == \
        prepared.manifest.prepared_digest
