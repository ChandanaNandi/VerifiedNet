"""VerifiedNet runtime: policy-guarded, transcripted command execution.

``readonly`` and ``mutation`` are deliberately SEPARATE modules — the AST
security guard bans collectors from importing ``verifiednet.runtime.mutation``
specifically, so mutation capability must never leak through the read path.
"""

from __future__ import annotations

from verifiednet.runtime.mutation import MutationExecutor
from verifiednet.runtime.policy import CommandPolicy, MutationCommandPolicy, TargetPolicy
from verifiednet.runtime.process import ProcessRunner, RawResult, default_runner
from verifiednet.runtime.readonly import ReadOnlyExecutor
from verifiednet.runtime.results import ExecResult, ExecStatus
from verifiednet.runtime.transcript import (
    FileTranscript,
    InMemoryTranscript,
    TranscriptEntry,
    TranscriptWriter,
)

__all__ = [
    "CommandPolicy",
    "ExecResult",
    "ExecStatus",
    "FileTranscript",
    "InMemoryTranscript",
    "MutationCommandPolicy",
    "MutationExecutor",
    "ProcessRunner",
    "RawResult",
    "ReadOnlyExecutor",
    "TargetPolicy",
    "TranscriptEntry",
    "TranscriptWriter",
    "default_runner",
]
