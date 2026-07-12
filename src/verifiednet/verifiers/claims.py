"""Deterministic claim verification over evidence bundles.

Provenance: verdict semantics modeled on closcall ``evidence/claims.py``
(commit d192bf3), REIMPLEMENTED FROM SPECIFICATION — closcall has no published
license. Gate 2.5 corrections applied:

- trusted-evidence enforcement: when ``check.require_trusted`` is set (the
  default), untrusted evidence is skipped and can NEVER verify a claim; a
  metric observed only in untrusted records yields INSUFFICIENT, not PASS;
- the ANY predicate is explicitly evaluated (and tested);
- UNKNOWN and INSUFFICIENT never count as verified (see
  ``VerificationResult.committable``).

Verifiers consume evidence strictly as data: this package never imports
``verifiednet.runtime``, ``verifiednet.labs`` or ``verifiednet.collectors``
(AST-enforced by ``tests/security/test_import_boundaries.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from verifiednet.common.runctx import RunContext
from verifiednet.schemas.evidence import EvidenceBundle
from verifiednet.schemas.verification import (
    Predicate,
    Verdict,
    VerificationCheck,
    VerificationResult,
)


class Verifier(Protocol):
    """Anything that can evaluate a VerificationCheck against evidence."""

    def verify(
        self, check: VerificationCheck, bundles: Sequence[EvidenceBundle]
    ) -> VerificationResult: ...


class ClaimVerifier:
    """Evaluate checks against evidence bundles, deterministically."""

    def __init__(self, run_ctx: RunContext) -> None:
        self._run_ctx = run_ctx

    def verify(
        self, check: VerificationCheck, bundles: Sequence[EvidenceBundle]
    ) -> VerificationResult:
        observed: list[str] = []
        evidence_ids: list[str] = []
        untrusted_hits = 0
        for bundle in bundles:
            for record in bundle.records:
                if check.metric not in record.normalized:
                    continue
                if check.require_trusted and not record.source.trusted:
                    untrusted_hits += 1
                    continue
                observed.append(str(record.normalized[check.metric]))
                evidence_ids.append(record.evidence_id)

        if not observed:
            if untrusted_hits:
                detail = (
                    f"{untrusted_hits} untrusted observation(s) ignored; "
                    "untrusted evidence can never verify a claim"
                )
            else:
                detail = f"no evidence observed for metric {check.metric!r}"
            return self._result(check, Verdict.INSUFFICIENT, (), (), detail)

        if check.predicate is Predicate.ANY:
            return self._result(
                check, Verdict.PASS, evidence_ids, observed, "observed values recorded"
            )

        if not check.expected:
            return self._result(
                check, Verdict.UNKNOWN, evidence_ids, observed, "no expected value"
            )

        verdict, detail = self._evaluate(check, observed)
        return self._result(check, verdict, evidence_ids, observed, detail)

    def _evaluate(self, check: VerificationCheck, observed: list[str]) -> tuple[Verdict, str]:
        expected = check.expected
        if check.predicate is Predicate.EQUALS:
            matches = [value == expected[0] for value in observed]
            if all(matches):
                return Verdict.PASS, ""
            if any(matches):
                return (
                    Verdict.FAIL,
                    "contradictory evidence: matching and non-matching observations",
                )
            return Verdict.FAIL, f"observed {observed!r} != expected {expected[0]!r}"
        if check.predicate is Predicate.NOT_EQUALS:
            if all(value != expected[0] for value in observed):
                return Verdict.PASS, ""
            return Verdict.FAIL, f"observed {observed!r} contains forbidden {expected[0]!r}"
        if check.predicate is Predicate.IN_SET:
            if all(value in expected for value in observed):
                return Verdict.PASS, ""
            return Verdict.FAIL, f"observed {observed!r} not all in {expected!r}"
        if check.predicate is Predicate.NOT_IN_SET:
            if all(value not in expected for value in observed):
                return Verdict.PASS, ""
            return Verdict.FAIL, f"observed {observed!r} intersects forbidden {expected!r}"
        if check.predicate in (Predicate.GREATER_THAN, Predicate.LESS_THAN):
            try:
                threshold = float(expected[0])
                numbers = [float(value) for value in observed]
            except ValueError:
                return (
                    Verdict.UNKNOWN,
                    f"non-numeric comparison: observed {observed!r} vs {expected[0]!r}",
                )
            if check.predicate is Predicate.GREATER_THAN:
                ok = all(number > threshold for number in numbers)
            else:
                ok = all(number < threshold for number in numbers)
            if ok:
                return Verdict.PASS, ""
            return Verdict.FAIL, f"observed {observed!r} fails {check.predicate.value} {threshold}"
        raise AssertionError(f"unhandled predicate: {check.predicate!r}")  # pragma: no cover

    def _result(
        self,
        check: VerificationCheck,
        verdict: Verdict,
        evidence_ids: Sequence[str],
        observed: Sequence[str],
        detail: str,
    ) -> VerificationResult:
        return VerificationResult(
            check_id=check.check_id,
            verdict=verdict,
            phase=check.phase,
            evidence_ids=tuple(evidence_ids),
            observed=tuple(observed),
            detail=detail,
            evaluated_at_seq=self._run_ctx.next_seq(),
            evaluated_at=self._run_ctx.now(),
        )
