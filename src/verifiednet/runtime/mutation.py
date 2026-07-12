"""Mutation command executor with a write-ahead transcript.

Provenance: the write-ahead ordering (pending entry durably recorded BEFORE the
mutation executes) is modeled on closcall's ``guarded_mutation`` pattern,
reimplemented from specification (closcall license unresolved).

Contract (Gate 3 Step 4, extended in Gate 4):

- Policy checks run FIRST; a denial returns a ``DENIED_*`` result without
  executing and is still transcripted (stage="completed").
- Gate 4 (additive): a caller may pass ``transport_argv`` and an ``invocation``.
  Policy validates the *logical* ``argv``; the runner executes ``transport_argv``
  (what actually runs). Both transcript entries and the result retain the same
  ``invocation`` so the pending and terminal entries pair by ``command_id``.
  When ``transport_argv`` is omitted the behaviour is exactly Gate 3's.
- A pending entry (stage="pending") is appended BEFORE execution. If that append
  raises ``TranscriptWriteError`` it is RE-RAISED and the mutation is blocked —
  no mutation without a durable write-ahead record.
- After execution a completion entry (stage="completed") is appended. If THAT
  append fails the result carries ``transcript_ok=False`` (the mutation already
  happened; the failure is visible, not swallowed).
- Unexpected runner exceptions propagate.

This module must remain SEPARATE from ``readonly.py``: the AST boundary guard
bans collectors from importing ``verifiednet.runtime.mutation`` specifically, so
mutation capability must never leak through the read path.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from verifiednet.common.errors import PolicyViolationError, TranscriptWriteError
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.invocation import CommandInvocation
from verifiednet.runtime.policy import MutationCommandPolicy, TargetPolicy
from verifiednet.runtime.process import ProcessRunner
from verifiednet.runtime.readonly import DEFAULT_MAX_OUTPUT_BYTES, status_from_raw
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.runtime.transcript import TranscriptEntry, TranscriptWriter


class MutationExecutor:
    """Executes mutating commands under policy with write-ahead transcripting."""

    def __init__(
        self,
        runner: ProcessRunner,
        command_policy: MutationCommandPolicy,
        target_policy: TargetPolicy,
        transcript: TranscriptWriter,
        run_ctx: RunContext,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._runner = runner
        self._command_policy = command_policy
        self._target_policy = target_policy
        self._transcript = transcript
        self._run_ctx = run_ctx
        self._max_output_bytes = max_output_bytes

    def run(
        self,
        target: str,
        argv: Sequence[str],
        timeout_s: float,
        *,
        transport_argv: Sequence[str] | None = None,
        invocation: CommandInvocation | None = None,
    ) -> ExecResult:
        """Policy-check the logical ``argv``, write-ahead, execute, complete."""
        seq = self._run_ctx.next_seq()
        logical_t = tuple(argv)
        exec_argv = tuple(transport_argv) if transport_argv is not None else logical_t
        started_at = self._run_ctx.now()

        try:
            self._command_policy.check(logical_t)
        except PolicyViolationError as exc:
            return self._denied(
                ExecStatus.DENIED_COMMAND, target, logical_t, seq, started_at, exc, invocation
            )
        try:
            self._target_policy.check(target)
        except PolicyViolationError as exc:
            return self._denied(
                ExecStatus.DENIED_TARGET, target, logical_t, seq, started_at, exc, invocation
            )

        # Write-ahead: a pending entry MUST land before the mutation executes.
        # TranscriptWriteError propagates — the mutation is blocked.
        self._transcript.append(
            self._entry(seq, "pending", target, exec_argv, "pending", started_at, 0.0, invocation)
        )

        raw = self._runner(exec_argv, timeout_s, self._max_output_bytes)
        duration_s = (self._run_ctx.now() - started_at).total_seconds()
        status = status_from_raw(raw)
        transcript_ok = self._append_completed(
            seq, target, exec_argv, status.value, started_at, duration_s, invocation
        )
        return ExecResult(
            status=status,
            target=target,
            argv=exec_argv,
            exit_code=raw.exit_code,
            stdout=raw.stdout,
            stderr=raw.stderr,
            truncated=raw.truncated,
            duration_s=duration_s,
            seq=seq,
            transcript_ok=transcript_ok,
            invocation=invocation,
        )

    def _denied(
        self,
        status: ExecStatus,
        target: str,
        argv: tuple[str, ...],
        seq: int,
        started_at: datetime,
        reason: PolicyViolationError,
        invocation: CommandInvocation | None,
    ) -> ExecResult:
        duration_s = (self._run_ctx.now() - started_at).total_seconds()
        transcript_ok = self._append_completed(
            seq, target, argv, status.value, started_at, duration_s, invocation
        )
        return ExecResult(
            status=status,
            target=target,
            argv=argv,
            exit_code=None,
            stdout="",
            stderr="",
            truncated=False,
            duration_s=duration_s,
            seq=seq,
            transcript_ok=transcript_ok,
            detail=str(reason),
            invocation=invocation,
        )

    def _append_completed(
        self,
        seq: int,
        target: str,
        argv: tuple[str, ...],
        status: str,
        started_at: datetime,
        duration_s: float,
        invocation: CommandInvocation | None,
    ) -> bool:
        entry = self._entry(
            seq, "completed", target, argv, status, started_at, duration_s, invocation
        )
        try:
            self._transcript.append(entry)
        except TranscriptWriteError:
            return False
        return True

    @staticmethod
    def _entry(
        seq: int,
        stage: Literal["pending", "completed"],
        target: str,
        argv: tuple[str, ...],
        status: str,
        started_at: datetime,
        duration_s: float,
        invocation: CommandInvocation | None,
    ) -> TranscriptEntry:
        return TranscriptEntry(
            seq=seq,
            mode="mutation",
            stage=stage,
            target=target,
            argv=argv,
            status=status,
            started_at=started_at,
            duration_s=duration_s,
            invocation=invocation,
        )
