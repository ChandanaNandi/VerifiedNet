"""Shared domain errors. One hierarchy; no silent swallowing anywhere."""

from __future__ import annotations


class VerifiedNetError(Exception):
    """Base for all VerifiedNet domain errors."""


class PolicyViolationError(VerifiedNetError):
    """A command or target was denied by policy."""


class TranscriptWriteError(VerifiedNetError):
    """The command transcript could not be durably written."""


class ParserError(VerifiedNetError):
    """Collector output could not be parsed; never silently ignored."""


class PreconditionFailedError(VerifiedNetError):
    """Scenario preconditions did not hold."""


class InjectFailedError(VerifiedNetError):
    """Fault injection command failed."""


class OnsetNotVerifiedError(VerifiedNetError):
    """Fault onset could not be verified within its deadline."""


class RestoreFailedError(VerifiedNetError):
    """Restoration failed; ledger remains in RESTORING for visibility."""


class RecoveryNotVerifiedError(VerifiedNetError):
    """Recovery could not be verified within its deadline."""


class LedgerError(VerifiedNetError):
    """Ledger corruption or IO failure."""


class TornLedgerLineError(LedgerError):
    """The final ledger line is torn/partial; recovered records are attached."""

    def __init__(self, message: str, recovered: object) -> None:
        super().__init__(message)
        self.recovered = recovered


class PhaseTransitionError(LedgerError):
    """An illegal fault-lifecycle phase transition was attempted."""


class SealedBundleError(VerifiedNetError):
    """Mutation of a sealed EvidenceBundle was attempted."""
