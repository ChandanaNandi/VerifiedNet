"""Pure bounded-polling state machine.

Provenance: bounded polling pattern from sonic-troubleshooting-agent
``faults/bgp_asn_mismatch.py::wait_for_state`` (MIT, commit eb4c818) — copy
with modifications: fully typed, clock and sleep are injected (no wall clock,
no real sleeping in tests), and consecutive-confirmation was added per the
Gate 2.5 W9 correction (a single flapping success must not satisfy a check).

``poll_until`` never raises on timeout — like the STA poller it returns an
outcome and the CALLER decides whether an unsatisfied outcome is an error.
Exceptions raised by ``sample`` are NEVER swallowed; they propagate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PollOutcome:
    """Result of one bounded polling run."""

    satisfied: bool
    attempts: int
    consecutive_successes: int
    elapsed_s: float
    last_detail: str = ""


def poll_until(
    sample: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    consecutive: int = 2,
) -> PollOutcome:
    """Poll ``sample`` until it returns True ``consecutive`` times in a row.

    A False sample resets the success streak. The deadline is computed on the
    injected monotonic clock; when it passes, the current (unsatisfied)
    outcome is returned — never raised. Exceptions from ``sample`` propagate.
    """
    if consecutive < 1:
        raise ValueError(f"consecutive must be >= 1, got {consecutive}")
    if timeout_s <= 0 or interval_s <= 0:
        raise ValueError("timeout_s and interval_s must be positive")

    start = monotonic()
    deadline = start + timeout_s
    attempts = 0
    streak = 0
    last_detail = ""
    while True:
        attempts += 1
        if sample():
            streak += 1
            if streak >= consecutive:
                return PollOutcome(
                    satisfied=True,
                    attempts=attempts,
                    consecutive_successes=streak,
                    elapsed_s=monotonic() - start,
                    last_detail=last_detail,
                )
        else:
            streak = 0
            last_detail = f"attempt {attempts} unsatisfied"
        if monotonic() >= deadline:
            return PollOutcome(
                satisfied=False,
                attempts=attempts,
                consecutive_successes=streak,
                elapsed_s=monotonic() - start,
                last_detail=last_detail,
            )
        sleep(interval_s)
