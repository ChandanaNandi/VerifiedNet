"""GroundTruth — assembled ONLY from injected-fault metadata and deterministic
verifier verdicts (Principles 11-12). No free text, no model output, ever."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from verifiednet.schemas.base import StrictModel
from verifiednet.schemas.fault import FaultInjection
from verifiednet.schemas.verification import VerificationResult


class GroundTruth(StrictModel):
    schema_version: Literal[1] = 1
    oracle_version: str = Field(min_length=1)
    fault: FaultInjection
    verdicts: tuple[VerificationResult, ...] = Field(min_length=1)
    accepted_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    root_cause_label: str = Field(min_length=1)  # machine label, e.g. "bgp_remote_as_mismatch"

    @model_validator(mode="after")
    def _no_untrusted_truth(self) -> GroundTruth:
        # The label must be a machine identifier, not prose: forbid whitespace.
        if any(ch.isspace() for ch in self.root_cause_label):
            raise ValueError("root_cause_label must be a machine label, not free text")
        return self
