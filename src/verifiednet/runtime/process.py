"""Subprocess boundary — the ONLY module in VerifiedNet allowed to import subprocess.

AST-enforced by ``tests/security/test_import_boundaries.py``. Rules honoured here:

- ``shell`` is NEVER passed to ``subprocess.run`` (argv-list execution only);
- a positive timeout is MANDATORY on every call (``ValueError`` otherwise);
- output is bounded: stdout/stderr are each truncated to ``max_output_bytes``
  (bytes-aware; UTF-8 encode/slice/decode with ``errors="replace"``) and the
  ``RawResult.truncated`` flag records whether truncation happened;
- no retries — timeout behaviour is subprocess's own monotonic timeout;
- binaries are resolved via PATH by the caller-supplied argv; policy layers
  (``verifiednet.runtime.policy``) decide what may run, not this module.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from typing import NamedTuple


class RawResult(NamedTuple):
    """Low-level process outcome, before status mapping in the executors."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    not_found: bool
    truncated: bool = False


ProcessRunner = Callable[[Sequence[str], float, int], RawResult]
"""Callable signature: ``(argv, timeout_s, max_output_bytes) -> RawResult``."""


def _truncate(text: str, max_output_bytes: int) -> tuple[str, bool]:
    """Bytes-aware truncation; returns ``(text, was_truncated)``."""
    data = text.encode("utf-8", errors="replace")
    if len(data) <= max_output_bytes:
        return text, False
    return data[:max_output_bytes].decode("utf-8", errors="replace"), True


def _coerce_text(value: object) -> str:
    """Best-effort str coercion for partial capture on timeout."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def default_runner(argv: Sequence[str], timeout_s: float, max_output_bytes: int) -> RawResult:
    """Run ``argv`` as a process list. No shell, mandatory timeout, bounded output."""
    if isinstance(argv, str):
        raise TypeError("argv must be a sequence of argument strings, not a str")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s is mandatory and must be > 0, got {timeout_s!r}")
    if max_output_bytes <= 0:
        raise ValueError(f"max_output_bytes must be > 0, got {max_output_bytes!r}")
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial, truncated = _truncate(_coerce_text(exc.stdout), max_output_bytes)
        return RawResult(
            exit_code=None,
            stdout=partial,
            stderr="",
            timed_out=True,
            not_found=False,
            truncated=truncated,
        )
    except FileNotFoundError:
        return RawResult(
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
            not_found=True,
            truncated=False,
        )
    stdout, out_truncated = _truncate(completed.stdout, max_output_bytes)
    stderr, err_truncated = _truncate(completed.stderr, max_output_bytes)
    return RawResult(
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        not_found=False,
        truncated=out_truncated or err_truncated,
    )
