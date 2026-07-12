"""FaultScenario protocol and the executor protocol faults depend on.

Lifecycle contract (see ``verifiednet.faults.ledger.LEGAL_TRANSITIONS``)::

    PENDING -> PRECHECKED -> INJECTING -> INJECTED -> ONSET_VERIFIED
        -> RESTORING -> RESTORED -> RECOVERY_VERIFIED

with two documented extras: INJECTED -> RESTORING (restore without onset
verification, e.g. when onset never satisfied and the caller cleans up) and
INJECTING -> RESTORING (the injection command itself failed midway; the
mutation-failure recovery path must still be able to attempt restoration).

Idempotency rules:

- ``inject()`` called twice fails loudly with ``PhaseTransitionError`` — a
  second injection would corrupt the before/after record;
- ``restore()`` after the ledger reached RESTORED (or RECOVERY_VERIFIED) is a
  safe no-op returning the previously produced ``RestorationMetadata``
  without issuing any further mutation commands.

Timeout ownership: the scenario owns phase deadlines (``ScenarioTimeouts``:
precondition/onset/recovery windows and poll interval); the executor owns
per-command timeouts (``timeouts.command_s`` is passed to every
``MutationExec.run`` call, and the runtime enforces it on the subprocess).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from verifiednet.common.errors import PreconditionFailedError
from verifiednet.runtime.results import ExecResult
from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.incident import RestorationMetadata
from verifiednet.schemas.verification import VerificationResult


class MutationExec(Protocol):
    """The only executor capability faults depend on (runtime provides it)."""

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult: ...


class PreconditionResultsError(PreconditionFailedError):
    """PreconditionFailedError that carries the failing verification results.

    The caller uses ``results`` to build the rejected IncidentRecord (the
    baseline evidence and verdicts are retained even on rejection).
    """

    def __init__(self, message: str, results: tuple[VerificationResult, ...]) -> None:
        super().__init__(message)
        self.results = results


class FaultScenario(Protocol):
    """One injectable, verifiable, restorable fault."""

    def validate_preconditions(self) -> tuple[VerificationResult, ...]: ...

    def inject(self) -> FaultInjection: ...

    def verify_onset(self) -> tuple[VerificationResult, ...]: ...

    def restore(self) -> RestorationMetadata: ...

    def verify_recovery(self) -> tuple[VerificationResult, ...]: ...
