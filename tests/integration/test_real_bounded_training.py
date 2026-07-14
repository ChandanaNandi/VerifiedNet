"""OPTIONAL integration: the first bounded REAL weight mutation (Gate 10F).

DOUBLE-GATED: deselected by default (`-m "not integration"`), and even when
integration tests run it additionally requires the explicit environment flag
``VERIFIEDNET_RUN_REAL_TRAINING=1`` plus the ``training-hf`` extras plus an
approved LOCAL model directory (``VERIFIEDNET_LOCAL_MODEL_DIR``). It never
downloads anything; a missing local artifact is a structured refusal.

When enabled it performs the bounded approved run (tiny corpus slice, few
optimizer steps), writes exactly ONE real checkpoint, verifies execution and
checkpoint, and proves REAL weight mutation by hashing one trainable tensor's
serialized bytes before and after — without exposing tensor values. It runs
no evaluation and no benchmark.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]

_ENABLED = os.environ.get("VERIFIEDNET_RUN_REAL_TRAINING") == "1"
_HAS_TORCH = importlib.util.find_spec("torch") is not None
_HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")


@pytest.mark.skipif(not _ENABLED,
                    reason="set VERIFIEDNET_RUN_REAL_TRAINING=1 to enable "
                           "the bounded real-training integration test")
@pytest.mark.skipif(not (_HAS_TORCH and _HAS_TRANSFORMERS),
                    reason="training-hf extras are not installed")
@pytest.mark.skipif(not _MODEL_DIR,
                    reason="VERIFIEDNET_LOCAL_MODEL_DIR is not set")
def test_bounded_real_training_mutates_weights(
    tmp_path: Path, realtrain_pipeline, monkeypatch,
) -> None:
    import urllib.request

    from verifiednet.training import (
        ExecutionState,
        HFTrainingEngine,
        RealTrainingExecutor,
        parse_safetensors_header,
        read_real_execution,
        verify_real_checkpoint,
        verify_real_execution,
    )

    def _no_network(*a: object, **k: object) -> object:
        raise AssertionError("real training attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    model_dir = Path(_MODEL_DIR)
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    executor = RealTrainingExecutor(HFTrainingEngine())
    written = executor.execute(
        plan_dir=ctx.plan_dir, corpus_dir=ctx.corpus_root,
        authorization_dir=ctx.auth_dir, model_dir=model_dir,
        tokenizer_dir=model_dir, output_root=tmp_path / "real-out",
        model_policy=ctx.model_policy, slice_policy=ctx.slice_policy,
        execution_policy=ctx.execution_policy,
        objective_policy=ctx.objective_policy)
    assert written.final_state is ExecutionState.COMPLETED
    assert verify_real_execution(written.root).verified is True
    loaded = read_real_execution(written.root)
    assert loaded.result.completed_optimizer_steps >= 1
    assert loaded.result.observed_losses  # finite, recorded, never "quality"

    ckpt = tmp_path / "real-out" / "real-checkpoints" / written.checkpoint_id
    assert verify_real_checkpoint(ckpt).verified is True
    # exactly one checkpoint was produced
    assert len(list((tmp_path / "real-out" / "real-checkpoints").iterdir())) == 1

    # REAL weight mutation proof: hash one trainable tensor's serialized
    # bytes before/after; never expose tensor values
    source_blob = (model_dir / "model.safetensors").read_bytes()
    trained_blob = (ckpt / "payload" / "model.safetensors").read_bytes()
    source_header = parse_safetensors_header(source_blob)
    tensor_name = next(k for k in sorted(source_header) if k != "__metadata__")

    def tensor_hash(blob: bytes, name: str) -> str:
        header = parse_safetensors_header(blob)
        entry = header[name]
        assert isinstance(entry, dict)
        (hlen,) = __import__("struct").unpack("<Q", blob[:8])
        start, end = entry["data_offsets"]  # type: ignore[index]
        data = blob[8 + hlen:][start:end]
        return hashlib.sha256(data).hexdigest()

    assert tensor_hash(source_blob, tensor_name) != \
        tensor_hash(trained_blob, tensor_name)
    # the source model artifact itself remained unchanged
    assert (model_dir / "model.safetensors").read_bytes() == source_blob
    assert trained_blob != source_blob
