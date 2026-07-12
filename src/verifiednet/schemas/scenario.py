"""ScenarioDefinition — what was asked (vs FaultInjection: what happened)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from verifiednet.schemas.base import StrictModel


class ScenarioTimeouts(StrictModel):
    precondition_s: float = Field(gt=0, le=600)
    onset_s: float = Field(gt=0, le=600)
    recovery_s: float = Field(gt=0, le=600)
    command_s: float = Field(gt=0, le=120)
    poll_interval_s: float = Field(gt=0, le=60)


class ScenarioDefinition(StrictModel):
    schema_version: Literal[1] = 1
    scenario_id: str = Field(min_length=1, max_length=128)
    family: str = Field(min_length=1, max_length=64)  # e.g. "bgp"
    template_id: str = Field(min_length=1, max_length=128)  # e.g. "bgp_remote_as_mismatch"
    version: int = Field(ge=1)
    parameters: dict[str, str | int] = Field(default_factory=dict)
    timeouts: ScenarioTimeouts
