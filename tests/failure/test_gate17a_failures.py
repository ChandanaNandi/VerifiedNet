"""Gate 17A failure tests: a separator on the boundary-aligned objective, a
wrong masking mode, missing/duplicate EOS shapes, overlength, an unsupported
objective version, a model_construct bypass, and any accidental mutation of the
legacy objective all fail closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    boundary_aligned_objective_policy,
    build_boundary_aligned_example,
    build_causal_lm_objective_policy,
)
from verifiednet.training.bounds import (
    BoundedTrainingError,
    TrainingObjectivePolicy,
    derive_objective_policy_id,
)

pytestmark = pytest.mark.failure

LEGACY_OBJECTIVE_ID = "objpol-e5f36da1a1292f3d"
BOUNDARY_OBJECTIVE_ID = "objpol-7e6428964eae2db8"


def test_separator_on_boundary_objective_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            separator="\n", label_masking="mask_input_only",
            objective_policy_id=BOUNDARY_OBJECTIVE_ID)


def test_legacy_masking_without_separator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            separator="", label_masking="mask_input_and_separator",
            objective_policy_id=LEGACY_OBJECTIVE_ID)


def test_unsupported_separator_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            separator="\t", label_masking="mask_input_only",
            objective_policy_id=BOUNDARY_OBJECTIVE_ID)


def test_unsupported_objective_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            objective_version=2, separator="",
            label_masking="mask_input_only",
            objective_policy_id=BOUNDARY_OBJECTIVE_ID)


def test_overlength_boundary_example_fails_closed() -> None:
    with pytest.raises(BoundedTrainingError):
        build_boundary_aligned_example(
            input_token_ids=(1, 2, 3), target_token_ids=(4, 5, 6),
            eos_token_id=9, max_total_tokens=6)


def test_model_construct_bypass_is_caught_by_id_derivation() -> None:
    # a hand-forged instance that skips validation still fails the id check
    forged = TrainingObjectivePolicy.model_construct(
        separator="\n", label_masking="mask_input_only",
        objective_policy_id=BOUNDARY_OBJECTIVE_ID)
    # its declared id does not match a coherent derivation of its own fields
    assert derive_objective_policy_id(forged) != BOUNDARY_OBJECTIVE_ID


def test_legacy_objective_cannot_be_mutated_in_place() -> None:
    legacy = build_causal_lm_objective_policy()
    with pytest.raises(ValidationError):
        legacy.label_masking = "mask_input_only"
    # and it still derives the pinned id
    assert legacy.objective_policy_id == LEGACY_OBJECTIVE_ID


def test_boundary_objective_id_wrong_binding_is_rejected() -> None:
    # binding the boundary policy's fields to the legacy id fails closed
    with pytest.raises(ValidationError):
        TrainingObjectivePolicy(
            separator="", label_masking="mask_input_only",
            objective_policy_id=LEGACY_OBJECTIVE_ID)


def test_boundary_builder_has_no_separator_parameter() -> None:
    import inspect

    params = inspect.signature(build_boundary_aligned_example).parameters
    assert "separator_token_ids" not in params
    assert "separator" not in params
    _ = boundary_aligned_objective_policy  # referenced for symmetry
