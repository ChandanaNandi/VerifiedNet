"""Gate 17A regression proofs: the additive objective must not perturb any
frozen upstream contract — the Gate 10F objective id, the Gate 8 prompt/target
ids, the frozen success policy, the Gate 13 reliability classification, and the
Gate 16A v2 byte-mirror all remain pinned."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def test_frozen_objective_and_template_ids() -> None:
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.evaluation.prompt import diagnosis_prompt_template
    from verifiednet.training import (
        build_causal_lm_objective_policy,
        diagnosis_target_template,
    )

    assert build_causal_lm_objective_policy().objective_policy_id \
        == "objpol-e5f36da1a1292f3d"
    assert diagnosis_prompt_template().prompt_template_id \
        == "prompt-93808d932655a347"
    task_id = diagnosis_task().task_id
    assert diagnosis_target_template(
        task_id=task_id).target_template_id == "traintgt-286e4ecdff06833e"


def test_frozen_success_policy_id() -> None:
    from verifiednet.experiment import build_success_policy

    assert build_success_policy().success_policy_id == "esucc-ab21b8d6e2ab7a70"


def test_gate13_reliability_classification_unchanged() -> None:
    from verifiednet.evaluation.structured import (
        InvalidOutputCategory,
        classify_invalid_output,
    )

    # empty completion -> empty_output (the Gate 16B treatment failure mode)
    assert classify_invalid_output(
        reason_code="malformed_json", raw_excerpt="") \
        == InvalidOutputCategory.EMPTY_OUTPUT
    # prose-wrapped json -> prose_wrapped_json (the base failure mode)
    assert classify_invalid_output(
        reason_code="malformed_json",
        raw_excerpt=' Sure. {"prediction_type": "abstention"}') \
        == InvalidOutputCategory.PROSE_WRAPPED_JSON


def test_gate16a_v2_byte_mirror_unchanged() -> None:
    # the contract-aligned v2 serialization still mirrors the deployed prompt's
    # instructions and response-schema byte-for-byte (Gate 16A invariant).
    from verifiednet.evaluation.prompt import _INSTRUCTIONS, _RESPONSE_SCHEMA
    from verifiednet.training.policy import (
        _CONTRACT_INSTRUCTIONS,
        _CONTRACT_RESPONSE_SCHEMA,
    )

    assert _CONTRACT_INSTRUCTIONS == _INSTRUCTIONS
    assert _CONTRACT_RESPONSE_SCHEMA == _RESPONSE_SCHEMA
    # the deployed prompt ends WITHOUT a trailing newline (the raw inference
    # boundary the Gate 17A objective aligns to)
    assert not _RESPONSE_SCHEMA.endswith("\n")
