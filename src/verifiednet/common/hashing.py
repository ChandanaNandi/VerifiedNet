"""SHA-256 helpers over canonical JSON and files.

Provenance: file-hash behavior matches closcall ``datasets/manifest.py::sha256_file``
(commit d192bf3) but is REIMPLEMENTED FROM SPECIFICATION (closcall has no published
license — Gate 0 provenance action). Content hashes are always computed over
canonical JSON (common/canonical.py) — never over ad-hoc serializations.

Volatile fields excluded from reproducibility comparisons (documented per Gate 3
Step 3): wall-clock timestamps, run_id values, compose project names, host paths,
and transcript durations. These are recorded in artifacts but MUST NOT feed
content hashes that are compared across runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from verifiednet.common.canonical import canonical_json_bytes

_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_canonical(value: Any) -> str:
    """Hex SHA-256 of the canonical JSON serialization of *value*."""
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's contents, streamed."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
