"""Dataset models — the read-only PROJECTION of a verified run (Gate 6.1).

A ``DatasetExample`` is NOT truth. Truth is owned by the authoritative
``IncidentRecord`` inside the verified run directory (ADR-0018). An example is a
frozen, content-addressed pointer set: it references the immutable run artifacts
(incident, evidence, transcript, ledger) by ``(run_id, relative_path)`` and
carries the run's ``run_digest`` so any consumer can re-verify the run and then
read the referenced artifact. It embeds NO copy of evidence/transcript/ledger,
NO model output, and NO inferred field.

All models are Pydantic v2, frozen, ``extra="forbid"``, versioned, fully typed
(no ``Any``, no ``dict[str, Any]``, no mutable defaults).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import field_validator

from verifiednet.schemas.base import StrictModel

_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REL_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class ArtifactReference(StrictModel):
    """A verifiable pointer to one immutable file inside a verified run.

    Integrity is anchored by the enclosing ``DatasetExample.run_digest`` (the
    run is re-verified through the run index before the referenced file is
    read) — the reference itself embeds no copy and no per-file hash.
    """

    schema_version: Literal[1] = 1
    run_id: str
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or not _REL_PATH_RE.match(value):
            raise ValueError(f"unsafe or absolute relative_path: {value!r}")
        if ".." in value.split("/"):
            raise ValueError(f"path traversal in relative_path: {value!r}")
        return value


class DatasetExample(StrictModel):
    """One verified run projected as a dataset example (references only)."""

    schema_version: Literal[1] = 1

    # -- identity ----------------------------------------------------------
    example_id: str  # unique per run: "ex-<hex16>"
    group_id: str  # leakage group (stable scenario identity): "grp-<hex16>"
    run_id: str
    run_digest: str
    template_id: str
    scenario_id: str
    topology_hash: str
    backend: str
    acceptance_status: Literal["accepted", "rejected"]

    # -- references to authoritative artifacts (never embedded copies) -----
    incident_reference: ArtifactReference
    #: Points at the artifact that CONTAINS the model-free GroundTruth (the
    #: incident file) for accepted runs; ``None`` for rejected runs.
    ground_truth_reference: ArtifactReference | None = None
    transcript_reference: ArtifactReference
    ledger_reference: ArtifactReference
    baseline_reference: ArtifactReference
    onset_reference: ArtifactReference | None = None
    recovery_reference: ArtifactReference | None = None

    # -- provenance --------------------------------------------------------
    code_commit: str
    oracle_version: str | None = None
    source_index_digest: str

    @field_validator("example_id")
    @classmethod
    def _valid_example_id(cls, value: str) -> str:
        if not (value.startswith("ex-") and _HEX16_RE.match(value[3:])):
            raise ValueError(f"example_id must be 'ex-<16 hex>': {value!r}")
        return value

    @field_validator("group_id")
    @classmethod
    def _valid_group_id(cls, value: str) -> str:
        if not (value.startswith("grp-") and _HEX16_RE.match(value[4:])):
            raise ValueError(f"group_id must be 'grp-<16 hex>': {value!r}")
        return value

    @field_validator("run_digest", "topology_hash", "source_index_digest")
    @classmethod
    def _valid_hex(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"expected 64 lowercase hex: {value!r}")
        return value


class DatasetManifest(StrictModel):
    """Minimal Gate 6.1 manifest (no ``dataset_digest`` yet — that is Gate 6.3)."""

    schema_version: Literal[1] = 1
    dataset_version: str
    generated_by: str
    source_index_digest: str
    example_count: int

    @field_validator("source_index_digest")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"source_index_digest must be 64 lowercase hex: {value!r}")
        return value

    @field_validator("example_count")
    @classmethod
    def _nonneg(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"example_count must be >= 0: {value}")
        return value
