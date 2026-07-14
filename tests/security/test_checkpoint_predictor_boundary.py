"""Gate 11 security proofs: the checkpoint predictor is feature-only,
network-free, read-only over the checkpoint, and statically training-free."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from verifiednet.datasets.features import AbstentionLabels, AcceptedLabels
from verifiednet.evaluation import (
    DecodingConfig,
    FakeInferenceBackend,
    VerifiedCheckpointPredictor,
    audit_evaluation_run,
    evaluate_prepared_corpus,
)

pytestmark = pytest.mark.security

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]
_HFINFERENCE = (Path(__file__).resolve().parents[2] / "src" / "verifiednet"
                / "evaluation" / "hfinference.py")


def _both(tmp_path: Path, eval_pipeline, ckpt_predictor_pipeline):
    """Build the eval corpus and the verified checkpoint in disjoint roots."""
    eval_root = tmp_path / "evalside"
    train_root = tmp_path / "trainside"
    eval_root.mkdir()
    train_root.mkdir()
    evalctx = eval_pipeline(eval_root, accepted=_ACC, rejected=["run-rej"])
    ckptctx = ckpt_predictor_pipeline(
        train_root, accepted=_ACC, rejected=["run-rej"])
    return evalctx, ckptctx


def test_checkpoint_predictor_prompt_contains_no_labels_or_metadata(
    tmp_path: Path, eval_pipeline, ckpt_predictor_pipeline,
) -> None:
    evalctx, ckptctx = _both(tmp_path, eval_pipeline, ckpt_predictor_pipeline)

    captured: list[str] = []

    def responder(prompt: str, decoding: DecodingConfig) -> str:
        captured.append(prompt)
        return '{"prediction_type": "abstention"}'

    predictor = VerifiedCheckpointPredictor(
        task=ckptctx.task, bundle=ckptctx.bundle,
        backend=FakeInferenceBackend(responder=responder),
        prompt_template=ckptctx.template, device_policy=ckptctx.device_policy,
        backend_family="fake")
    evaluate_prepared_corpus(evalctx.loaded, predictor, ckptctx.task)

    # Evaluator-only secrets that must NEVER appear in a prompt: per-example
    # identity, digests, split, answer-binding facts. The candidate family
    # CLASS LIST is public class space, not the answer.
    secrets: set[str] = set()
    for ex in evalctx.loaded.examples:
        secrets.update({ex.trace.example_id, ex.trace.group_id,
                        ex.trace.run_id, ex.trace.run_digest,
                        ex.trace.split_policy_id})
        if isinstance(ex.labels, AcceptedLabels):
            secrets.add(ex.labels.scenario_id)
        elif isinstance(ex.labels, AbstentionLabels):
            secrets.update({ex.labels.rejection_code, ex.labels.failed_phase})
    # nor may any checkpoint/lineage identity leak INTO a prompt
    lineage = ckptctx.bundle.manifest.lineage
    secrets.update({ckptctx.bundle.manifest.checkpoint_id,
                    lineage.training_corpus_id, lineage.real_execution_id})

    assert len(captured) == len(evalctx.loaded.examples)
    for prompt in captured:
        for secret in secrets:
            assert secret not in prompt, secret


def test_checkpoint_predictor_pipeline_uses_no_network(
    tmp_path: Path, eval_pipeline, ckpt_predictor_pipeline, monkeypatch,
) -> None:
    evalctx, ckptctx = _both(tmp_path, eval_pipeline, ckpt_predictor_pipeline)

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "the offline checkpoint pipeline must not open a network client")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    predictor = VerifiedCheckpointPredictor(
        task=ckptctx.task, bundle=ckptctx.bundle,
        backend=FakeInferenceBackend(
            responder=lambda p, d: '{"prediction_type": "abstention"}'),
        prompt_template=ckptctx.template, device_policy=ckptctx.device_policy,
        backend_family="fake")
    run = evaluate_prepared_corpus(evalctx.loaded, predictor, ckptctx.task)
    assert audit_evaluation_run(run).passed  # completes, network sabotaged


def test_prediction_never_mutates_the_checkpoint(
    tmp_path: Path, eval_pipeline, ckpt_predictor_pipeline,
) -> None:
    evalctx, ckptctx = _both(tmp_path, eval_pipeline, ckpt_predictor_pipeline)
    before = ckptctx.bundle.fingerprint()
    predictor = VerifiedCheckpointPredictor(
        task=ckptctx.task, bundle=ckptctx.bundle,
        backend=FakeInferenceBackend(
            responder=lambda p, d: '{"prediction_type": "diagnosis", '
                                   '"fault_family": "bgp_remote_as_mismatch"}'),
        prompt_template=ckptctx.template, device_policy=ckptctx.device_policy,
        backend_family="fake")
    run = evaluate_prepared_corpus(evalctx.loaded, predictor, ckptctx.task)
    assert len(run.records) == len(evalctx.loaded.examples)
    assert ckptctx.bundle.fingerprint() == before  # every byte identical
    assert ckptctx.bundle.reverify().eligible is True


#: Training / mutation / persistence APIs that must never appear in the
#: inference backend. ``requires_grad_`` and ``eval`` are the sanctioned
#: read-only calls and are asserted PRESENT instead.
_FORBIDDEN_CALLS = {"backward", "step", "zero_grad", "train",
                    "save_pretrained", "save", "save_file", "load_state_dict",
                    "state_dict", "add_", "mul_", "copy_", "fill_"}
_REQUIRED_CALLS = {"eval", "requires_grad_", "inference_mode"}


def test_hfinference_is_statically_training_free() -> None:
    tree = ast.parse(_HFINFERENCE.read_text(encoding="utf-8"),
                     filename=str(_HFINFERENCE))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert not called & _FORBIDDEN_CALLS, sorted(called & _FORBIDDEN_CALLS)
    assert _REQUIRED_CALLS <= called, sorted(_REQUIRED_CALLS - called)
    # no optimizer/scheduler machinery is referenced anywhere
    source = _HFINFERENCE.read_text(encoding="utf-8")
    for marker in ("torch.optim", "lr_scheduler", "GradScaler", "Trainer("):
        assert marker not in source, marker
    # greedy decoding + offline + read-only load flags are pinned in source
    for required in ("do_sample=False", "num_beams=1", "local_files_only=True",
                     "trust_remote_code=False", "HF_HUB_OFFLINE",
                     "TRANSFORMERS_OFFLINE", "inference_mode"):
        assert required in source, required


def test_offline_suite_never_imports_ml_via_checkpoint_predictor() -> None:
    import sys

    import verifiednet.evaluation  # noqa: F401  (fully imported package)

    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
