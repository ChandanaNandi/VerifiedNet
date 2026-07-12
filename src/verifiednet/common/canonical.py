"""Canonical JSON — the single serialization used for every content hash.

Gate 2.5 HIGH correction W5. Rules:
- UTF-8 output bytes;
- object keys sorted;
- deterministic separators ``(",", ":")`` (no whitespace);
- ``datetime`` must be timezone-aware and serializes as UTC ISO-8601 with a
  ``Z`` suffix (naive datetimes are rejected);
- ``Enum`` serializes to its ``value``;
- ``ipaddress`` objects serialize to their string form;
- ``set``/``frozenset`` serialize as a sorted list (elements canonicalized first);
- NaN and Infinity are rejected (``allow_nan=False``);
- floats use Python ``repr`` semantics via the standard JSON encoder — the
  shortest round-trippable representation; integral floats keep their ``.0``.

There is exactly ONE implementation of these rules; nothing else in the code
base may hash JSON any other way.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

_IP_TYPES = (
    ipaddress.IPv4Address,
    ipaddress.IPv6Address,
    ipaddress.IPv4Interface,
    ipaddress.IPv6Interface,
    ipaddress.IPv4Network,
    ipaddress.IPv6Network,
)


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonically serialized."""


def _normalize(value: Any) -> Any:
    """Recursively convert to plain JSON-compatible structures, deterministically."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalizationError(f"non-finite float not allowed: {value!r}")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise CanonicalizationError("naive datetime not allowed in canonical JSON")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, _IP_TYPES):
        return str(value)
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize(v) for v in value]
        try:
            return sorted(normalized, key=lambda x: json.dumps(x, sort_keys=True))
        except TypeError as exc:  # pragma: no cover - defensive
            raise CanonicalizationError(f"unsortable set: {exc}") from exc
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string mapping key: {key!r}")
            out[key] = _normalize(val)
        return out
    if isinstance(value, bytes):
        raise CanonicalizationError("raw bytes not allowed; hex-encode explicitly")
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _normalize(model_dump(mode="json"))
    raise CanonicalizationError(f"unsupported type for canonical JSON: {type(value)!r}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize *value* to canonical JSON bytes (UTF-8)."""
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_str(value: Any) -> str:
    """Serialize *value* to a canonical JSON string."""
    return canonical_json_bytes(value).decode("utf-8")
