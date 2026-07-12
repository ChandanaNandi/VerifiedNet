"""Shared schema base: strict, frozen, versioned, JSON-deterministic.

Rules for every VerifiedNet data schema (Gate 3 Step 2):
- explicit ``schema_version``;
- strict validation, unexpected fields forbidden;
- immutable (frozen) models;
- UTC-aware timestamps only (naive datetimes rejected);
- deterministic JSON via ``verifiednet.common.canonical`` (NOT model_dump_json).

Schemas import nothing from VerifiedNet implementation packages (AST-enforced);
the canonical serializer lives in ``common`` and takes models as *values*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


def _require_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware (UTC)")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_tz)]


class StrictModel(BaseModel):
    """Frozen, extra-forbidding, strictly-validating base model."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        validate_assignment=True,
    )
