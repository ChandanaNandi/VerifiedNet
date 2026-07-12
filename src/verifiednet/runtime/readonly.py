"""Read-only command executor.

Contract (Gate 3 Step 4):

- The ``target`` is NOT prepended to argv: the runner receives argv exactly as
  given (docker-exec adaptation per target is Gate 4 adapter work).
- Policy checks run BEFORE execution; a denial returns a ``DENIED_*`` result
  without executing, and denials are still transcripted (stage="completed").
- The transcript entry is appended AFTER execution (stage="completed"). If the
  append raises ``TranscriptWriteError``, the read result is still returned
  with ``transcript_ok=False`` — a read transcript failure marks the run
  incomplete downstream; it is visible, never swallowed, but does not raise.
- Timeout / binary-not-found are handled inside the runner (mapped to TIMEOUT /
  TARGET_NOT_FOUND). Any other exception from the runner propagates — nothing
  is silently swallowed.
- Ordering and time come only from ``RunContext`` (seq + injected clock), so
  tests are fully deterministic with ``FakeClock``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from verifiednet.common.errors import PolicyViolationError, TranscriptWriteError
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.policy import CommandPolicy, TargetPolicy
from verifiednet.runtime.process import ProcessRunner, RawResult
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.runtime.transcript import TranscriptEntry, TranscriptWriter

DEFAULT_MAX_OUTPUT_BYTES = 65536


def status_from_raw(raw: RawResult) -> ExecStatus:
    """Map a RawResult to its ExecStatus (shared with the mutation executor)."""
    if raw.timed_out:
        return ExecStatus.TIMEOUT
    if raw.not_found:
        return ExecStatus.TARGET_NOT_FOUND
    if raw.exit_code == 0:
        return ExecStatus.OK
    return ExecStatus.NONZERO_EXIT


class ReadOnlyExecutor:
    """Executes read-only (show) commands under command + target policy."""

    def __init__(
        self,
        runner: ProcessRunner,
        command_policy: CommandPolicy,
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

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        """Policy-check, execute, transcript, and return an ``ExecResult``."""
        seq = self._run_ctx.next_seq()
        argv_t = tuple(argv)
        started_at = self._run_ctx.now()

        try:
            self._command_policy.check(argv_t)
        except PolicyViolationError as exc:
            return self._denied(ExecStatus.DENIED_COMMAND, target, argv_t, seq, started_at, exc)
        try:
            self._target_policy.check(target)
        except PolicyViolationError as exc:
            return self._denied(ExecStatus.DENIED_TARGET, target, argv_t, seq, started_at, exc)

        raw = self._runner(argv_t, timeout_s, self._max_output_bytes)
        duration_s = (self._run_ctx.now() - started_at).total_seconds()
        status = status_from_raw(raw)
        transcript_ok = self._append_completed(
            seq, target, argv_t, status.value, started_at, duration_s
        )
        return ExecResult(
            status=status,
            target=target,
            argv=argv_t,
            exit_code=raw.exit_code,
            stdout=raw.stdout,
            stderr=raw.stderr,
            truncated=raw.truncated,
            duration_s=duration_s,
            seq=seq,
            transcript_ok=transcript_ok,
        )

    def _denied(
        self,
        status: ExecStatus,
        target: str,
        argv: tuple[str, ...],
        seq: int,
        started_at: datetime,
        reason: PolicyViolationError,
    ) -> ExecResult:
        duration_s = (self._run_ctx.now() - started_at).total_seconds()
        transcript_ok = self._append_completed(
            seq, target, argv, status.value, started_at, duration_s
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
        )

    def _append_completed(
        self,
        seq: int,
        target: str,
        argv: tuple[str, ...],
        status: str,
        started_at: datetime,
        duration_s: float,
    ) -> bool:
        entry = TranscriptEntry(
            seq=seq,
            mode="read",
            stage="completed",
            target=target,
            argv=argv,
            status=status,
            started_at=started_at,
            duration_s=duration_s,
        )
        try:
            self._transcript.append(entry)
        except TranscriptWriteError:
            return False
        return True
