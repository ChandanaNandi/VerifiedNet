"""RunManifest and EnvironmentManifest (Gate 2.5 HIGH correction W1).

Provenance: capture-set modeled on closcall ``scripts/emit_manifest*.py`` patterns
(architectural reference only) with the Gate 2 §12 gaps fixed: OS/Python captured,
seeds recorded, and manifest writers fail loudly (writer lives in incidents).
Gate 3 tests populate environment fields with deterministic fixtures; live values
arrive in Gate 4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from verifiednet.schemas.base import StrictModel, UtcDatetime


class RunManifest(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    git_rev: str
    lock_hash: str  # sha256 of uv.lock
    scenario_id: str
    template_id: str
    topology_hash: str  # sha256_canonical(TopologySpec)
    image_digests: dict[str, str] = Field(default_factory=dict)
    transcript_sha256: str | None = None
    seeds: dict[str, int] = Field(default_factory=dict)
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    acceptance_status: Literal["accepted", "rejected", "incomplete"] = "incomplete"


class EnvironmentManifest(StrictModel):
    schema_version: Literal[1] = 1
    os_name: str
    kernel: str
    arch: str
    python_version: str
    container_runtime: str
    container_runtime_version: str
    image_reference: str  # as requested, e.g. "frrouting/frr:v8.4.1@sha256:..."
    image_manifest_digest: str | None = None  # multi-arch manifest digest
    platform_resolved_digest: str | None = None  # digest actually run on this host
    frr_version: str | None = None
    captured_at: UtcDatetime
