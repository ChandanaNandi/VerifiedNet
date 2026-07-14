"""Gate 10A failure tests: fail-closed eligibility, corruption, leakage."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import diagnosis_task
from verifiednet.training import (
    TrainingCorpusError,
    TrainingStoreError,
    audit_training_example,
    build_training_corpus,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
    load_training_corpus,
    load_training_pairs,
    verify_training_corpus,
    write_training_corpus,
)
from verifiednet.training.corpus import (
    SupervisedTrainingExample,
    SupervisedTrainingInput,
    TrainingLeakageCode,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _setup(ctx):
    task_id = diagnosis_task().task_id
    fp = ctx.loaded.manifest.feature_policy_id
    itpl = diagnosis_input_template(task_id=task_id, feature_policy_id=fp)
    ttpl = diagnosis_target_template(task_id=task_id)
    policy = diagnosis_training_policy(task_id=task_id, input_template=itpl,
                                       target_template=ttpl)
    return policy, itpl, ttpl


def _corpus(tmp_path, eval_pipeline):
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    corpus = build_training_corpus(ctx.loaded, training_data_policy=policy,
                                   input_template=itpl, target_template=ttpl)
    return corpus, ctx


def test_mismatched_task_ids_fail(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, _ttpl = _setup(ctx)
    other_ttpl = diagnosis_target_template(task_id="task-ffffffffffffffff")
    with pytest.raises(TrainingCorpusError):
        build_training_corpus(ctx.loaded, training_data_policy=policy,
                              input_template=itpl, target_template=other_ttpl)


def test_mismatched_feature_policy_fails(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task_id = diagnosis_task().task_id
    itpl = diagnosis_input_template(task_id=task_id,
                                    feature_policy_id="feat-ffffffffffffffff")
    ttpl = diagnosis_target_template(task_id=task_id)
    policy = diagnosis_training_policy(task_id=task_id, input_template=itpl,
                                       target_template=ttpl)
    with pytest.raises(TrainingCorpusError):
        build_training_corpus(ctx.loaded, training_data_policy=policy,
                              input_template=itpl, target_template=ttpl)


def test_rejected_label_in_train_partition_fails(
    tmp_path: Path, eval_pipeline,
) -> None:
    # A train-partition example forced (model_construct) to carry abstention
    # labels while claiming accepted kind must be refused — a rejected target can
    # never become a diagnosis target.
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    from verifiednet.datasets.features import SeparatedDatasetExample
    from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition

    train_ex = next(ex for ex in ctx.loaded.examples
                    if ex.trace.partition is DatasetPartition.TRAIN)
    abstention_ex = next(ex for ex in ctx.loaded.examples
                         if ex.trace.partition is DatasetPartition.ABSTENTION)
    forged = SeparatedDatasetExample.model_construct(
        schema_version=1, features=train_ex.features, labels=abstention_ex.labels,
        trace=train_ex.trace.model_copy(
            update={"example_kind": DatasetExampleKind.ACCEPTED_FAULT,
                    "partition": DatasetPartition.TRAIN}))
    loaded = dataclasses.replace(ctx.loaded, examples=(forged,))
    with pytest.raises(TrainingCorpusError):
        build_training_corpus(loaded, training_data_policy=policy,
                              input_template=itpl, target_template=ttpl)


def test_duplicate_source_example_fails(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy, itpl, ttpl = _setup(ctx)
    doubled = dataclasses.replace(
        ctx.loaded, examples=(*ctx.loaded.examples, ctx.loaded.examples[0]))
    with pytest.raises(TrainingCorpusError):
        build_training_corpus(doubled, training_data_policy=policy,
                              input_template=itpl, target_template=ttpl)


def test_deliberate_leakage_is_rejected(tmp_path: Path, eval_pipeline) -> None:
    # Inject the source example id into the rendered input via defense-in-depth
    # construction — the audit must flag it as a forbidden VALUE.
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    victim = corpus.examples[0]
    leaked_text = victim.input.text + "\n" + victim.trace.source_example_id
    forged = SupervisedTrainingExample.model_construct(
        schema_version=1, training_example_id=victim.training_example_id,
        input=SupervisedTrainingInput(text=leaked_text),
        target=victim.target, trace=victim.trace)
    result = audit_training_example(forged)
    assert result.passed is False
    assert any(f.code is TrainingLeakageCode.FORBIDDEN_INPUT_VALUE
               for f in result.errors)
    # And the honest path: a validated example with tampered input cannot even
    # be constructed (identity binding).
    with pytest.raises(ValidationError):
        SupervisedTrainingExample(
            training_example_id=victim.training_example_id,
            input=SupervisedTrainingInput(text=leaked_text),
            target=victim.target, trace=victim.trace)


def test_corrupted_inputs_file_rejected(tmp_path: Path, eval_pipeline) -> None:
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    victim = w.root / "inputs.jsonl"
    victim.write_bytes(victim.read_bytes() + b" ")
    result = verify_training_corpus(w.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    with pytest.raises(TrainingStoreError):
        load_training_pairs(w.root)


def test_corrupted_target_content_rejected(tmp_path: Path, eval_pipeline) -> None:
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    victim = w.root / "targets.jsonl"
    tampered = victim.read_bytes().replace(b"bgp_", b"xxx_", 1)
    assert tampered != victim.read_bytes()
    victim.write_bytes(tampered)
    result = verify_training_corpus(w.root)
    assert result.verified is False  # hash guard + id binding both break
    with pytest.raises(TrainingStoreError):
        load_training_corpus(w.root)


def test_tampered_manifest_digest_rejected(tmp_path: Path, eval_pipeline) -> None:
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    m = w.root / "manifest.json"
    data = json.loads(m.read_text())
    data["source_prepared_digest"] = "0" * 64
    m.write_text(json.dumps(data))
    result = verify_training_corpus(w.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_missing_file_rejected(tmp_path: Path, eval_pipeline) -> None:
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    (w.root / "metadata.jsonl").unlink()
    result = verify_training_corpus(w.root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)


def test_missing_directory_rejected(tmp_path: Path) -> None:
    result = verify_training_corpus(tmp_path / "nope")
    assert result.verified is False
    with pytest.raises(TrainingStoreError):
        load_training_pairs(tmp_path / "nope")


def test_unsafe_overwrite_refused(tmp_path: Path, eval_pipeline) -> None:
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    write_training_corpus(corpus, tmp_path / "training-corpora")
    with pytest.raises(TrainingStoreError):
        write_training_corpus(corpus, tmp_path / "training-corpora")


def test_unsupported_format_version_does_not_parse(
    tmp_path: Path, eval_pipeline,
) -> None:
    corpus, _ = _corpus(tmp_path, eval_pipeline)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    m = w.root / "manifest.json"
    data = json.loads(m.read_text())
    data["corpus_format_version"] = 2
    m.write_text(json.dumps(data))
    result = verify_training_corpus(w.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)
