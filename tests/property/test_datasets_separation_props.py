"""Gate 6.2 Part 4 property tests: deterministic separation + leakage walker."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.datasets.feature_leakage import (
    FORBIDDEN_FEATURE_KEYS,
    FeatureLeakageCode,
    audit_feature_payload,
)
from verifiednet.datasets.features import FeaturePolicy, LabelPolicy

pytestmark = pytest.mark.property

# A bounded recursive JSON-ish structure using only SAFE keys and values.
_safe_keys = st.sampled_from(["a", "b", "ctx", "topology_hash", "backend", "info"])
_safe_scalars = st.one_of(
    st.integers(-5, 5), st.booleans(), st.sampled_from(["ok", "frr_compose", "x", "y"])
)
_json = st.recursive(
    _safe_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(_safe_keys, children, max_size=4),
    ),
    max_leaves=15,
)


def _insert(node, key, value):
    """Insert (key,value) into the deepest reachable dict, or wrap in a new dict."""
    if isinstance(node, dict):
        node = dict(node)
        node[key] = value
        return node
    if isinstance(node, list) and node and isinstance(node[0], dict):
        node = list(node)
        node[0] = _insert(node[0], key, value)
        return node
    return {"nested": node, key: value}


@given(payload=st.dictionaries(_safe_keys, _json, max_size=4),
       forbidden_key=st.sampled_from(sorted(FORBIDDEN_FEATURE_KEYS)))
@settings(max_examples=200)
def test_nested_forbidden_key_is_detected(payload, forbidden_key) -> None:
    tampered = _insert(payload, forbidden_key, "whatever")
    findings = audit_feature_payload(tampered, forbidden_values=frozenset())
    assert any(f.code is FeatureLeakageCode.FORBIDDEN_FEATURE_KEY for f in findings)


@given(payload=st.dictionaries(_safe_keys, _json, max_size=4),
       secret=st.text(min_size=6, max_size=20))
@settings(max_examples=200)
def test_nested_forbidden_value_is_detected(payload, secret) -> None:
    tampered = _insert(payload, "ctx", secret)
    findings = audit_feature_payload(tampered, forbidden_values=frozenset({secret}))
    assert any(f.code is FeatureLeakageCode.FORBIDDEN_FEATURE_VALUE for f in findings)


@given(payload=st.dictionaries(_safe_keys, _json, max_size=4))
@settings(max_examples=100)
def test_clean_payload_has_no_findings(payload) -> None:
    # A payload with only safe keys and no forbidden values audits clean. ("info"/
    # "ctx"/"a"/"b"/"backend"/"topology_hash" are not forbidden names.)
    findings = audit_feature_payload(payload, forbidden_values=frozenset({"UNSEEN-SECRET"}))
    assert findings == []


@given(include_onset=st.booleans())
@settings(max_examples=25)
def test_policy_id_is_stable(include_onset) -> None:
    a = FeaturePolicy(include_onset=include_onset).policy_id
    b = FeaturePolicy(include_onset=include_onset).policy_id
    assert a == b
    assert LabelPolicy().policy_id == LabelPolicy().policy_id


def test_separation_is_deterministic(tmp_path_factory, separated_pipeline) -> None:
    # Full separation twice from the same source -> byte-identical canonical output.
    from verifiednet.common.canonical import canonical_json_bytes
    from verifiednet.datasets.separation import separate_dataset

    tmp = tmp_path_factory.mktemp("sep")
    ctx = separated_pipeline(tmp, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b")],
                             rejected=["run-rej"])
    again = separate_dataset(ctx.loaded.examples, feature_policy=ctx.feature_policy,
                             label_policy=ctx.label_policy, dataset_version="v1",
                             source_index_digest=ctx.source_index_digest)
    assert [canonical_json_bytes(s) for s in ctx.separated] == \
           [canonical_json_bytes(s) for s in again]
