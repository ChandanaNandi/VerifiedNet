"""Gate 8 security proofs: the SLM predictor is features-only and network-free.

The model NEVER receives labels, trace metadata, identity, or split — only the
rendered prompt built from ``DatasetFeatures``. And the default SLM pipeline
(with the fake backend) touches no network client.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets.features import AbstentionLabels, AcceptedLabels
from verifiednet.evaluation import (
    DecodingConfig,
    FakeInferenceBackend,
    SlmPredictor,
    audit_evaluation_run,
    diagnosis_prompt_template,
    diagnosis_task,
    evaluate_prepared_corpus,
)

pytestmark = pytest.mark.security

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


def test_model_prompt_contains_no_labels_or_metadata(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    captured: list[str] = []

    def responder(prompt: str, decoding: DecodingConfig) -> str:
        captured.append(prompt)
        return '{"prediction_type": "abstention"}'

    task = diagnosis_task()
    slm = SlmPredictor(
        task=task, backend=FakeInferenceBackend(responder=responder),
        prompt_template=diagnosis_prompt_template(), model_identifier="m",
        backend_name="fake")
    evaluate_prepared_corpus(ctx.loaded, slm, task)

    # Collect the evaluator-only secrets that must NEVER appear in a prompt:
    # per-example identity, digests, split, and answer-binding facts. NOTE the
    # fault-family CLASS NAMES are legitimately in the prompt as the candidate
    # class list (public class space, not the answer) and the generic words
    # "diagnosis"/"abstention" appear in the response schema — neither is a secret.
    secrets: set[str] = set()
    for ex in ctx.loaded.examples:
        secrets.update({ex.trace.example_id, ex.trace.group_id, ex.trace.run_id,
                        ex.trace.run_digest, ex.trace.split_policy_id})
        if isinstance(ex.labels, AcceptedLabels):
            secrets.add(ex.labels.scenario_id)
        elif isinstance(ex.labels, AbstentionLabels):
            secrets.update({ex.labels.rejection_code, ex.labels.failed_phase})

    assert len(captured) == len(ctx.loaded.examples)
    for prompt in captured:
        for secret in secrets:
            assert secret not in prompt, secret


def test_slm_pipeline_uses_no_network(tmp_path: Path, eval_pipeline, monkeypatch) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("the offline SLM pipeline must not open a network client")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    task = diagnosis_task()
    slm = SlmPredictor(
        task=task,
        backend=FakeInferenceBackend(responder=lambda p, d: '{"prediction_type": "abstention"}'),
        prompt_template=diagnosis_prompt_template(), model_identifier="m",
        backend_name="fake")
    run = evaluate_prepared_corpus(ctx.loaded, slm, task)
    assert audit_evaluation_run(run).passed  # completes with the network sabotaged
