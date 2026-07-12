"""RunContext — the single authority for run identity, sequence, clock and ids.

Gate 2.5 W11/W12 (§12): run-level ordering comes only from RunContext sequence
numbers; capture timestamps are data, never ordering. Inner artifact identifiers
are deterministic: content-derived (hash prefix) or ``run_id`` + sequence — never
random UUIDs, never wall-clock-derived.

The clock is injected so tests are fully deterministic.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from datetime import UTC, datetime

from verifiednet.common.hashing import sha256_canonical

_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_.]{2,63}$")


def utc_now() -> datetime:
    """Default wall clock (UTC, tz-aware)."""
    return datetime.now(tz=UTC)


class RunContext:
    """Authoritative source of run_id, sequence numbers, clock and inner ids."""

    def __init__(self, run_id: str, clock: Callable[[], datetime] = utc_now) -> None:
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        self.run_id = run_id
        self._clock = clock
        self._seq = itertools.count(1)

    def next_seq(self) -> int:
        """Monotonically increasing sequence number (starts at 1)."""
        return next(self._seq)

    def now(self) -> datetime:
        """Run-level clock access; always tz-aware UTC."""
        stamp = self._clock()
        if stamp.tzinfo is None:
            raise ValueError("RunContext clock returned a naive datetime")
        return stamp.astimezone(UTC)

    def content_id(self, prefix: str, payload: object) -> str:
        """Deterministic content-derived identifier: ``prefix-<sha256[:16]>``."""
        return f"{prefix}-{sha256_canonical(payload)[:16]}"

    def seq_id(self, prefix: str, seq: int) -> str:
        """Deterministic sequence-derived identifier: ``prefix-<run_id>-<seq:06d>``."""
        return f"{prefix}-{self.run_id}-{seq:06d}"
