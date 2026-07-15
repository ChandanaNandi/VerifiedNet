"""Optional Gate 16A integration: the AUTHORITATIVE pre-experiment
token-length proof over the REAL v3 chain with the REAL pinned tokenizer,
plus the real-chain same-source proof. Never runs in offline CI.

Gated on ``VERIFIEDNET_RUN_GATE16A=1`` + the v3 prepared-chain dir + the
approved base-model snapshot dir + the training-hf extras. Loads ONLY the
tokenizer (never the model), never trains, never evaluates, and never
creates an experiment, plan, authorization, execution, or checkpoint —
Gate 16B remains unstarted.

If any of the 64 selected v2 examples exceeds the UNCHANGED Gate 15
sequence policy (384 input / 64 target / 448 total), this test FAILS with
the exact counts — the sequence policy is never changed here and nothing is
silently truncated.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE16A") == "1"
_BASE_DIR = os.environ.get("VERIFIEDNET_BASE_MODEL_DIR", "")
_V3_PREPARED = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V3_PREPARED_DIR", "")

MAX_INPUT_TOKENS = 384
MAX_TARGET_TOKENS = 64
MAX_TOTAL_TOKENS = 448
EXAMPLE_CAP = 64


def test_all_selected_v2_examples_fit_the_gate15_sequence_policy(
    monkeypatch,
) -> None:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_GATE16A!=1")
    for name, value in (("VERIFIEDNET_BASE_MODEL_DIR", _BASE_DIR),
                        ("VERIFIEDNET_EVAL_CORPUS_V3_PREPARED_DIR",
                         _V3_PREPARED)):
        if not value or not Path(value).is_dir():
            pytest.skip(f"{name} not set / not a dir")
    if importlib.util.find_spec("transformers") is None:
        pytest.skip("transformers not installed (training-hf extras required)")

    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 16A must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.experiment import cap_training_corpus
    from verifiednet.training import (
        build_training_corpus,
        contract_aligned_input_template,
        contract_aligned_training_policy,
        diagnosis_input_template,
        diagnosis_target_template,
        diagnosis_training_policy,
    )

    prepared = load_prepared(Path(_V3_PREPARED))
    task_id = diagnosis_task().task_id
    feature_policy_id = prepared.manifest.feature_policy_id
    target = diagnosis_target_template(task_id=task_id)

    v1_template = diagnosis_input_template(
        task_id=task_id, feature_policy_id=feature_policy_id)
    v1_capped = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=diagnosis_training_policy(
            task_id=task_id, input_template=v1_template,
            target_template=target),
        input_template=v1_template, target_template=target),
        max_example_count=EXAMPLE_CAP)
    v2_template = contract_aligned_input_template(
        task_id=task_id, feature_policy_id=feature_policy_id)
    v2_capped = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=contract_aligned_training_policy(
            task_id=task_id, input_template=v2_template,
            target_template=target),
        input_template=v2_template, target_template=target),
        max_example_count=EXAMPLE_CAP)

    # REAL-CHAIN same-source proof: exact ordered source ids, targets equal.
    assert [e.trace.source_example_id for e in v1_capped.examples] == \
        [e.trace.source_example_id for e in v2_capped.examples]
    assert len(v2_capped.examples) == EXAMPLE_CAP
    for left, right in zip(v1_capped.examples, v2_capped.examples,
                           strict=True):
        assert left.target.text == right.target.text
        assert left.input.text != right.input.text

    # AUTHORITATIVE token-length proof with the REAL pinned tokenizer.
    from transformers import AutoTokenizer  # type: ignore[import-not-found, unused-ignore]

    tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call, unused-ignore]
        _BASE_DIR, local_files_only=True)
    separator_tokens = len(tokenizer.encode("\n", add_special_tokens=False))
    overlength: list[tuple[str, int, int, int]] = []
    max_input = max_target = max_total = 0
    for example in v2_capped.examples:
        input_tokens = len(tokenizer.encode(
            example.input.text, add_special_tokens=False))
        target_tokens = len(tokenizer.encode(
            example.target.text, add_special_tokens=False))
        total = input_tokens + separator_tokens + target_tokens + 1  # + EOS
        max_input = max(max_input, input_tokens)
        max_target = max(max_target, target_tokens)
        max_total = max(max_total, total)
        if (input_tokens > MAX_INPUT_TOKENS
                or target_tokens > MAX_TARGET_TOKENS
                or total > MAX_TOTAL_TOKENS):
            overlength.append((example.training_example_id, input_tokens,
                               target_tokens, total))
    assert not overlength, (
        f"{len(overlength)} of {EXAMPLE_CAP} v2 examples exceed the "
        f"UNCHANGED Gate 15 sequence policy "
        f"(max_input={max_input}, max_target={max_target}, "
        f"max_total={max_total}): {overlength[:5]}")
    print(f"TOKEN-LENGTH PROOF: max_input={max_input}/{MAX_INPUT_TOKENS} "
          f"max_target={max_target}/{MAX_TARGET_TOKENS} "
          f"max_total={max_total}/{MAX_TOTAL_TOKENS} over "
          f"{EXAMPLE_CAP} examples")
