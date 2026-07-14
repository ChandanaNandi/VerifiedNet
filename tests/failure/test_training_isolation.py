"""Gate 10A proofs: partition isolation, source immutability, no execution/network.

The partition-isolation proof is the critical one: changing ONLY evaluation-side
examples (validation/test/abstention) must leave the training corpus content
byte-identical — evaluation-only data structurally cannot influence training
artifacts. The manifest's provenance pins (prepared/dataset digests) necessarily
track the changed source; that is the ONLY permitted difference, and the corpus
identity itself is unchanged.
"""

from __future__ import annotations

import hashlib
import subprocess
import urllib.request
from pathlib import Path

import pytest

from verifiednet.evaluation import diagnosis_task
from verifiednet.training import (
    build_training_corpus,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
    load_training_corpus,
    load_training_pairs,
    verify_training_corpus,
    write_training_corpus,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


def _build(ctx):
    task_id = diagnosis_task().task_id
    fp = ctx.loaded.manifest.feature_policy_id
    itpl = diagnosis_input_template(task_id=task_id, feature_policy_id=fp)
    ttpl = diagnosis_target_template(task_id=task_id)
    policy = diagnosis_training_policy(task_id=task_id, input_template=itpl,
                                       target_template=ttpl)
    return build_training_corpus(ctx.loaded, training_data_policy=policy,
                                 input_template=itpl, target_template=ttpl)


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_partition_isolation(tmp_path: Path, eval_pipeline) -> None:
    # Two prepared corpora with IDENTICAL train examples but a DIFFERENT
    # abstention (evaluation-only) example.
    ctx_a = eval_pipeline(tmp_path / "a", accepted=_ACC, rejected=["run-rej"])
    ctx_b = eval_pipeline(tmp_path / "b", accepted=_ACC, rejected=["run-rej-other"])
    assert (ctx_a.loaded.manifest.prepared_digest
            != ctx_b.loaded.manifest.prepared_digest)  # sources genuinely differ

    corpus_a = _build(ctx_a)
    corpus_b = _build(ctx_b)

    # The training corpus is unaffected by the evaluation-side change:
    assert corpus_a.training_corpus_id == corpus_b.training_corpus_id
    assert [e.training_example_id for e in corpus_a.examples] == \
        [e.training_example_id for e in corpus_b.examples]

    w_a = write_training_corpus(corpus_a, tmp_path / "tc-a")
    w_b = write_training_corpus(corpus_b, tmp_path / "tc-b")
    # inputs/targets/metadata are byte-identical; only the manifest's provenance
    # pins (prepared/dataset digests + corpus digest over them) may differ.
    for rel in ("inputs.jsonl", "targets.jsonl", "metadata.jsonl"):
        assert (w_a.root / rel).read_bytes() == (w_b.root / rel).read_bytes(), rel
    assert w_a.root.name == w_b.root.name  # same corpus identity on disk


def test_training_does_not_mutate_sources(tmp_path: Path, eval_pipeline) -> None:
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        evaluate_prepared_corpus,
        write_evaluation,
    )

    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # materialize an evaluation artifact too, to prove training never touches it
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    write_evaluation(run, tmp_path / "evaluations")

    roots = {
        "runs": Path(ctx.run_root),
        "dataset": Path(ctx.dataset_dir),
        "prepared": Path(ctx.prepared_dir),
        "evaluations": tmp_path / "evaluations",
    }
    before = {name: _fingerprint(root) for name, root in roots.items()}

    corpus = _build(ctx)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    assert verify_training_corpus(w.root).verified is True
    load_training_corpus(w.root)
    load_training_pairs(w.root)

    after = {name: _fingerprint(root) for name, root in roots.items()}
    assert after == before  # every upstream stage byte-identical


def test_training_pipeline_no_execution_no_network(
    tmp_path: Path, eval_pipeline, monkeypatch,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("training pipeline must not execute or open a network client")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    corpus = _build(ctx)
    w = write_training_corpus(corpus, tmp_path / "training-corpora")
    assert verify_training_corpus(w.root).verified is True
    assert len(load_training_pairs(w.root)) == len(corpus.examples)


def test_build_twice_reproducibility(tmp_path: Path, eval_pipeline) -> None:
    from verifiednet.common.canonical import canonical_json_bytes

    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    c1 = _build(ctx)
    c2 = _build(ctx)
    assert canonical_json_bytes(c1) == canonical_json_bytes(c2)
    w1 = write_training_corpus(c1, tmp_path / "t1")
    w2 = write_training_corpus(c2, tmp_path / "t2")
    assert w1.training_corpus_digest == w2.training_corpus_digest
    assert _fingerprint(w1.root) == _fingerprint(w2.root)  # byte-identical dirs
