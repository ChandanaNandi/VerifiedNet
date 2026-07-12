"""CommandInvocation — the single runtime-owned record binding a logical network
command to the host transport argv that actually delivers it (Gate 4).

Gate 3 executed the same argv it validated. Gate 4 introduces a transport split:
the *logical* command (e.g. ``vtysh -c "show ip bgp summary json"``) is what the
safety policy validates, while the *transport* command
(``docker compose -p <project> exec -T <service> vtysh -c "…"``) is what the
process runner executes. Both must be retained under one stable identity so a
transcript pending entry and its terminal entry can be paired.

Design (Gate 4 freeze-check corrections F2/F3):

- ``command_id`` is deterministic — derived from the RunContext, target, logical
  command, and an operation sequence — never a random UUID.
- Sequence numbers on transcript entries remain globally monotonic but are NOT
  the pairing mechanism; ``command_id`` is.
- ``CommandInvocation`` is the ONE place both argvs live; ``ExecResult`` and
  ``TranscriptEntry`` reference it via an optional field rather than duplicating
  logical/transport command tuples across models.

Compatibility: this type is referenced only by OPTIONAL fields with a ``None``
default on the released v0.3 ``ExecResult`` / ``TranscriptEntry`` models, so
v0.3-serialized records continue to validate unchanged (contract-tested).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CommandInvocation(BaseModel):
    """Immutable identity + logical/transport argv pair for one command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: str
    target: str
    logical_argv: tuple[str, ...]
    transport_argv: tuple[str, ...]
