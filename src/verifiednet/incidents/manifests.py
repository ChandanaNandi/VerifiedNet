"""Manifest and incident serialization — canonical bytes, loud failures.

Writers return the SHA-256 of the exact bytes written so callers can
cross-link manifests into records (``ProvenanceInfo``). Any I/O error
propagates: a manifest that cannot be durably written must fail the run
(Gate 2 §12 correction — no silent manifest loss).
"""

from __future__ import annotations

from pathlib import Path

from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.hashing import sha256_bytes
from verifiednet.schemas.incident import IncidentRecord
from verifiednet.schemas.manifests import EnvironmentManifest, RunManifest


def write_run_manifest(manifest: RunManifest, path: Path) -> str:
    """Write the run manifest as canonical JSON; return the SHA-256 of the bytes."""
    data = canonical_json_bytes(manifest)
    path.write_bytes(data)
    return sha256_bytes(data)


def write_environment_manifest(manifest: EnvironmentManifest, path: Path) -> str:
    """Write the environment manifest as canonical JSON; return its SHA-256."""
    data = canonical_json_bytes(manifest)
    path.write_bytes(data)
    return sha256_bytes(data)


def incident_to_json_bytes(record: IncidentRecord) -> bytes:
    """Canonical JSON bytes for an incident record (the dataset serialization)."""
    return canonical_json_bytes(record)
