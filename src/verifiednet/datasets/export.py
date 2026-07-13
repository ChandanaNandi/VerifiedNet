"""Deterministic dataset export builder (Gate 6.2 Part 3).

``build_dataset`` is a PURE function of the assigned examples + split policy +
build config: it partitions the examples, serialises each partition to canonical
JSONL, hashes every content file, derives the counts, and builds the
self-validating ``DatasetManifest`` (with ``dataset_digest``). It performs NO
filesystem IO — the writer owns that — so the exact output bytes can be compared
across two builds without touching disk.

Determinism guarantees: examples are sorted by ``example_id``; partitions are a
fixed, always-present set of four files (empty allowed); serialisation is
canonical JSON; the file list and digest are path-sorted. No timestamp, UUID,
random ordering, filesystem-order dependence, or platform dependence enters the
output.

Fail-closed: the builder runs the full leakage audit and refuses to build a
dataset that does not pass, so a leaky corpus can never be exported.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes
from verifiednet.datasets.leakage import audit_leakage
from verifiednet.datasets.models import (
    DATASET_EXPORT_VERSION,
    DATASET_GENERATOR,
    AssignedDatasetExample,
    DatasetFileHash,
    DatasetManifest,
    DatasetPartition,
    DatasetPartitionCounts,
    SplitPolicy,
    compute_dataset_digest,
)

#: On-disk layout of an exported dataset (stable filenames).
DATASET_MANIFEST_FILE = "manifest.json"
DATASET_SPLITS_DIR = "splits"
DATASET_INCOMPLETE_MARKER = ".INCOMPLETE"

#: Fixed, ordered partition→file map. All four files are ALWAYS written (an empty
#: partition yields an empty file) so the exported file set is stable.
SPLIT_FILE_BY_PARTITION: dict[DatasetPartition, str] = {
    DatasetPartition.TRAIN: f"{DATASET_SPLITS_DIR}/train.jsonl",
    DatasetPartition.VALIDATION: f"{DATASET_SPLITS_DIR}/validation.jsonl",
    DatasetPartition.TEST: f"{DATASET_SPLITS_DIR}/test.jsonl",
    DatasetPartition.ABSTENTION: f"{DATASET_SPLITS_DIR}/abstention.jsonl",
}

#: The complete set of relative paths every exported dataset contains.
EXPECTED_SPLIT_FILES: frozenset[str] = frozenset(SPLIT_FILE_BY_PARTITION.values())
DATASET_SCHEMA_VERSION = 1


class DatasetExportError(VerifiedNetError):
    """A dataset could not be built deterministically (duplicate/leak/etc.)."""


@dataclass(frozen=True)
class ExportedDataset:
    """The complete, in-memory bytes of one export (no filesystem involved)."""

    manifest: DatasetManifest
    #: (relative_path, canonical bytes) for each of the four split files, path-sorted.
    split_files: tuple[tuple[str, bytes], ...]

    @property
    def manifest_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest)

    def output_files(self) -> tuple[tuple[str, bytes], ...]:
        """Every file the writer emits (splits + manifest), path-sorted."""
        files = list(self.split_files)
        files.append((DATASET_MANIFEST_FILE, self.manifest_bytes))
        return tuple(sorted(files, key=lambda kv: kv[0]))


def _jsonl_bytes(examples: Sequence[AssignedDatasetExample]) -> bytes:
    """Canonical JSONL for one partition (one example per line, trailing \\n)."""
    return b"".join(canonical_json_bytes(ex) + b"\n" for ex in examples)


def parse_split_bytes(data: bytes) -> tuple[AssignedDatasetExample, ...]:
    """Parse one split file's canonical JSONL back into assigned examples.

    Strict: an empty file is zero examples; a non-empty file must end in exactly
    one newline and contain no blank lines. Raises ``DatasetExportError`` on any
    malformed framing (byte-level corruption is caught independently by the
    manifest file hashes).
    """
    if data == b"":
        return ()
    if not data.endswith(b"\n"):
        raise DatasetExportError("split file must end with a newline")
    lines = data[:-1].split(b"\n")
    out: list[AssignedDatasetExample] = []
    for line in lines:
        if not line:
            raise DatasetExportError("blank line in split file")
        out.append(AssignedDatasetExample.model_validate_json(line))
    return tuple(out)


def build_dataset(
    assigned_examples: Iterable[AssignedDatasetExample],
    *,
    policy: SplitPolicy,
    dataset_version: str,
    source_index_digest: str,
) -> ExportedDataset:
    """Build the immutable exported dataset from audited, assigned examples.

    Fails closed (``DatasetExportError``) on a duplicate example/source-run, a
    policy mismatch, or any leakage-audit ERROR — a leaky or ambiguous corpus is
    never exported.
    """
    assigned = tuple(assigned_examples)

    expected_policy_id = policy.policy_id

    # Group by partition; detect duplicates up front (defence in depth over audit).
    by_partition: dict[DatasetPartition, list[AssignedDatasetExample]] = {
        part: [] for part in SPLIT_FILE_BY_PARTITION
    }
    seen_examples: set[str] = set()
    seen_runs: set[str] = set()
    for a in assigned:
        ex = a.example
        if ex.example_id in seen_examples:
            raise DatasetExportError(f"duplicate example_id in export: {ex.example_id}")
        if ex.run_id in seen_runs:
            raise DatasetExportError(f"duplicate source run_id in export: {ex.run_id}")
        seen_examples.add(ex.example_id)
        seen_runs.add(ex.run_id)
        if a.partition not in by_partition:
            raise DatasetExportError(f"unexpected partition: {a.partition}")
        # Accepted assignments must carry this policy's id; abstention is exempt.
        is_abstention = a.partition is DatasetPartition.ABSTENTION
        if not is_abstention and a.split_policy_id != expected_policy_id:
            raise DatasetExportError(
                f"example {ex.example_id} assigned under a different split policy"
            )
        by_partition[a.partition].append(a)

    # Leakage audit — refuse to export a corpus that does not pass (fail closed).
    audit = audit_leakage(assigned)
    if not audit.passed:
        codes = ", ".join(sorted({f.code.value for f in audit.errors}))
        raise DatasetExportError(f"leakage audit failed; cannot export: {codes}")

    # Serialise each partition deterministically (sorted by example_id).
    split_files: list[tuple[str, bytes]] = []
    file_hashes: list[DatasetFileHash] = []
    for part, rel in sorted(SPLIT_FILE_BY_PARTITION.items(), key=lambda kv: kv[1]):
        members = sorted(by_partition[part], key=lambda a: a.example.example_id)
        payload = _jsonl_bytes(members)
        split_files.append((rel, payload))
        file_hashes.append(
            DatasetFileHash(
                relative_path=rel, sha256=sha256_bytes(payload), size=len(payload)
            )
        )

    counts = DatasetPartitionCounts(
        train=len(by_partition[DatasetPartition.TRAIN]),
        validation=len(by_partition[DatasetPartition.VALIDATION]),
        test=len(by_partition[DatasetPartition.TEST]),
        abstention=len(by_partition[DatasetPartition.ABSTENTION]),
    )
    accepted_count = counts.accepted_total
    rejected_count = counts.abstention
    files = tuple(sorted(file_hashes, key=lambda f: f.relative_path))

    dataset_digest = compute_dataset_digest(
        schema_version=DATASET_SCHEMA_VERSION,
        export_version=DATASET_EXPORT_VERSION,
        dataset_version=dataset_version,
        generated_by=DATASET_GENERATOR,
        source_index_digest=source_index_digest,
        split_policy_id=expected_policy_id,
        partition_counts=counts,
        files=files,
    )
    manifest = DatasetManifest(
        dataset_version=dataset_version,
        generated_by=DATASET_GENERATOR,
        source_index_digest=source_index_digest,
        split_policy=policy,
        split_policy_id=expected_policy_id,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        example_count=accepted_count + rejected_count,
        partition_counts=counts,
        files=files,
        dataset_digest=dataset_digest,
    )
    return ExportedDataset(
        manifest=manifest,
        split_files=tuple(sorted(split_files, key=lambda kv: kv[0])),
    )
