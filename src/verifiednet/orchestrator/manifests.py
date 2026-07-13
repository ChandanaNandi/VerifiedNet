"""Build RunManifest and EnvironmentManifest from observed data (Gate 4 Step 6).

These helpers map already-observed values into the released manifest schemas.
They do NOT shell out or query Docker themselves — the caller passes the backend
environment metadata, the git commit, the lock hash, and the timestamps. When no
randomness exists, seeds default to an empty mapping (never invented).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel

from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.hashing import sha256_bytes
from verifiednet.schemas.incident import IncidentRecord
from verifiednet.schemas.manifests import EnvironmentManifest, RunManifest


def transcript_sha256(entries: Sequence[BaseModel]) -> str:
    """SHA-256 over the canonical JSONL bytes of the transcript (stable)."""
    data = b"".join(canonical_json_bytes(entry) + b"\n" for entry in entries)
    return sha256_bytes(data)


def build_run_manifest(
    *,
    incident: IncidentRecord,
    git_rev: str,
    lock_hash: str,
    transcript_sha: str,
    started_at: datetime,
    finished_at: datetime,
    seeds: Mapping[str, int] | None = None,
) -> RunManifest:
    """Assemble the run manifest from the incident + observed run metadata."""
    return RunManifest(
        run_id=incident.run_id,
        git_rev=git_rev,
        lock_hash=lock_hash,
        scenario_id=incident.scenario.scenario_id,
        template_id=incident.scenario.template_id,
        topology_hash=incident.topology_hash,
        image_digests={"frr": incident.topology.images.frr},
        transcript_sha256=transcript_sha,
        seeds=dict(seeds) if seeds else {},
        started_at=started_at,
        finished_at=finished_at,
        acceptance_status=incident.status,
    )


def build_environment_manifest(
    metadata: Mapping[str, str], *, captured_at: datetime
) -> EnvironmentManifest:
    """Map backend environment metadata into the environment manifest.

    Reads only keys the backend actually observed. ``frr_version`` must be placed
    into *metadata* by the caller (the backend does not capture it); when absent
    the field is left None rather than invented.
    """
    required = ("os_name", "kernel", "arch", "python_version", "container_runtime")
    missing = [k for k in required if not metadata.get(k)]
    if missing:
        raise ValueError(f"environment metadata missing required keys: {missing!r}")
    return EnvironmentManifest(
        os_name=metadata["os_name"],
        kernel=metadata["kernel"],
        arch=metadata["arch"],
        python_version=metadata["python_version"],
        container_runtime=metadata["container_runtime"],
        container_runtime_version=metadata.get("container_runtime_version", ""),
        image_reference=metadata["image_reference"],
        image_manifest_digest=metadata.get("image_manifest_digest"),
        platform_resolved_digest=metadata.get("platform_resolved_repo_digest"),
        frr_version=metadata.get("frr_version"),
        captured_at=captured_at,
    )
