"""Optional Gate 11 integration: REAL inference from the verified checkpoint.

Never runs in offline CI. It is DOUBLE-gated: the ``integration`` marker
(deselected by default) AND ``VERIFIEDNET_RUN_REAL_CHECKPOINT_INFERENCE=1``
AND ``VERIFIEDNET_REAL_CHECKPOINT_DIR`` pointing at a verified real checkpoint
AND the ``training-hf`` extras being installed. When enabled it proves the
Gate 11 chain end to end on real weights: verify the checkpoint, load it
locally (CPU, float32, offline mode forced), produce at least one real
prediction from real ``DatasetFeatures`` through the unchanged Gate 8
pipeline, and prove the checkpoint bytes are unchanged afterwards.

It deliberately performs NO correctness evaluation, NO metrics, and NO
benchmarking — that is Gate 12 territory. A prediction of any outcome kind
(diagnosis, abstention, invalid) is an acceptable honest result here.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    DecodingConfig,
    HfCheckpointInferenceBackend,
    VerifiedCheckpointPredictor,
    build_checkpoint_inference_compatibility,
    build_cpu_inference_device_policy,
    diagnosis_prompt_template,
    diagnosis_task,
    load_verified_checkpoint_bundle,
)

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_REAL_CHECKPOINT_INFERENCE") == "1"
_CKPT_DIR = os.environ.get("VERIFIEDNET_REAL_CHECKPOINT_DIR", "")


def _checkpoint_or_skip() -> Path:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_REAL_CHECKPOINT_INFERENCE!=1")
    if not _CKPT_DIR:
        pytest.skip("VERIFIEDNET_REAL_CHECKPOINT_DIR is not set")
    root = Path(_CKPT_DIR)
    if not root.is_dir():
        pytest.skip(f"checkpoint dir not present: {root}")
    for module in ("torch", "transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} not installed (training-hf extras required)")
    return root


def test_real_checkpoint_backed_prediction(
    tmp_path: Path, eval_pipeline, monkeypatch,
) -> None:
    root = _checkpoint_or_skip()

    # No network: sabotage the stdlib client outright; HF offline mode is
    # additionally forced by the backend before any Transformers call.
    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("real checkpoint inference must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    compatibility = build_checkpoint_inference_compatibility()  # Qwen2 only
    device_policy = build_cpu_inference_device_policy()
    bundle = load_verified_checkpoint_bundle(root, compatibility=compatibility)
    fingerprint_before = bundle.fingerprint()

    backend = HfCheckpointInferenceBackend(
        bundle=bundle, device_policy=device_policy)
    task = diagnosis_task()
    predictor = VerifiedCheckpointPredictor(
        task=task, bundle=bundle, backend=backend,
        prompt_template=diagnosis_prompt_template(),
        device_policy=device_policy, decoding=DecodingConfig(max_tokens=64))
    assert predictor.predictor_spec.checkpoint_id == \
        bundle.manifest.checkpoint_id

    # Real DatasetFeatures from the deterministic prepared corpus: one
    # accepted-fault example and one abstention example.
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    outcomes = []
    for example in ctx.loaded.examples:
        prediction = predictor.predict(example.features)
        assert prediction.outcome_kind in {"diagnosis", "abstention", "invalid"}
        assert prediction.prediction_id.startswith("pred-")
        outcomes.append(prediction.outcome_kind)
    assert len(outcomes) >= 1  # at least one REAL prediction happened

    # Offline mode was forced before any Transformers call.
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"

    # The checkpoint is byte-identical after inference and still verified.
    assert bundle.fingerprint() == fingerprint_before
    assert bundle.reverify().eligible is True
    # NO correctness scoring, NO metrics, NO benchmark — Gate 12 boundary.
