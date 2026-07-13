"""Reader for an exported dataset (Gate 6.2 Part 3).

``read_dataset`` verifies the dataset first (``verify_dataset``) and refuses to
return anything if verification fails — it FAILS CLOSED. On success it
reconstructs the immutable in-memory corpus: the manifest plus the assigned
examples grouped by partition, sorted by ``example_id``. Read-only: it never
writes, mutates a run, or executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from verifiednet.common.errors import VerifiedNetError
from verifiednet.datasets.export import SPLIT_FILE_BY_PARTITION, parse_split_bytes
from verifiednet.datasets.models import (
    AssignedDatasetExample,
    DatasetManifest,
    DatasetPartition,
)


class DatasetReadError(VerifiedNetError):
    """An exported dataset could not be read (verification failed / corrupt)."""


@dataclass(frozen=True)
class LoadedDataset:
    """A verified, reconstructed exported dataset (immutable)."""

    manifest: DatasetManifest
    examples: tuple[AssignedDatasetExample, ...]
    by_partition: dict[DatasetPartition, tuple[AssignedDatasetExample, ...]]


def read_dataset(dataset_dir: str | Path) -> LoadedDataset:
    """Verify then reconstruct an exported dataset; raise on any failure."""
    from verifiednet.datasets.verifier import verify_dataset

    root = Path(dataset_dir)
    result = verify_dataset(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise DatasetReadError(f"dataset failed verification: {detail}")

    manifest = DatasetManifest.model_validate_json(
        (root / "manifest.json").read_bytes()
    )
    by_partition: dict[DatasetPartition, tuple[AssignedDatasetExample, ...]] = {}
    all_examples: list[AssignedDatasetExample] = []
    for part, rel in SPLIT_FILE_BY_PARTITION.items():
        examples = parse_split_bytes((root / rel).read_bytes())
        ordered = tuple(sorted(examples, key=lambda a: a.example.example_id))
        by_partition[part] = ordered
        all_examples.extend(ordered)

    return LoadedDataset(
        manifest=manifest,
        examples=tuple(sorted(all_examples, key=lambda a: a.example.example_id)),
        by_partition=by_partition,
    )
