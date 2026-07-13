"""Optional Gate 8 integration: a REAL local Ollama SLM through the framework.

Skipped by default (marked ``integration``, deselected in CI). When a local Ollama
daemon serving the configured model IS reachable, it proves a real language model
plugs into the exact same evaluation framework and produces a verifiable,
immutable evaluation result. Determinism note: real model text is not guaranteed
bit-identical; this test asserts the framework INTEGRATION (parse/validate/score/
write/verify), not model-output bit-identity.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    DecodingConfig,
    OllamaBackend,
    SlmPredictor,
    audit_evaluation_run,
    diagnosis_prompt_template,
    diagnosis_task,
    evaluate_prepared_corpus,
    read_evaluation,
    verify_evaluation,
    write_evaluation,
)
from verifiednet.evaluation.inference import BackendUnavailableError, InferenceTimeoutError

pytestmark = pytest.mark.integration

_MODEL = os.environ.get("VERIFIEDNET_OLLAMA_MODEL", "qwen2.5:0.5b")
_HOST = os.environ.get("VERIFIEDNET_OLLAMA_HOST", "http://127.0.0.1:11434")


def _ollama_or_skip() -> OllamaBackend:
    backend = OllamaBackend(model=_MODEL, host=_HOST, timeout_s=30.0)
    try:
        backend.generate("ping", decoding=DecodingConfig(max_tokens=1))
    except (BackendUnavailableError, InferenceTimeoutError) as exc:
        pytest.skip(f"local Ollama unavailable: {exc}")
    return backend


def test_real_ollama_predictor_integrates(tmp_path: Path, eval_pipeline) -> None:
    backend = _ollama_or_skip()
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b")],
                        rejected=["run-rej"])
    task = diagnosis_task()
    slm = SlmPredictor(
        task=task, backend=backend, prompt_template=diagnosis_prompt_template(),
        model_identifier=_MODEL, backend_name="ollama", decoding=DecodingConfig())
    run = evaluate_prepared_corpus(ctx.loaded, slm, task)
    assert audit_evaluation_run(run).passed
    assert len(run.records) == len(ctx.loaded.examples)
    written = write_evaluation(run, tmp_path / "evaluations")
    assert verify_evaluation(written.root).verified is True
    assert read_evaluation(written.root).evaluation_id == run.evaluation_id
