"""Canonical per-run artifact layout — filenames, roles, and artifact-owned models.

The layout is versioned and deterministic: one run per directory, the directory
name IS the ``run_id``, every truth-bearing file has a fixed name and semantic
role, and absent phases are simply absent (never faked as empty bundles).

This module owns ONLY low-level persistence schemas. It imports nothing but
``verifiednet.schemas`` and ``verifiednet.common`` (AST-enforced); it never
imports live execution, collectors, verifiers, incident builders, or scenario
implementations.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator

from verifiednet.schemas.base import StrictModel, UtcDatetime

LAYOUT_SCHEMA_VERSION = 1

# Fixed filenames (the layout is not machine-random).
LAYOUT_FILE = "layout.json"
INCIDENT_FILE = "incident.json"
RUN_MANIFEST_FILE = "run_manifest.json"
ENVIRONMENT_MANIFEST_FILE = "environment_manifest.json"
TRANSCRIPT_FILE = "transcript.jsonl"
LEDGER_FILE = "ledger.jsonl"
EVIDENCE_DIR = "evidence"
EVIDENCE_BASELINE_FILE = "evidence/baseline.json"
EVIDENCE_ONSET_FILE = "evidence/onset.json"
EVIDENCE_RECOVERY_FILE = "evidence/recovery.json"
HASH_INDEX_FILE = "hashes.json"
VERIFICATION_REPORT_FILE = "verification_report.json"
INCOMPLETE_MARKER = ".INCOMPLETE"

#: Files that are META (never appear in the hash index or the run digest):
#: the hash index itself, the verification report, and the construction marker.
META_FILES = frozenset({HASH_INDEX_FILE, VERIFICATION_REPORT_FILE, INCOMPLETE_MARKER})

_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REL_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")


class ArtifactRole(StrEnum):
    """Semantic role of a canonical artifact file."""

    LAYOUT = "layout"
    INCIDENT = "incident"
    RUN_MANIFEST = "run_manifest"
    ENVIRONMENT_MANIFEST = "environment_manifest"
    TRANSCRIPT = "transcript"
    LEDGER = "ledger"
    EVIDENCE_BASELINE = "evidence_baseline"
    EVIDENCE_ONSET = "evidence_onset"
    EVIDENCE_RECOVERY = "evidence_recovery"
    HASH_INDEX = "hash_index"
    VERIFICATION_REPORT = "verification_report"


#: Role -> fixed relative path for the single-instance roles.
ROLE_TO_PATH: dict[ArtifactRole, str] = {
    ArtifactRole.LAYOUT: LAYOUT_FILE,
    ArtifactRole.INCIDENT: INCIDENT_FILE,
    ArtifactRole.RUN_MANIFEST: RUN_MANIFEST_FILE,
    ArtifactRole.ENVIRONMENT_MANIFEST: ENVIRONMENT_MANIFEST_FILE,
    ArtifactRole.TRANSCRIPT: TRANSCRIPT_FILE,
    ArtifactRole.LEDGER: LEDGER_FILE,
    ArtifactRole.EVIDENCE_BASELINE: EVIDENCE_BASELINE_FILE,
    ArtifactRole.EVIDENCE_ONSET: EVIDENCE_ONSET_FILE,
    ArtifactRole.EVIDENCE_RECOVERY: EVIDENCE_RECOVERY_FILE,
    ArtifactRole.HASH_INDEX: HASH_INDEX_FILE,
    ArtifactRole.VERIFICATION_REPORT: VERIFICATION_REPORT_FILE,
}

#: JSONL (append-oriented) roles; every other truth-bearing file is canonical JSON.
JSONL_ROLES = frozenset({ArtifactRole.TRANSCRIPT, ArtifactRole.LEDGER})


def is_safe_run_id(run_id: str) -> bool:
    """A run_id must be a single safe path component (no traversal, no separators)."""
    return bool(_RUN_ID_RE.match(run_id)) and run_id not in (".", "..")


def is_safe_relative_path(path: str) -> bool:
    """Relative, forward-slash, no ``..`` segments, no leading slash."""
    if path.startswith("/") or "\\" in path or not _REL_PATH_RE.match(path):
        return False
    return ".." not in path.split("/")


class ArtifactEntry(StrictModel):
    """One declared artifact file: its relative path and semantic role."""

    relative_path: str
    role: ArtifactRole

    @field_validator("relative_path")
    @classmethod
    def _validate_rel(cls, value: str) -> str:
        if not is_safe_relative_path(value):
            raise ValueError(f"unsafe or absolute artifact path: {value!r}")
        return value


class RunLayout(StrictModel):
    """``layout.json`` — the declared structure of a run directory."""

    layout_schema_version: Literal[1] = 1
    run_id: str
    acceptance_status: Literal["accepted", "rejected", "incomplete"]
    artifacts: tuple[ArtifactEntry, ...] = Field(min_length=1)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        if not is_safe_run_id(value):
            raise ValueError(f"unsafe run_id for a directory name: {value!r}")
        return value


class ArtifactHash(StrictModel):
    """A truth-bearing file's relative path, role, SHA-256, and byte size."""

    relative_path: str
    role: ArtifactRole
    sha256: str
    size: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def _validate_rel(cls, value: str) -> str:
        if not is_safe_relative_path(value):
            raise ValueError(f"unsafe or absolute artifact path: {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _validate_sha(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"sha256 must be 64 lowercase hex chars: {value!r}")
        return value


class ArtifactHashIndex(StrictModel):
    """``hashes.json`` — every truth-bearing file's hash plus the run digest."""

    schema_version: Literal[1] = 1
    run_id: str
    run_digest: str
    entries: tuple[ArtifactHash, ...] = Field(min_length=1)

    @field_validator("run_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError(f"run_digest must be 64 lowercase hex chars: {value!r}")
        return value


class CheckOutcome(StrictModel):
    """One integrity/consistency rule result."""

    rule: str
    passed: bool
    detail: str = ""


class ArtifactVerificationResult(StrictModel):
    """``verification_report.json`` — structured integrity result (not a bool)."""

    schema_version: Literal[1] = 1
    run_id: str
    verified: bool
    run_digest: str
    checks: tuple[CheckOutcome, ...] = Field(min_length=1)
    verified_at: UtcDatetime

    @property
    def failures(self) -> tuple[CheckOutcome, ...]:
        return tuple(c for c in self.checks if not c.passed)
