"""Hypothesis properties for canonical JSON stability."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.hashing import sha256_canonical

pytestmark = pytest.mark.property

json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.text(max_size=20),
)

json_values = st.recursive(
    json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=10), children, max_size=4),
    ),
    max_leaves=20,
)

json_dicts = st.dictionaries(st.text(max_size=10), json_values, max_size=6)


def _shuffle_keys(value: Any) -> Any:
    """Deterministically reorder mapping keys throughout a structure."""
    if isinstance(value, dict):
        return dict(reversed([(k, _shuffle_keys(v)) for k, v in value.items()]))
    if isinstance(value, list):
        return [_shuffle_keys(v) for v in value]
    return value


@settings(max_examples=50, deadline=None, derandomize=True)
@given(json_dicts)
def test_canonical_bytes_stable_across_calls(value: dict[str, Any]) -> None:
    assert canonical_json_bytes(value) == canonical_json_bytes(value)


@settings(max_examples=50, deadline=None, derandomize=True)
@given(json_dicts)
def test_canonical_bytes_invariant_under_key_order(value: dict[str, Any]) -> None:
    assert canonical_json_bytes(value) == canonical_json_bytes(_shuffle_keys(value))


@settings(max_examples=50, deadline=None, derandomize=True)
@given(json_dicts)
def test_sha256_canonical_stable(value: dict[str, Any]) -> None:
    assert sha256_canonical(value) == sha256_canonical(_shuffle_keys(value))
    assert len(sha256_canonical(value)) == 64
