"""VerificationCheck and VerificationResult.

Provenance: verdict/predicate design modeled on closcall ``evidence/claims.py``
(commit d192bf3) — REIMPLEMENTED FROM SPECIFICATION with the Gate 2.5 correction:
trusted-evidence semantics are enforced (untrusted evidence can never verify a
claim), and UNKNOWN/INSUFFICIENT never count as verified.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from verifiednet.schemas.base import StrictModel, UtcDatetime
from verifiednet.schemas.evidence import PhaseField


class Verdict(StrEnum):
    PASS = "pass"  # noqa: S105 - verdict label, not a credential
    FAIL = "fail"
    UNKNOWN = "unknown"
    INSUFFICIENT = "insufficient"


class Predicate(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN_SET = "in_set"
    NOT_IN_SET = "not_in_set"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    ANY = "any"  # at least one trusted evidence value exists for the metric


class VerificationCheck(StrictModel):
    schema_version: Literal[1] = 1
    check_id: str = Field(min_length=1, max_length=128)
    claim: str = Field(min_length=1)  # human-readable statement of the fact
    subject: str  # node or session the claim is about
    metric: str  # normalized evidence key, e.g. "bgp.peer.state"
    predicate: Predicate
    expected: tuple[str, ...] = Field(default_factory=tuple)  # values; empty for ANY
    phase: PhaseField
    require_trusted: bool = True


class VerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    check_id: str
    verdict: Verdict
    phase: PhaseField
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    observed: tuple[str, ...] = Field(default_factory=tuple)
    detail: str = ""
    evaluated_at_seq: int = Field(ge=1)
    evaluated_at: UtcDatetime

    @property
    def committable(self) -> bool:
        """Only PASS commits toward ground truth (UNKNOWN/INSUFFICIENT never do)."""
        return self.verdict is Verdict.PASS
