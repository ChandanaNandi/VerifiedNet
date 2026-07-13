"""Canonical per-run artifact directory: write, read, and verify run records.

Low-level persistence only (imports ``verifiednet.schemas`` + ``verifiednet.common``,
plus the pure ledger/transcript data models). It persists ALREADY-PRODUCED,
already-verified run outputs — it does not own live execution, and it never
creates truth: no model output is stored as ground truth here.
"""

from verifiednet.artifacts.layout import (
    INCOMPLETE_MARKER,
    LAYOUT_SCHEMA_VERSION,
    ArtifactEntry,
    ArtifactHash,
    ArtifactHashIndex,
    ArtifactRole,
    ArtifactVerificationResult,
    CheckOutcome,
    RunLayout,
)
from verifiednet.artifacts.reader import LoadedRun, load_run
from verifiednet.artifacts.verify import (
    ArtifactIntegrityError,
    compute_run_digest,
    verify_run_dir,
)
from verifiednet.artifacts.writer import (
    ArtifactWriteError,
    WrittenRun,
    write_run_artifacts,
)

__all__ = [
    "INCOMPLETE_MARKER",
    "LAYOUT_SCHEMA_VERSION",
    "ArtifactEntry",
    "ArtifactHash",
    "ArtifactHashIndex",
    "ArtifactIntegrityError",
    "ArtifactRole",
    "ArtifactVerificationResult",
    "ArtifactWriteError",
    "CheckOutcome",
    "LoadedRun",
    "RunLayout",
    "WrittenRun",
    "compute_run_digest",
    "load_run",
    "verify_run_dir",
    "write_run_artifacts",
]
