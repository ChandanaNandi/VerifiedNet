"""Gate 10A unit tests: policies, templates, builder, store, trainer loader."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from verifiednet.datasets.models import DatasetPartition
from verifiednet.evaluation import diagnosis_task
from verifiednet.training import (
    TrainingPair,
    build_training_corpus,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
    load_training_corpus,
    load_training_pairs,
    verify_training_corpus,
    write_training_corpus,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c"),
        ("pf-ref", "run-d")]


def _setup(ctx):
    task_id = diagnosis_task().task_id
    fp = ctx.loaded.manifest.feature_policy_id
    itpl = diagnosis_input_template(task_id=task_id, feature_policy_id=fp)
    ttpl = diagnosis_target_template(task_id=task_id)
    policy = diagnosis_training_policy(task_id=task_id, input_template=itpl,
                                       target_template=ttpl)
    return policy, itpl, ttpl


def test_policy_and_template_ids_deterministic(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    p1, i1, t1 = _setup(ctx)
    p2, i2, t2 = _setup(ctx)
    assert p1.training_data_policy_id == p2.training_data_policy_id
    assert i1.input_template_id == i2.input_template_id
    assert t1.target_template_id == t2.target_template_id
    assert p1.training_data_policy_id.startswith("trainpolicy-")
    assert i1.input_template_id.startswith("traintmpl-")
    assert t1.target_template_id.startswith("traintgt-")


def test_input_rendering_is_deterministic_and_feature_only(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _, itpl, _ = _setup(ctx)
    example = ctx.loaded.examples[0]
    rendered = itpl.render(example.features)
    assert rendered == itpl.render(example.features)  # deterministic
    assert example.features.backend in rendered
    assert example.features.topology_hash in rendered
    # no identity / label / policy id in the model input
    for secret in (example.trace.example_id, example.trace.group_id,
                   example.trace.run_id, example.trace.run_digest,
                   itpl.feature_policy_id):
        assert secret not in rendered


def test_target_rendering_is_canonical(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _, _, ttpl = _setup(ctx)
    a = ttpl.render("bgp_remote_as_mismatch")
    b = ttpl.render("bgp_remote_as_mismatch")
    assert a == b  # equivalent labels -> byte-identical
    parsed = json.loads(a)
    assert parsed == {"prediction_type": "diagnosis",
                      "fault_family": "bgp_remote_as_mismatch"}
    assert " " not in a  # canonical: no whitespace


def test_train_only_selection_and_exclusions(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    corpus = build_training_corpus(ctx.loaded, training_data_policy=policy,
                                   input_template=itpl, target_template=ttpl)
    # 4 accepted train examples in; the abstention example is excluded.
    assert len(corpus.examples) == 4
    assert all(e.trace.partition == "train" for e in corpus.examples)
    assert all(e.trace.example_kind == "accepted_fault" for e in corpus.examples)
    # abstention source ids never appear
    abstention_ids = {ex.trace.example_id for ex in ctx.loaded.examples
                      if ex.trace.partition is DatasetPartition.ABSTENTION}
    corpus_sources = {e.trace.source_example_id for e in corpus.examples}
    assert abstention_ids.isdisjoint(corpus_sources)


def test_validation_and_test_partition_examples_are_excluded(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    # Move one accepted example into VALIDATION and one into TEST (legal states
    # for accepted examples): the builder must simply not select them.
    examples = list(ctx.loaded.examples)
    moved = 0
    for i, ex in enumerate(examples):
        if ex.trace.partition is DatasetPartition.TRAIN and moved < 2:
            new_part = (DatasetPartition.VALIDATION if moved == 0
                        else DatasetPartition.TEST)
            examples[i] = ex.model_copy(
                update={"trace": ex.trace.model_copy(update={"partition": new_part})})
            moved += 1
    assert moved == 2
    loaded = dataclasses.replace(ctx.loaded, examples=tuple(examples))
    corpus = build_training_corpus(loaded, training_data_policy=policy,
                                   input_template=itpl, target_template=ttpl)
    assert len(corpus.examples) == 2  # only the remaining train examples


def test_corpus_is_input_order_independent(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    from verifiednet.common.canonical import canonical_json_bytes

    forward = build_training_corpus(ctx.loaded, training_data_policy=policy,
                                    input_template=itpl, target_template=ttpl)
    reversed_loaded = dataclasses.replace(
        ctx.loaded, examples=tuple(reversed(ctx.loaded.examples)))
    backward = build_training_corpus(reversed_loaded, training_data_policy=policy,
                                     input_template=itpl, target_template=ttpl)
    assert canonical_json_bytes(forward) == canonical_json_bytes(backward)
    assert forward.training_corpus_id == backward.training_corpus_id


def test_write_verify_read_round_trip(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    corpus = build_training_corpus(ctx.loaded, training_data_policy=policy,
                                   input_template=itpl, target_template=ttpl)
    written = write_training_corpus(corpus, tmp_path / "training-corpora")
    assert written.root.name == corpus.training_corpus_id
    result = verify_training_corpus(written.root)
    assert result.verified is True, result.failures

    loaded = load_training_corpus(written.root)
    assert loaded.manifest.training_corpus_id == corpus.training_corpus_id
    assert len(loaded.examples) == 4
    from verifiednet.common.canonical import canonical_json_bytes
    assert [canonical_json_bytes(e) for e in loaded.examples] == \
        [canonical_json_bytes(e) for e in corpus.examples]


def test_trainer_facing_loader_returns_only_pairs(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    corpus = build_training_corpus(ctx.loaded, training_data_policy=policy,
                                   input_template=itpl, target_template=ttpl)
    written = write_training_corpus(corpus, tmp_path / "training-corpora")
    pairs = load_training_pairs(written.root)
    assert len(pairs) == 4
    for pair in pairs:
        assert isinstance(pair, TrainingPair)
        assert set(TrainingPair.model_fields) == {"schema_version", "input_text",
                                                  "target_text"}
        for forbidden in ("trace", "example_id", "group_id", "run_id",
                          "prepared_digest", "training_example_id"):
            assert not hasattr(pair, forbidden)
        # no identity leaks in the pair text either
        for e in corpus.examples:
            assert e.trace.source_example_id not in pair.input_text


def test_training_example_ids_are_deterministic(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    a = build_training_corpus(ctx.loaded, training_data_policy=policy,
                              input_template=itpl, target_template=ttpl)
    b = build_training_corpus(ctx.loaded, training_data_policy=policy,
                              input_template=itpl, target_template=ttpl)
    assert [e.training_example_id for e in a.examples] == \
        [e.training_example_id for e in b.examples]
    assert all(e.training_example_id.startswith("trainex-") for e in a.examples)
