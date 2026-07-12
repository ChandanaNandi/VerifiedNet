"""LabBackend — the behavioral interface every lab backend must satisfy.

Gate 2.5 W2: interfaces live in their owning packages, so the lab backend
protocol is defined here in ``verifiednet.labs`` (not in a central "interfaces"
module). Gate 3 ships NO live backend implementation — this module contains the
typed contract only; the first concrete backend (FRR-on-compose) arrives at
Gate 4 together with live execution.

Implementations are expected to be deterministic where the contract demands it
(``topology()`` returns the exact spec the lab was built from) and honest where
it does not (``health_check()`` reflects real backend state).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from verifiednet.schemas.topology import TopologySpec

if TYPE_CHECKING:
    from verifiednet.runtime.results import ExecResult


class LabBackend(Protocol):
    """Behavioral contract for a lab backend (Gate 3: contract only, no impl)."""

    def start(self) -> None:
        """Bring the lab up. Idempotent: starting a running lab is a no-op.

        Raises a domain error (never a bare backend exception) if the lab
        cannot reach a running state.
        """
        ...

    def stop(self) -> None:
        """Tear the lab down and release all backend resources.

        Idempotent: stopping a stopped lab is a no-op.
        """
        ...

    def reset(self) -> None:
        """Return the lab to its pristine post-``start`` state.

        All mutations (injected faults, config edits) are discarded; the
        topology and rendered configuration are exactly those of ``topology()``.
        """
        ...

    def health_check(self) -> bool:
        """Report whether every node of the lab is up and responsive.

        Returns ``True`` only when all nodes accept read-only commands;
        never raises for an unhealthy-but-reachable lab.
        """
        ...

    def topology(self) -> TopologySpec:
        """Return the exact ``TopologySpec`` this lab was built from."""
        ...

    def execute_readonly(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        """Run a policy-checked read-only command on *target*.

        Never raises for command failure — failures are encoded in the
        returned ``ExecResult`` status (Gate 3: results, not exceptions).
        Mutation-capable execution is deliberately absent from this protocol.
        """
        ...

    def capture_environment_metadata(self) -> dict[str, str]:
        """Return reproducibility metadata (image digests, versions, host info).

        Keys and values are plain strings so the mapping can feed the
        ``EnvironmentManifest`` without transformation.
        """
        ...
