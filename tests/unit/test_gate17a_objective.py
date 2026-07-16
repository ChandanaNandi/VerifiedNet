"""Gate 17A unit tests: the boundary-aligned objective removes the masked
newline separator, supervises the first target token under the exact deployed
inference prefix, keeps a deterministic distinct id, and leaves the Gate 10F
separator-bearing objective and its pure example builder byte-unchanged."""

from __future__ import annotations

import pytest

from verifiednet.training import (
    boundary_aligned_objective_policy,
    build_boundary_aligned_example,
    build_causal_lm_example,
    build_causal_lm_objective_policy,
)
from verifiednet.training.bounds import (
    BoundedTrainingError,
    derive_objective_policy_id,
)

pytestmark = pytest.mark.unit

#: Pinned identities (content-addressed; regression-locked).
LEGACY_OBJECTIVE_ID = "objpol-e5f36da1a1292f3d"
BOUNDARY_OBJECTIVE_ID = "objpol-7e6428964eae2db8"


def test_legacy_objective_is_byte_unchanged() -> None:
    legacy = build_causal_lm_objective_policy()
    assert legacy.objective_policy_id == LEGACY_OBJECTIVE_ID
    assert legacy.separator == "\n"
    assert legacy.label_masking == "mask_input_and_separator"
    assert legacy.sequence_construction == "input_separator_target_eos"
    assert legacy.chat_template == "none"
    # widening the Literals did not perturb the derived id
    assert derive_objective_policy_id(legacy) == LEGACY_OBJECTIVE_ID


def test_boundary_objective_id_is_deterministic_and_distinct() -> None:
    a = boundary_aligned_objective_policy()
    b = boundary_aligned_objective_policy()
    assert a == b
    assert a.objective_policy_id == b.objective_policy_id == BOUNDARY_OBJECTIVE_ID
    assert a.objective_policy_id != LEGACY_OBJECTIVE_ID
    assert derive_objective_policy_id(a) == BOUNDARY_OBJECTIVE_ID


def test_boundary_objective_locks_the_contract() -> None:
    pol = boundary_aligned_objective_policy()
    assert pol.separator == ""  # no separator span
    assert pol.label_masking == "mask_input_only"
    assert pol.sequence_construction == "input_target_eos"
    assert pol.special_vocab_rule == "append_eos_only"
    assert pol.eos_handling == "single_trailing_eos_in_loss"
    assert pol.padding_rule == "pad_right_mask_labels"
    assert pol.loss_reduction == "mean_over_unmasked"
    assert pol.chat_template == "none"
    assert pol.ignore_index == -100


def test_sequence_construction_is_not_serialized() -> None:
    # the dispatch view is derived, never a stored field — so it cannot
    # perturb the content-addressed id.
    dump = boundary_aligned_objective_policy().model_dump(mode="json")
    assert "sequence_construction" not in dump


def test_boundary_example_is_input_target_eos_mask_input_only() -> None:
    tokens, labels = build_boundary_aligned_example(
        input_token_ids=(11, 12, 13), target_token_ids=(21, 22),
        eos_token_id=99, max_total_tokens=64)
    assert tokens == (11, 12, 13, 21, 22, 99)  # no separator between 13 and 21
    assert labels == (-100, -100, -100, 21, 22, 99)
    assert len(tokens) == len(labels)
    assert labels.count(99) == 1  # exactly one trailing EOS supervised


def test_boundary_prefix_matches_inference_prefix_exactly() -> None:
    """The supervised first-target-token context is the raw input tokens —
    byte-identical to the deployed inference prefix (no trailing separator),
    whereas the legacy prefix carries the extra newline token."""
    input_ids = (5, 6, 7, 8)
    sep = (198,)
    target = (30, 31)
    legacy_tokens, _ = build_causal_lm_example(
        input_token_ids=input_ids, separator_token_ids=sep,
        target_token_ids=target, eos_token_id=2, max_total_tokens=64)
    bound_tokens, _ = build_boundary_aligned_example(
        input_token_ids=input_ids, target_token_ids=target,
        eos_token_id=2, max_total_tokens=64)
    first_target_index_bound = len(input_ids)
    assert bound_tokens[:first_target_index_bound] == input_ids
    assert bound_tokens[first_target_index_bound] == target[0]
    # the legacy assembly has the newline token immediately before the target
    assert legacy_tokens[len(input_ids)] == 198
    assert legacy_tokens[len(input_ids) + len(sep)] == target[0]
    # target tuple is identical either way (independent tokenization)
    assert bound_tokens[first_target_index_bound:] == (*target, 2)


def test_boundary_example_overlength_fails_closed() -> None:
    with pytest.raises(BoundedTrainingError):
        build_boundary_aligned_example(
            input_token_ids=(1, 2, 3, 4), target_token_ids=(5, 6),
            eos_token_id=9, max_total_tokens=5)


def test_legacy_pure_builder_is_unchanged() -> None:
    tokens, labels = build_causal_lm_example(
        input_token_ids=(1, 2), separator_token_ids=(198,),
        target_token_ids=(7,), eos_token_id=9, max_total_tokens=64)
    assert tokens == (1, 2, 198, 7, 9)
    assert labels == (-100, -100, -100, 7, 9)
