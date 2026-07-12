"""Unit tests for SHA-256 helpers over canonical JSON and files."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from verifiednet.common.hashing import sha256_bytes, sha256_canonical, sha256_file

pytestmark = pytest.mark.unit


def test_sha256_bytes_matches_hashlib() -> None:
    data = b"verifiednet"
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"line one\nline two\n" * 1000
    target = tmp_path / "blob.bin"
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_accepts_str_path(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_bytes(b"abc")
    assert sha256_file(str(target)) == hashlib.sha256(b"abc").hexdigest()


def test_sha256_canonical_key_order_independent() -> None:
    assert sha256_canonical({"a": 1, "b": 2}) == sha256_canonical({"b": 2, "a": 1})


def test_sha256_canonical_deterministic_across_calls() -> None:
    value = {"nested": {"x": [1, 2, 3]}, "flag": True}
    assert sha256_canonical(value) == sha256_canonical(value)


def test_sha256_canonical_distinguishes_values() -> None:
    assert sha256_canonical({"a": 1}) != sha256_canonical({"a": 2})
