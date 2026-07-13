"""Persisted, separated ("prepared") dataset corpus (Gate 6.2 Part 4).

The prepared corpus is a NEW derived representation, written to its OWN directory
and NEVER touching the Part 3 export (`manifest.json`, the split JSONL files, or
the `dataset_digest`). It stores the three separated layers in distinct files so
the model-facing loader can return ONLY features:

    prepared/
      manifest.json                       # PreparedManifest + prepared_digest
      features/{train,validation,test,abstention}.jsonl
      labels/{train,validation,test,abstention}.jsonl
      metadata/{train,validation,test,abstention}.jsonl

Everything is deterministic (canonical JSON, path-sorted files, examples sorted
by ``example_id``, no timestamps/UUIDs/randomness). The writer is atomic and
verifies before dropping its ``.INCOMPLETE`` marker; the reader fails closed; the
verifier returns a structured result and re-runs the feature-leakage audit on
every reconstructed example.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.feature_leakage import audit_separated_example
from verifiednet.datasets.features import (
    AbstentionLabels,
    AcceptedLabels,
    DatasetFeatures,
    DatasetTraceMetadata,
    FeaturePolicy,
    LabelPolicy,
    SeparatedDatasetExample,
)
from verifiednet.datasets.models import (
    DatasetFileHash,
    DatasetPartition,
    DatasetPartitionCounts,
)
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel

PREPARED_MANIFEST_FILE = "manifest.json"
PREPARED_INCOMPLETE_MARKER = ".INCOMPLETE"
PREPARED_VERSION = 1
PREPARED_GENERATOR = "verifiednet.datasets.separation"

_LAYERS = ("features", "labels", "metadata")
_PARTITION_ORDER = (
    DatasetPartition.TRAIN,
    DatasetPartition.VALIDATION,
    DatasetPartition.TEST,
    DatasetPartition.ABSTENTION,
)
_PARTITION_FILE = {
    DatasetPartition.TRAIN: "train.jsonl",
    DatasetPartition.VALIDATION: "validation.jsonl",
    DatasetPartition.TEST: "test.jsonl",
    DatasetPartition.ABSTENTION: "abstention.jsonl",
}


def _layer_path(layer: str, part: DatasetPartition) -> str:
    return f"{layer}/{_PARTITION_FILE[part]}"


EXPECTED_PREPARED_FILES: frozenset[str] = frozenset(
    _layer_path(layer, part) for layer in _LAYERS for part in _PARTITION_ORDER
)
SUPPORTED_PREPARED_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_PREPARED_VERSION: frozenset[int] = frozenset({1})

_LabelsUnion = AcceptedLabels | AbstentionLabels
_LABELS_ADAPTER: TypeAdapter[_LabelsUnion] = TypeAdapter(_LabelsUnion)


class PreparedError(VerifiedNetError):
    """A prepared corpus could not be built, written, or read."""


def compute_prepared_digest(
    *,
    schema_version: int,
    prepared_version: int,
    dataset_version: str,
    generated_by: str,
    source_dataset_digest: str,
    source_index_digest: str,
    feature_policy_id: str,
    label_policy_id: str,
    partition_counts: DatasetPartitionCounts,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the prepared content + deterministic config."""
    payload = {
        "schema_version": schema_version,
        "prepared_version": prepared_version,
        "dataset_version": dataset_version,
        "generated_by": generated_by,
        "source_dataset_digest": source_dataset_digest,
        "source_index_digest": source_index_digest,
        "feature_policy_id": feature_policy_id,
        "label_policy_id": label_policy_id,
        "partition_counts": {
            "train": partition_counts.train,
            "validation": partition_counts.validation,
            "test": partition_counts.test,
            "abstention": partition_counts.abstention,
        },
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return sha256_canonical(payload)


class PreparedManifest(StrictModel):
    """The immutable manifest of a prepared (separated) corpus."""

    schema_version: Literal[1] = 1
    prepared_version: Literal[1] = 1
    dataset_version: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    source_dataset_digest: str
    source_index_digest: str
    feature_policy: FeaturePolicy
    label_policy: LabelPolicy
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    example_count: int = Field(ge=0)
    partition_counts: DatasetPartitionCounts
    files: tuple[DatasetFileHash, ...] = Field(default_factory=tuple)
    prepared_digest: str

    @model_validator(mode="after")
    def _consistent(self) -> PreparedManifest:
        if self.feature_policy_id != self.feature_policy.policy_id:
            raise ValueError("feature_policy_id does not match feature_policy")
        if self.label_policy_id != self.label_policy.policy_id:
            raise ValueError("label_policy_id does not match label_policy")
        if self.example_count != self.accepted_count + self.rejected_count:
            raise ValueError("example_count must equal accepted + rejected")
        if self.partition_counts.accepted_total != self.accepted_count:
            raise ValueError("train+validation+test must equal accepted_count")
        if self.partition_counts.abstention != self.rejected_count:
            raise ValueError("abstention count must equal rejected_count")
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files must be unique by path")
        expected = compute_prepared_digest(
            schema_version=self.schema_version, prepared_version=self.prepared_version,
            dataset_version=self.dataset_version, generated_by=self.generated_by,
            source_dataset_digest=self.source_dataset_digest,
            source_index_digest=self.source_index_digest,
            feature_policy_id=self.feature_policy_id, label_policy_id=self.label_policy_id,
            partition_counts=self.partition_counts, files=self.files,
        )
        if self.prepared_digest != expected:
            raise ValueError("prepared_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class PreparedExport:
    """The complete in-memory bytes of one prepared corpus (no filesystem)."""

    manifest: PreparedManifest
    layer_files: tuple[tuple[str, bytes], ...]  # (relative_path, bytes), path-sorted

    @property
    def manifest_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest)

    def output_files(self) -> tuple[tuple[str, bytes], ...]:
        files = list(self.layer_files)
        files.append((PREPARED_MANIFEST_FILE, self.manifest_bytes))
        return tuple(sorted(files, key=lambda kv: kv[0]))


def _projection_bytes(layer: str, s: SeparatedDatasetExample) -> bytes:
    if layer == "features":
        return canonical_json_bytes(s.features)
    if layer == "labels":
        return canonical_json_bytes(s.labels)
    return canonical_json_bytes(s.trace)


def build_prepared(
    separated: tuple[SeparatedDatasetExample, ...],
    *,
    feature_policy: FeaturePolicy,
    label_policy: LabelPolicy,
    dataset_version: str,
    source_index_digest: str,
    source_dataset_digest: str,
) -> PreparedExport:
    """Build the immutable prepared corpus from separated examples (pure)."""
    by_partition: dict[DatasetPartition, list[SeparatedDatasetExample]] = {
        part: [] for part in _PARTITION_ORDER
    }
    seen_examples: set[str] = set()
    seen_runs: set[str] = set()
    for s in separated:
        if s.features.feature_policy_id != feature_policy.policy_id:
            raise PreparedError("separated example carries a different feature policy")
        if s.labels.label_policy_id != label_policy.policy_id:
            raise PreparedError("separated example carries a different label policy")
        eid = s.trace.example_id
        rid = s.trace.run_id
        if eid in seen_examples:
            raise PreparedError(f"duplicate example_id: {eid}")
        if rid in seen_runs:
            raise PreparedError(f"duplicate run_id: {rid}")
        seen_examples.add(eid)
        seen_runs.add(rid)
        by_partition[s.trace.partition].append(s)

    layer_files: list[tuple[str, bytes]] = []
    file_hashes: list[DatasetFileHash] = []
    for layer in _LAYERS:
        for part in _PARTITION_ORDER:
            members = sorted(by_partition[part], key=lambda s: s.trace.example_id)
            payload = b"".join(_projection_bytes(layer, s) + b"\n" for s in members)
            rel = _layer_path(layer, part)
            layer_files.append((rel, payload))
            file_hashes.append(DatasetFileHash(
                relative_path=rel, sha256=sha256_bytes(payload), size=len(payload)
            ))

    counts = DatasetPartitionCounts(
        train=len(by_partition[DatasetPartition.TRAIN]),
        validation=len(by_partition[DatasetPartition.VALIDATION]),
        test=len(by_partition[DatasetPartition.TEST]),
        abstention=len(by_partition[DatasetPartition.ABSTENTION]),
    )
    files = tuple(sorted(file_hashes, key=lambda f: f.relative_path))
    prepared_digest = compute_prepared_digest(
        schema_version=1, prepared_version=PREPARED_VERSION,
        dataset_version=dataset_version, generated_by=PREPARED_GENERATOR,
        source_dataset_digest=source_dataset_digest, source_index_digest=source_index_digest,
        feature_policy_id=feature_policy.policy_id, label_policy_id=label_policy.policy_id,
        partition_counts=counts, files=files,
    )
    manifest = PreparedManifest(
        dataset_version=dataset_version, generated_by=PREPARED_GENERATOR,
        source_dataset_digest=source_dataset_digest, source_index_digest=source_index_digest,
        feature_policy=feature_policy, label_policy=label_policy,
        feature_policy_id=feature_policy.policy_id, label_policy_id=label_policy.policy_id,
        accepted_count=counts.accepted_total, rejected_count=counts.abstention,
        example_count=counts.total, partition_counts=counts, files=files,
        prepared_digest=prepared_digest,
    )
    return PreparedExport(
        manifest=manifest,
        layer_files=tuple(sorted(layer_files, key=lambda kv: kv[0])),
    )


@dataclass(frozen=True)
class WrittenPrepared:
    root: Path
    prepared_digest: str
    file_count: int


def write_prepared(prepared: PreparedExport, out_dir: str | Path) -> WrittenPrepared:
    """Write a prepared corpus deterministically; fail loudly on any error."""
    root = Path(out_dir)
    if root.exists() and any(root.iterdir()):
        raise PreparedError(f"target prepared directory exists and is non-empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for layer in _LAYERS:
        (root / layer).mkdir(exist_ok=True)
    marker = root / PREPARED_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    try:
        for rel, payload in prepared.output_files():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, payload)
        result = verify_prepared(root)
        hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
        if hard:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
            raise PreparedError(f"post-write verification failed: {detail}")
    except Exception:
        raise
    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenPrepared(
        root=root, prepared_digest=prepared.manifest.prepared_digest, file_count=file_count
    )


class PreparedVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    prepared_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def _parse_layer(data: bytes, layer: str) -> list[object]:
    if data == b"":
        return []
    if not data.endswith(b"\n"):
        raise PreparedError(f"{layer} file must end with a newline")
    out: list[object] = []
    for line in data[:-1].split(b"\n"):
        if not line:
            raise PreparedError(f"blank line in {layer} file")
        if layer == "features":
            out.append(DatasetFeatures.model_validate_json(line))
        elif layer == "labels":
            out.append(_LABELS_ADAPTER.validate_json(line))
        else:
            out.append(DatasetTraceMetadata.model_validate_json(line))
    return out


def verify_prepared(prepared_dir: str | Path) -> PreparedVerificationResult:
    """Verify a prepared corpus directory; return a structured result."""
    root = Path(prepared_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("prepared_dir_present", False, f"not a directory: {root}"))
        return PreparedVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("prepared_dir_present", True))

    marker_absent = not (root / PREPARED_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent))

    manifest_path = root / PREPARED_MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return PreparedVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = PreparedManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return PreparedVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.prepared_digest

    schema_ok = manifest.schema_version in SUPPORTED_PREPARED_SCHEMA
    version_ok = manifest.prepared_version in SUPPORTED_PREPARED_VERSION
    checks.append(_c("schema_supported", schema_ok))
    checks.append(_c("prepared_version_supported", version_ok))

    listed = {f.relative_path for f in manifest.files}
    checks.append(_c("manifest_lists_expected_files", listed == EXPECTED_PREPARED_FILES,
                    "" if listed == EXPECTED_PREPARED_FILES else f"listed={sorted(listed)}"))

    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != PREPARED_INCOMPLETE_MARKER
    }
    allowed = EXPECTED_PREPARED_FILES | {PREPARED_MANIFEST_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_c("no_missing_files", not missing, "" if not missing else f"missing={missing}"))
    checks.append(_c("no_unexpected_files", not unexpected,
                    "" if not unexpected else f"unexpected={unexpected}"))

    hash_ok = True
    hash_detail = ""
    for fh in manifest.files:
        fpath = root / fh.relative_path
        if not fpath.is_file():
            hash_ok, hash_detail = False, f"missing {fh.relative_path}"
            break
        raw = fpath.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok, hash_detail = False, f"hash/size mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, hash_detail))

    recomputed = compute_prepared_digest(
        schema_version=manifest.schema_version, prepared_version=manifest.prepared_version,
        dataset_version=manifest.dataset_version, generated_by=manifest.generated_by,
        source_dataset_digest=manifest.source_dataset_digest,
        source_index_digest=manifest.source_index_digest,
        feature_policy_id=manifest.feature_policy_id, label_policy_id=manifest.label_policy_id,
        partition_counts=manifest.partition_counts, files=manifest.files,
    )
    checks.append(_c("prepared_digest_matches", recomputed == manifest.prepared_digest))

    # Reconstruct each example across the three layers; re-run leakage audit.
    reconstruct_ok = True
    reconstruct_detail = ""
    leakage_ok = True
    counts: dict[DatasetPartition, int] = defaultdict(int)
    seen_examples: set[str] = set()
    seen_runs: set[str] = set()
    for part in _PARTITION_ORDER:
        try:
            feats = _parse_layer((root / _layer_path("features", part)).read_bytes(), "features")
            labs = _parse_layer((root / _layer_path("labels", part)).read_bytes(), "labels")
            metas = _parse_layer((root / _layer_path("metadata", part)).read_bytes(), "metadata")
        except (OSError, VerifiedNetError, ValidationError) as exc:
            reconstruct_ok = False
            reconstruct_detail = f"{part.value}: {str(exc).splitlines()[0]}"
            break
        if not (len(feats) == len(labs) == len(metas)):
            reconstruct_ok = False
            reconstruct_detail = f"{part.value}: layer line counts differ"
            break
        for f, lab, meta in zip(feats, labs, metas, strict=True):
            try:
                sep = SeparatedDatasetExample(features=f, labels=lab, trace=meta)  # type: ignore[arg-type]
            except ValidationError as exc:
                reconstruct_ok = False
                reconstruct_detail = f"{part.value}: {str(exc).splitlines()[0]}"
                break
            if meta.partition is not part:  # type: ignore[attr-defined]
                reconstruct_ok = False
                reconstruct_detail = f"{part.value}: example in wrong partition file"
                break
            if not audit_separated_example(sep).passed:
                leakage_ok = False
            eid = meta.example_id  # type: ignore[attr-defined]
            rid = meta.run_id  # type: ignore[attr-defined]
            if eid in seen_examples or rid in seen_runs:
                reconstruct_ok = False
                reconstruct_detail = f"duplicate identity {eid}/{rid}"
                break
            seen_examples.add(eid)
            seen_runs.add(rid)
            counts[part] += 1
        if not reconstruct_ok:
            break
    checks.append(_c("layers_reconstruct", reconstruct_ok, reconstruct_detail))
    checks.append(_c("no_feature_leakage", leakage_ok,
                    "" if leakage_ok else "feature-leakage audit failed"))

    if reconstruct_ok:
        pc = manifest.partition_counts
        counts_ok = (
            counts[DatasetPartition.TRAIN] == pc.train
            and counts[DatasetPartition.VALIDATION] == pc.validation
            and counts[DatasetPartition.TEST] == pc.test
            and counts[DatasetPartition.ABSTENTION] == pc.abstention
        )
        checks.append(_c("counts_match_manifest", counts_ok))

    return PreparedVerificationResult(
        verified=all(c.passed for c in checks), prepared_digest=digest, checks=tuple(checks)
    )


# ---------------------------------------------------------------------------
# Narrow public loaders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedPrepared:
    """A verified, reconstructed prepared corpus (evaluator-facing)."""

    manifest: PreparedManifest
    examples: tuple[SeparatedDatasetExample, ...]
    by_partition: dict[DatasetPartition, tuple[SeparatedDatasetExample, ...]]


def _require_verified(root: Path) -> PreparedManifest:
    result = verify_prepared(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise PreparedError(f"prepared corpus failed verification: {detail}")
    return PreparedManifest.model_validate_json((root / PREPARED_MANIFEST_FILE).read_bytes())


def load_features(
    prepared_dir: str | Path,
) -> dict[DatasetPartition, tuple[DatasetFeatures, ...]]:
    """MODEL-FACING loader: returns ONLY features, per partition. No labels, no
    trace metadata is ever returned by this API."""
    root = Path(prepared_dir)
    _require_verified(root)
    out: dict[DatasetPartition, tuple[DatasetFeatures, ...]] = {}
    for part in _PARTITION_ORDER:
        feats = _parse_layer((root / _layer_path("features", part)).read_bytes(), "features")
        out[part] = tuple(feats)  # type: ignore[arg-type]
    return out


def load_prepared(prepared_dir: str | Path) -> LoadedPrepared:
    """EVALUATOR-FACING loader: verifies then returns features + labels + trace."""
    root = Path(prepared_dir)
    manifest = _require_verified(root)
    by_partition: dict[DatasetPartition, tuple[SeparatedDatasetExample, ...]] = {}
    everything: list[SeparatedDatasetExample] = []
    for part in _PARTITION_ORDER:
        feats = _parse_layer((root / _layer_path("features", part)).read_bytes(), "features")
        labs = _parse_layer((root / _layer_path("labels", part)).read_bytes(), "labels")
        metas = _parse_layer((root / _layer_path("metadata", part)).read_bytes(), "metadata")
        examples = tuple(
            SeparatedDatasetExample(features=f, labels=lab, trace=meta)  # type: ignore[arg-type]
            for f, lab, meta in zip(feats, labs, metas, strict=True)
        )
        by_partition[part] = examples
        everything.extend(examples)
    return LoadedPrepared(
        manifest=manifest,
        examples=tuple(sorted(everything, key=lambda s: s.trace.example_id)),
        by_partition=by_partition,
    )
