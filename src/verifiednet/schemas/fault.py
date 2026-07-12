"""FaultInjection — the recorded mutation (ground-truth input, never model output)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from verifiednet.schemas.base import StrictModel, UtcDatetime


class FaultInjection(StrictModel):
    schema_version: Literal[1] = 1
    scenario_id: str
    template_id: str
    target_node: str
    target_session: str
    method: str  # e.g. "vtysh-remote-as"
    parameter_name: str  # e.g. "remote_as"
    before_value: str  # exact pre-injection value (Gate 3 Step 8 requirement)
    after_value: str  # exact post-injection value
    transcript_refs: tuple[int, ...] = Field(default_factory=tuple)  # transcript seq numbers
    injected_at_seq: int = Field(ge=1)
    injected_at: UtcDatetime
