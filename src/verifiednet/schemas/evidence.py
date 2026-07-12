"""EvidenceRecord and EvidenceBundle.

Evidence ids are content-derived (fixes the closcall ``_emit`` id-collision noted
in Gate 2 §4). Bundles are immutable models; ``with_record`` returns a new bundle
and raises once sealed (Gate 3 Step 2: EvidenceBundle must reject mutation after
sealing).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from verifiednet.schemas.base import StrictModel, UtcDatetime

Phase = Literal["baseline", "onset", "recovery", "precondition"]


class EvidenceSource(StrictModel):
    collector: str  # e.g. "frr.bgp_summary"
    target: str  # node name
    command: tuple[str, ...] = Field(default_factory=tuple)
    transcript_seq: int | None = None
    trusted: bool = True  # deterministic collector output; model output is NEVER trusted


class EvidenceRecord(StrictModel):
    schema_version: Literal[1] = 1
    evidence_id: str  # content-derived: "ev-<sha256[:16]>"
    phase: Phase
    source: EvidenceSource
    raw_sha256: str
    raw_payload: str  # verbatim stdout/output (bounded upstream)
    normalized: dict[str, Any] = Field(default_factory=dict)
    captured_at: UtcDatetime
    run_seq: int = Field(ge=1)


class EvidenceBundle(StrictModel):
    schema_version: Literal[1] = 1
    bundle_id: str
    phase: Phase
    records: tuple[EvidenceRecord, ...] = Field(default_factory=tuple)
    sealed: bool = False

    def with_record(self, record: EvidenceRecord) -> EvidenceBundle:
        """Return a new bundle including *record*; forbidden once sealed."""
        if self.sealed:
            # Local import is not possible (schemas may not import common);
            # sealing violations raise a plain ValueError subclass semantics.
            raise SealedBundleViolation(self.bundle_id)
        if record.phase != self.phase:
            raise ValueError(
                f"record phase {record.phase!r} does not match bundle phase {self.phase!r}"
            )
        return self.model_copy(update={"records": (*self.records, record)})

    def seal(self) -> EvidenceBundle:
        """Return the sealed (append-forbidden) form of this bundle."""
        return self.model_copy(update={"sealed": True})

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(r.evidence_id for r in self.records)


class SealedBundleViolation(ValueError):
    """Raised when appending to a sealed EvidenceBundle."""

    def __init__(self, bundle_id: str) -> None:
        super().__init__(f"bundle {bundle_id} is sealed; mutation rejected")
        self.bundle_id = bundle_id
