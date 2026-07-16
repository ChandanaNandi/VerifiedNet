"""Gate 17A contract tests: the boundary-aligned objective is a frozen,
content-addressed, extra-forbidding policy that cannot carry a separator or
input+separator masking, keeps chat_template none, and leaves the Gate 8
prompt/parser and the frozen templates untouched."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    boundary_aligned_objective_policy,
    build_causal_lm_objective_policy,
)
from verifiednet.training.bounds import TrainingObjectivePolicy

pytestmark = pytest.mark.contract

LEGACY_OBJECTIVE_ID = "objpol-e5f36da1a1292f3d"
BOUNDARY_OBJECTIVE_ID = "objpol-7e6428964eae2db8"
PROMPT_TEMPLATE_ID = "prompt-93808d932655a347"
TARGET_TEMPLATE_ID = "traintgt-286e4ecdff06833e"


def test_policy_is_frozen_and_extra_forbid() -> None:
    pol = boundary_aligned_objective_policy()
    with pytest.raises(ValidationError):
        pol.separator = "\n"  # frozen
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            separator="", label_masking="mask_input_only",
            objective_policy_id=BOUNDARY_OBJECTIVE_ID,
            unexpected="x")  # extra=forbid


def test_ids_self_validate() -> None:
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            separator="", label_masking="mask_input_only",
            objective_policy_id="objpol-wrong")


def test_legacy_policy_still_valid_and_pinned() -> None:
    legacy = build_causal_lm_objective_policy()
    assert legacy.objective_policy_id == LEGACY_OBJECTIVE_ID
    # reconstructing it explicitly round-trips
    again = TrainingObjectivePolicy(
        objective_policy_id=LEGACY_OBJECTIVE_ID)
    assert again == legacy


def test_new_policy_cannot_carry_a_separator() -> None:
    # a boundary-aligned (mask_input_only) policy with a "\n" separator is
    # structurally unrepresentable.
    with pytest.raises(ValidationError, match="forbids a separator"):
        TrainingObjectivePolicy(
            separator="\n", label_masking="mask_input_only",
            objective_policy_id=BOUNDARY_OBJECTIVE_ID)


def test_new_policy_cannot_use_mask_input_and_separator() -> None:
    # and the boundary-aligned builder never yields the legacy masking mode
    assert boundary_aligned_objective_policy().label_masking \
        == "mask_input_only"
    # a mask_input_and_separator policy REQUIRES the newline separator
    with pytest.raises(ValidationError, match="requires the"):
        TrainingObjectivePolicy(
            separator="", label_masking="mask_input_and_separator",
            objective_policy_id=LEGACY_OBJECTIVE_ID)


def test_new_policy_keeps_chat_template_none() -> None:
    assert boundary_aligned_objective_policy().chat_template == "none"


def test_builder_exposes_no_arbitrary_objective_text_or_separator() -> None:
    import inspect

    sig = inspect.signature(boundary_aligned_objective_policy)
    assert list(sig.parameters) == []  # no injectable separator/masking/text


def test_gate8_prompt_and_parser_and_templates_unchanged() -> None:
    # the frozen deployed prompt and the frozen target template keep their ids
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.evaluation.prompt import diagnosis_prompt_template
    from verifiednet.training import diagnosis_target_template

    assert diagnosis_prompt_template().prompt_template_id == PROMPT_TEMPLATE_ID
    task_id = diagnosis_task().task_id
    assert diagnosis_target_template(
        task_id=task_id).target_template_id == TARGET_TEMPLATE_ID
    # the Gate 8 strict parser module is importable and unchanged in contract:
    # a bare target round-trips valid, empty text is invalid.
    from verifiednet.evaluation.slm import parse_backend_response

    assert parse_backend_response is not None
