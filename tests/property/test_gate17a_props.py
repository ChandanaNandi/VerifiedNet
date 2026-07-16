"""Gate 17A property tests: for arbitrary bounded token tuples the boundary-
aligned assembly is exactly input+target+EOS with input-only masking, exactly
one EOS, deterministic output, id sensitivity to the defining fields, and
overlength rejection."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    boundary_aligned_objective_policy,
    build_boundary_aligned_example,
)
from verifiednet.training.bounds import (
    BoundedTrainingError,
    TrainingObjectivePolicy,
    derive_objective_policy_id,
)

pytestmark = pytest.mark.property

_toks = st.lists(st.integers(min_value=0, max_value=200000), min_size=0,
                 max_size=40).map(tuple)
_target = st.lists(st.integers(min_value=0, max_value=200000), min_size=1,
                   max_size=40).map(tuple)


@settings(max_examples=200, deadline=None)
@given(inp=_toks, tgt=_target, eos=st.integers(min_value=0, max_value=200000))
def test_assembly_and_labels_are_exact(inp, tgt, eos) -> None:
    tokens, labels = build_boundary_aligned_example(
        input_token_ids=inp, target_token_ids=tgt, eos_token_id=eos,
        max_total_tokens=10_000)
    # sequence arithmetic
    assert tokens == (*inp, *tgt, eos)
    assert len(labels) == len(tokens)
    # every input label is masked
    assert labels[:len(inp)] == (-100,) * len(inp)
    # every target label equals its token id
    assert labels[len(inp):len(inp) + len(tgt)] == tgt
    # final label equals EOS, and exactly one EOS is appended
    assert labels[-1] == eos
    assert tokens[-1] == eos
    assert len(tokens) == len(inp) + len(tgt) + 1
    # no separator span was inserted
    if inp and tgt:
        assert tokens[len(inp)] == tgt[0]


@settings(max_examples=100, deadline=None)
@given(inp=_toks, tgt=_target, eos=st.integers(min_value=0, max_value=9))
def test_output_is_deterministic(inp, tgt, eos) -> None:
    a = build_boundary_aligned_example(
        input_token_ids=inp, target_token_ids=tgt, eos_token_id=eos,
        max_total_tokens=10_000)
    b = build_boundary_aligned_example(
        input_token_ids=inp, target_token_ids=tgt, eos_token_id=eos,
        max_total_tokens=10_000)
    assert a == b


@settings(max_examples=100, deadline=None)
@given(inp=st.lists(st.integers(0, 9), min_size=1, max_size=30).map(tuple),
       tgt=st.lists(st.integers(0, 9), min_size=1, max_size=30).map(tuple),
       eos=st.integers(0, 9))
def test_overlength_fails_closed(inp, tgt, eos) -> None:
    total = len(inp) + len(tgt) + 1
    # a cap one below the exact length must reject
    with pytest.raises(BoundedTrainingError):
        build_boundary_aligned_example(
            input_token_ids=inp, target_token_ids=tgt, eos_token_id=eos,
            max_total_tokens=total - 1)
    # the exact length is accepted
    tokens, _ = build_boundary_aligned_example(
        input_token_ids=inp, target_token_ids=tgt, eos_token_id=eos,
        max_total_tokens=total)
    assert len(tokens) == total


def test_boundary_id_is_sensitive_to_defining_fields() -> None:
    base = boundary_aligned_objective_policy()
    # flipping either defining field changes the derived id
    flipped_sep = TrainingObjectivePolicy.model_construct(
        **{**base.model_dump(), "separator": "\n",
           "label_masking": "mask_input_and_separator"})
    assert derive_objective_policy_id(flipped_sep) != base.objective_policy_id
