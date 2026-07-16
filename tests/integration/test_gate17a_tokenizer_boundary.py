"""Gate 17A gated real-tokenizer boundary proof (read-only).

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE17A_TOKENIZER=1``,
plus a local Qwen snapshot dir and a training-corpus dir. Skips by default so
offline CI never loads a tokenizer or a model. It proves — against the exact
pinned tokenizer — that the boundary-aligned training prefix is token-identical
to the raw deployed inference prompt, that the legacy prefix differs only by the
single ``"\n"`` token (198), and that no BOS/EOS-source mismatch confounds the
change. It loads a tokenizer ONLY (never model weights), writes nothing, and
touches no experiment store.

Enable, e.g.:
  VERIFIEDNET_RUN_GATE17A_TOKENIZER=1 \
  VERIFIEDNET_LOCAL_MODEL_DIR=<qwen snapshot dir> \
  VERIFIEDNET_GATE17A_CORPUS=<training-corpora/<id> dir> \
  pytest tests/integration/test_gate17a_tokenizer_boundary.py -m integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from verifiednet.training import (
    build_boundary_aligned_example,
    build_causal_lm_example,
)

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE17A_TOKENIZER") == "1"
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")
_CORPUS = os.environ.get("VERIFIEDNET_GATE17A_CORPUS", "")

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(
                  not (_ENABLED and _MODEL_DIR and _CORPUS),
                  reason="Gate 17A tokenizer proof is opt-in and needs a local "
                         "Qwen snapshot dir and a training-corpus dir")]


def _first_pair() -> tuple[str, str]:
    corp = Path(_CORPUS)
    inp = json.loads(next(open(corp / "inputs.jsonl")))["text"]
    tgt = json.loads(next(open(corp / "targets.jsonl")))["text"]
    return inp, tgt


def test_boundary_prefix_matches_raw_inference_prefix() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from transformers import (  # type: ignore[import-not-found, unused-ignore]
        AutoTokenizer,
        PreTrainedTokenizerFast,
    )

    model_dir = Path(_MODEL_DIR)
    train_tok = AutoTokenizer.from_pretrained(
        str(model_dir), local_files_only=True)
    infer_tok = PreTrainedTokenizerFast(
        tokenizer_file=str(model_dir / "tokenizer.json"))
    config = json.loads((model_dir / "config.json").read_text())

    prompt, target = _first_pair()
    train_ids = tuple(train_tok.encode(prompt, add_special_tokens=False))
    infer_default = tuple(infer_tok(prompt)["input_ids"])
    infer_false = tuple(infer_tok(prompt, add_special_tokens=False)["input_ids"])

    # (a) training input encoding == inference prompt encoding
    assert train_ids == infer_default
    # (b) default inference encoding adds no BOS
    assert infer_default == infer_false
    assert (train_tok.bos_token_id is None
            or train_ids[0] != train_tok.bos_token_id)
    # (c) tokenizer EOS == config EOS
    cfg_eos = config["eos_token_id"]
    cfg_eos = cfg_eos[0] if isinstance(cfg_eos, list) else cfg_eos
    assert train_tok.eos_token_id == cfg_eos
    # (d) newline is a single token 198
    assert tuple(train_tok.encode("\n", add_special_tokens=False)) == (198,)

    eos = int(train_tok.eos_token_id)
    target_ids = tuple(train_tok.encode(target, add_special_tokens=False))
    bound_tokens, bound_labels = build_boundary_aligned_example(
        input_token_ids=train_ids, target_token_ids=target_ids,
        eos_token_id=eos, max_total_tokens=100_000)
    legacy_tokens, _ = build_causal_lm_example(
        input_token_ids=train_ids, separator_token_ids=(198,),
        target_token_ids=target_ids, eos_token_id=eos,
        max_total_tokens=100_000)

    # (e) the new pre-target training prefix equals the raw inference prompt ids
    assert bound_tokens[:len(train_ids)] == infer_default
    # (f) the legacy prefix equals raw prompt ids + [198]
    assert legacy_tokens[:len(train_ids) + 1] == (*infer_default, 198)
    # (g) record the actual verified first target token (do not hardcode a
    #     false universal fact); assert it is the target's own first token and
    #     that the boundary assembly supervises it under the raw prefix.
    first_target_token = target_ids[0]
    assert bound_tokens[len(train_ids)] == first_target_token
    assert bound_labels[len(train_ids)] == first_target_token
    print(f"VERIFIED first_target_token={first_target_token} "
          f"decoded={train_tok.decode([first_target_token])!r}")
