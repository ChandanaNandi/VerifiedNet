"""Unit tests for the single canonical JSON implementation."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta, timezone

import pytest

from verifiednet.common.canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    canonical_json_str,
)
from verifiednet.schemas import NodeSpec, Verdict

pytestmark = pytest.mark.unit


def test_keys_sorted_and_compact_separators() -> None:
    assert canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_no_whitespace_in_output() -> None:
    out = canonical_json_str({"a": [1, 2], "b": {"c": True}})
    assert " " not in out
    assert out == '{"a":[1,2],"b":{"c":true}}'


def test_naive_datetime_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes({"t": datetime(2026, 1, 1)})


def test_aware_datetime_serializes_as_utc_z() -> None:
    stamp = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    assert canonical_json_bytes(stamp) == b'"2026-01-01T12:30:00Z"'


def test_non_utc_offset_converted_to_utc() -> None:
    tz = timezone(timedelta(hours=2))
    stamp = datetime(2026, 1, 1, 14, 0, tzinfo=tz)
    assert canonical_json_bytes(stamp) == b'"2026-01-01T12:00:00Z"'


def test_enum_serializes_to_value() -> None:
    assert canonical_json_bytes(Verdict.PASS) == b'"pass"'
    assert canonical_json_bytes({"v": Verdict.FAIL}) == b'{"v":"fail"}'


def test_ip_objects_serialize_to_string() -> None:
    assert canonical_json_bytes(ipaddress.ip_address("172.30.0.1")) == b'"172.30.0.1"'
    assert canonical_json_bytes(ipaddress.ip_network("10.0.0.0/24")) == b'"10.0.0.0/24"'
    assert canonical_json_bytes(ipaddress.ip_interface("172.30.0.1/30")) == b'"172.30.0.1/30"'


def test_set_serializes_as_sorted_list() -> None:
    assert canonical_json_bytes({"s": {"c", "a", "b"}}) == b'{"s":["a","b","c"]}'
    assert canonical_json_bytes(frozenset({3, 1, 2})) == b"[1,2,3]"


def test_nan_and_infinity_rejected() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalizationError):
            canonical_json_bytes({"x": bad})


def test_bytes_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes({"raw": b"\x00\x01"})


def test_float_representation() -> None:
    assert canonical_json_bytes(1.5) == b"1.5"
    assert canonical_json_bytes(1.0) == b"1.0"  # integral float keeps .0
    assert canonical_json_bytes(0.1) == b"0.1"


def test_non_string_mapping_key_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes({1: "a"})


def test_nested_pydantic_model_via_model_dump() -> None:
    node = NodeSpec(name="router_a", asn=65001, loopback="10.255.0.1/32")
    out = canonical_json_str({"node": node})
    assert '"asn":65001' in out
    assert '"name":"router_a"' in out
    assert out.index('"asn"') < out.index('"loopback"') < out.index('"name"')


def test_determinism_across_insertion_orders() -> None:
    a = {"x": 1, "y": {"b": 2, "a": 3}, "z": [1, 2]}
    b = {"z": [1, 2], "y": {"a": 3, "b": 2}, "x": 1}
    assert canonical_json_bytes(a) == canonical_json_bytes(b)


def test_unsupported_type_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes(object())
