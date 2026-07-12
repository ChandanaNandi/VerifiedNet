"""Collector foundations: protocols + evidence record construction (Gate 3 Step 6).

Import boundary (AST-enforced): collectors import ONLY
``verifiednet.runtime.results`` from the runtime package — never
``verifiednet.runtime.mutation`` and never ``verifiednet.faults``. The
executor is typed as a local ``ReadOnlyExec`` protocol so no executor class
is imported at all.

Evidence ids are CONTENT-DERIVED (``run_ctx.content_id`` over collector,
target, raw payload hash and phase) — provenance note: reimplemented from
specification to fix the closcall ``_emit`` id-collision (Gate 2 §4).

Error policy (Gate 3 Step 6): parse failures raise ``ParserError`` — there is
NO silent fallback. A non-OK ``ExecResult`` handed to a parsing collector also
raises ``ParserError`` with the status in the message; callers decide
rejection. (Reachability probing is the documented exception: a failed ping
is evidence, not a parse failure — see ``collectors.frr.reachability``.)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from verifiednet.common.errors import ParserError
from verifiednet.common.hashing import sha256_bytes
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.schemas.evidence import EvidenceRecord, EvidenceSource, Phase


class ReadOnlyExec(Protocol):
    """Minimal executor surface collectors rely on (read-only by construction)."""

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        """Execute *argv* on *target* under policy; failures are result statuses."""
        ...


class EvidenceCollector(Protocol):
    """A named collector producing one ``EvidenceRecord`` per ``collect`` call."""

    name: str

    def collect(self, phase: Phase) -> EvidenceRecord:
        """Gather evidence for *phase*; raises ``ParserError`` on unusable output."""
        ...


def require_ok(name: str, result: ExecResult) -> None:
    """Raise ``ParserError`` unless *result* has OK status.

    Non-OK execution cannot yield parseable evidence for command-output
    collectors; the status is surfaced loudly so callers decide rejection.
    """
    if result.status is not ExecStatus.OK:
        raise ParserError(
            f"collector {name}: exec status {result.status.value} "
            f"(target={result.target!r}, exit_code={result.exit_code}, "
            f"detail={result.detail!r})"
        )


def sorted_normalized(values: dict[str, str]) -> dict[str, str]:
    """Return *values* with keys in sorted order (deterministic normalized dicts)."""
    return {key: values[key] for key in sorted(values)}


def make_evidence_record(
    *,
    collector: str,
    target: str,
    command: Sequence[str],
    transcript_seq: int | None,
    trusted: bool,
    phase: Phase,
    raw_payload: str,
    normalized: dict[str, Any],
    run_ctx: RunContext,
) -> EvidenceRecord:
    """Build an ``EvidenceRecord`` with a content-derived evidence id.

    ``evidence_id`` is derived from (collector, target, raw payload sha256,
    phase) via canonical JSON — identical content yields identical ids across
    runs; distinct raw payloads always yield distinct ids (closcall ``_emit``
    collision fix, reimplemented from specification).
    """
    raw_sha256 = sha256_bytes(raw_payload.encode("utf-8"))
    evidence_id = run_ctx.content_id(
        "ev",
        {
            "collector": collector,
            "target": target,
            "raw_sha256": raw_sha256,
            "phase": phase,
        },
    )
    source = EvidenceSource(
        collector=collector,
        target=target,
        command=tuple(command),
        transcript_seq=transcript_seq,
        trusted=trusted,
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        phase=phase,
        source=source,
        raw_sha256=raw_sha256,
        raw_payload=raw_payload,
        normalized=normalized,
        captured_at=run_ctx.now(),
        run_seq=run_ctx.next_seq(),
    )
