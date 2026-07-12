"""Runtime execution results — the runtime-owned ``ExecResult`` contract.

``ExecResult`` is owned by the runtime package (it is not a ``schemas`` model)
and is JSON-serializable: it round-trips through ``model_dump_json`` /
``model_validate_json`` and hashes deterministically via
``verifiednet.common.hashing.sha256_canonical`` (contract-tested in
``tests/contract/test_exec_result.py``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExecStatus(StrEnum):
    """Terminal status of one executed (or denied) command."""

    OK = "ok"
    DENIED_COMMAND = "denied_command"
    DENIED_TARGET = "denied_target"
    TIMEOUT = "timeout"
    TARGET_NOT_FOUND = "target_not_found"
    NONZERO_EXIT = "nonzero_exit"
    INTERNAL_ERROR = "internal_error"


class ExecResult(BaseModel):
    """Immutable outcome of a single executor invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ExecStatus
    target: str
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool = False
    duration_s: float
    seq: int = Field(ge=1)
    transcript_ok: bool = True
    detail: str = ""
