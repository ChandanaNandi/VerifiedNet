"""Deterministic writer for an exported dataset (Gate 6.2 Part 3).

``write_dataset`` installs an ``ExportedDataset`` into a fresh output directory
with byte-for-byte determinism: canonical bytes, stable filenames, atomic writes
(temp → fsync → replace → dir fsync, reusing the run-artifact durability
helpers), and a ``.INCOMPLETE`` marker that is removed ONLY after an independent
``verify_dataset`` pass. Running it twice on identical source data produces
identical bytes.

It writes ONLY into the given output directory (never the verified run library),
so no authoritative run artifact is ever touched (ADR-0018).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.errors import VerifiedNetError
from verifiednet.datasets.export import (
    DATASET_INCOMPLETE_MARKER,
    DATASET_SPLITS_DIR,
    ExportedDataset,
)
from verifiednet.datasets.verifier import verify_dataset


class DatasetWriteError(VerifiedNetError):
    """Writing the exported dataset directory failed (collision, IO, verify)."""


@dataclass(frozen=True)
class WrittenDataset:
    """Result of writing one exported dataset directory."""

    root: Path
    dataset_digest: str
    file_count: int


def write_dataset(exported: ExportedDataset, out_dir: str | Path) -> WrittenDataset:
    """Write *exported* into *out_dir* deterministically; fail loudly on any error."""
    root = Path(out_dir)
    if root.exists() and any(root.iterdir()):
        raise DatasetWriteError(f"target dataset directory exists and is non-empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    (root / DATASET_SPLITS_DIR).mkdir(exist_ok=True)

    marker = root / DATASET_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)

    try:
        for rel, payload in exported.output_files():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, payload)

        result = verify_dataset(root)
        if not result.verified:
            # verify_dataset refuses while .INCOMPLETE is present; ignore only
            # that one check for the post-write gate, everything else must pass.
            hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
            if hard:
                detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
                raise DatasetWriteError(f"post-write verification failed: {detail}")
    except Exception:
        # Leave .INCOMPLETE in place; never report the directory as complete.
        raise

    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenDataset(
        root=root,
        dataset_digest=exported.manifest.dataset_digest,
        file_count=file_count,
    )
