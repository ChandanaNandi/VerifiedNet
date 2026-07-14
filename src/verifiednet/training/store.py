"""Immutable training-corpus storage: manifest, writer, verifier, loaders (Gate 10A).

A training corpus is persisted separately from verified runs, dataset exports,
prepared corpora, evaluations, and benchmarks:

    training-corpora/<training_corpus_id>/
        manifest.json     # TrainingCorpusManifest (+ self-validating digest)
        inputs.jsonl      # SupervisedTrainingInput, one per line
        targets.jsonl     # SupervisedTrainingTarget, one per line
        metadata.jsonl    # TrainingTraceMetadata, one per line (audit only)

Line *i* of each file is the same example (ordered by source example id), so the
input/target/metadata boundary is preserved on disk. The TRAINER-FACING loader
(``load_training_pairs``) returns ONLY (input, target) pairs — never identity,
digests, trace metadata, or evaluation/benchmark information. The audit-facing
reader returns all layers. The verifier re-derives every id, re-runs the training
leakage audit on every stored example, and fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import DatasetFileHash
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.corpus import (
    SupervisedTrainingExample,
    SupervisedTrainingInput,
    SupervisedTrainingTarget,
    TrainingCorpus,
    TrainingTraceMetadata,
    audit_training_example,
    derive_training_corpus_id,
    derive_training_example_id,
)
from verifiednet.training.policy import (
    TrainingDataPolicy,
    TrainingInputTemplate,
    TrainingTargetTemplate,
)

TRAINING_CORPUS_FORMAT_VERSION = 1
TRAINING_GENERATOR = "verifiednet.training.corpus"

MANIFEST_FILE = "manifest.json"
INPUTS_FILE = "inputs.jsonl"
TARGETS_FILE = "targets.jsonl"
METADATA_FILE = "metadata.jsonl"
TRAINING_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_TRAINING_FILES: frozenset[str] = frozenset(
    {INPUTS_FILE, TARGETS_FILE, METADATA_FILE}
)
SUPPORTED_TRAINING_SCHEMA: frozenset[int] = frozenset({1})
SUPPORTED_TRAINING_FORMAT: frozenset[int] = frozenset({1})


class TrainingStoreError(VerifiedNetError):
    """Writing/reading/verifying a training-corpus directory failed."""


def compute_training_corpus_digest(
    *,
    schema_version: int,
    corpus_format_version: int,
    training_corpus_id: str,
    task_id: str,
    training_data_policy_id: str,
    input_template_id: str,
    target_template_id: str,
    feature_policy_id: str,
    label_policy_id: str,
    source_prepared_digest: str,
    source_dataset_digest: str,
    example_count: int,
    training_example_ids: tuple[str, ...],
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    """Non-recursive digest over the corpus config, provenance pins, and files."""
    payload = {
        "schema_version": schema_version,
        "corpus_format_version": corpus_format_version,
        "training_corpus_id": training_corpus_id,
        "task_id": task_id,
        "training_data_policy_id": training_data_policy_id,
        "input_template_id": input_template_id,
        "target_template_id": target_template_id,
        "feature_policy_id": feature_policy_id,
        "label_policy_id": label_policy_id,
        "source_prepared_digest": source_prepared_digest,
        "source_dataset_digest": source_dataset_digest,
        "example_count": example_count,
        "training_example_ids": list(training_example_ids),
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "traindig-" + sha256_canonical(payload)[:24]


class TrainingCorpusManifest(StrictModel):
    """The immutable manifest of a persisted training corpus (self-validating)."""

    schema_version: Literal[1] = 1
    corpus_format_version: Literal[1] = 1
    training_corpus_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    training_data_policy: TrainingDataPolicy
    input_template: TrainingInputTemplate
    target_template: TrainingTargetTemplate
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    source_prepared_digest: str = Field(min_length=1)
    source_dataset_digest: str = Field(min_length=1)
    source_partition: Literal["train"] = "train"
    example_count: int = Field(ge=0)
    training_example_ids: tuple[str, ...] = Field(default_factory=tuple)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(default_factory=tuple)
    training_corpus_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> TrainingCorpusManifest:
        if self.training_data_policy.task_id != self.task_id:
            raise ValueError("task_id does not match embedded policy")
        if (self.training_data_policy.input_template_id
                != self.input_template.input_template_id):
            raise ValueError("policy input_template_id does not match embedded template")
        if (self.training_data_policy.target_template_id
                != self.target_template.target_template_id):
            raise ValueError("policy target_template_id does not match embedded template")
        if self.example_count != len(self.training_example_ids):
            raise ValueError("example_count does not match training_example_ids")
        if len(set(self.training_example_ids)) != len(self.training_example_ids):
            raise ValueError("training_example_ids must be unique")
        expected_corpus_id = derive_training_corpus_id(
            task_id=self.task_id,
            training_data_policy_id=self.training_data_policy.training_data_policy_id,
            input_template_id=self.input_template.input_template_id,
            target_template_id=self.target_template.target_template_id,
            training_example_ids=self.training_example_ids,
        )
        if self.training_corpus_id != expected_corpus_id:
            raise ValueError("training_corpus_id does not match the manifest content")
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths):
            raise ValueError("manifest files must be path-sorted")
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files must be unique by path")
        expected = compute_training_corpus_digest(
            schema_version=self.schema_version,
            corpus_format_version=self.corpus_format_version,
            training_corpus_id=self.training_corpus_id, task_id=self.task_id,
            training_data_policy_id=self.training_data_policy.training_data_policy_id,
            input_template_id=self.input_template.input_template_id,
            target_template_id=self.target_template.target_template_id,
            feature_policy_id=self.feature_policy_id,
            label_policy_id=self.label_policy_id,
            source_prepared_digest=self.source_prepared_digest,
            source_dataset_digest=self.source_dataset_digest,
            example_count=self.example_count,
            training_example_ids=self.training_example_ids,
            generated_by=self.generated_by, files=self.files,
        )
        if self.training_corpus_digest != expected:
            raise ValueError("training_corpus_digest does not match manifest content")
        return self


@dataclass(frozen=True)
class TrainingCorpusExport:
    manifest: TrainingCorpusManifest
    content_files: tuple[tuple[str, bytes], ...]

    @property
    def manifest_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest)

    def output_files(self) -> tuple[tuple[str, bytes], ...]:
        files = list(self.content_files)
        files.append((MANIFEST_FILE, self.manifest_bytes))
        return tuple(sorted(files, key=lambda kv: kv[0]))


def build_training_export(corpus: TrainingCorpus) -> TrainingCorpusExport:
    """Build the immutable on-disk bytes for a training corpus (pure)."""
    inputs_payload = b"".join(
        canonical_json_bytes(e.input) + b"\n" for e in corpus.examples)
    targets_payload = b"".join(
        canonical_json_bytes(e.target) + b"\n" for e in corpus.examples)
    metadata_payload = b"".join(
        canonical_json_bytes(e.trace) + b"\n" for e in corpus.examples)
    content = {
        INPUTS_FILE: inputs_payload,
        TARGETS_FILE: targets_payload,
        METADATA_FILE: metadata_payload,
    }
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path,
    ))
    example_ids = tuple(e.training_example_id for e in corpus.examples)
    digest = compute_training_corpus_digest(
        schema_version=1, corpus_format_version=TRAINING_CORPUS_FORMAT_VERSION,
        training_corpus_id=corpus.training_corpus_id, task_id=corpus.task_id,
        training_data_policy_id=corpus.policy.training_data_policy_id,
        input_template_id=corpus.input_template.input_template_id,
        target_template_id=corpus.target_template.target_template_id,
        feature_policy_id=corpus.feature_policy_id,
        label_policy_id=corpus.label_policy_id,
        source_prepared_digest=corpus.source_prepared_digest,
        source_dataset_digest=corpus.source_dataset_digest,
        example_count=len(corpus.examples), training_example_ids=example_ids,
        generated_by=TRAINING_GENERATOR, files=files,
    )
    manifest = TrainingCorpusManifest(
        training_corpus_id=corpus.training_corpus_id, task_id=corpus.task_id,
        training_data_policy=corpus.policy, input_template=corpus.input_template,
        target_template=corpus.target_template,
        feature_policy_id=corpus.feature_policy_id,
        label_policy_id=corpus.label_policy_id,
        source_prepared_digest=corpus.source_prepared_digest,
        source_dataset_digest=corpus.source_dataset_digest,
        example_count=len(corpus.examples), training_example_ids=example_ids,
        generated_by=TRAINING_GENERATOR, files=files,
        training_corpus_digest=digest,
    )
    return TrainingCorpusExport(
        manifest=manifest,
        content_files=tuple(sorted(content.items(), key=lambda kv: kv[0])),
    )


@dataclass(frozen=True)
class WrittenTrainingCorpus:
    root: Path
    training_corpus_id: str
    training_corpus_digest: str
    file_count: int


def write_training_corpus(
    corpus: TrainingCorpus, corpora_root: str | Path
) -> WrittenTrainingCorpus:
    """Write ``training-corpora/<corpus_id>/`` deterministically; never overwrite."""
    export = build_training_export(corpus)
    root = Path(corpora_root) / corpus.training_corpus_id
    if root.exists() and any(root.iterdir()):
        raise TrainingStoreError(f"training corpus already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / TRAINING_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    try:
        for rel, payload in export.output_files():
            atomic_write_bytes(root / rel, payload)
        result = verify_training_corpus(root)
        hard = [c for c in result.failures if c.rule != "incomplete_marker_absent"]
        if hard:
            detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
            raise TrainingStoreError(f"post-write verification failed: {detail}")
    except Exception:
        raise
    marker.unlink()
    fsync_dir(root)
    file_count = sum(1 for p in root.rglob("*") if p.is_file())
    return WrittenTrainingCorpus(
        root=root, training_corpus_id=corpus.training_corpus_id,
        training_corpus_digest=export.manifest.training_corpus_digest,
        file_count=file_count,
    )


class TrainingVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    training_corpus_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


def _parse_lines(data: bytes, model: type, name: str) -> list[object]:
    if data == b"":
        return []
    if not data.endswith(b"\n"):
        raise TrainingStoreError(f"{name} must end with a newline")
    out: list[object] = []
    for line in data[:-1].split(b"\n"):
        if not line:
            raise TrainingStoreError(f"blank line in {name}")
        out.append(model.model_validate_json(line))  # type: ignore[attr-defined]
    return out


def verify_training_corpus(corpus_dir: str | Path) -> TrainingVerificationResult:
    """Verify a training-corpus directory; re-derive ids; re-audit; fail closed."""
    root = Path(corpus_dir)
    checks: list[DatasetCheck] = []

    if not root.is_dir():
        checks.append(_c("corpus_dir_present", False, f"not a directory: {root}"))
        return TrainingVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("corpus_dir_present", True))

    marker_absent = not (root / TRAINING_INCOMPLETE_MARKER).exists()
    checks.append(_c("incomplete_marker_absent", marker_absent))

    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return TrainingVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = TrainingCorpusManifest.model_validate_json(manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return TrainingVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.training_corpus_digest

    checks.append(_c("schema_supported",
                    manifest.schema_version in SUPPORTED_TRAINING_SCHEMA))
    checks.append(_c("format_supported",
                    manifest.corpus_format_version in SUPPORTED_TRAINING_FORMAT))

    listed = {f.relative_path for f in manifest.files}
    checks.append(_c("manifest_lists_expected_files",
                    listed == EXPECTED_TRAINING_FILES,
                    "" if listed == EXPECTED_TRAINING_FILES else f"listed={sorted(listed)}"))

    on_disk = {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name != TRAINING_INCOMPLETE_MARKER
    }
    allowed = EXPECTED_TRAINING_FILES | {MANIFEST_FILE}
    missing = sorted(allowed - on_disk)
    unexpected = sorted(on_disk - allowed)
    checks.append(_c("no_missing_files", not missing,
                    "" if not missing else f"missing={missing}"))
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

    # Reconstruct every example across the three layers; the example model
    # re-derives training_example_id (binding input+target+source), and the
    # training leakage audit re-runs on every stored payload. Full re-derivation
    # of rendered text from features requires the SOURCE prepared corpus (pinned
    # by source_prepared_digest); within this artifact, the content-hash binding
    # is the independently auditable proof.
    reconstruct_ok, leakage_ok, ids_ok = True, True, True
    detail = ""
    example_ids: list[str] = []
    if hash_ok:
        try:
            inputs = _parse_lines((root / INPUTS_FILE).read_bytes(),
                                  SupervisedTrainingInput, INPUTS_FILE)
            targets = _parse_lines((root / TARGETS_FILE).read_bytes(),
                                   SupervisedTrainingTarget, TARGETS_FILE)
            metas = _parse_lines((root / METADATA_FILE).read_bytes(),
                                 TrainingTraceMetadata, METADATA_FILE)
        except (VerifiedNetError, ValidationError) as exc:
            reconstruct_ok = False
            detail = str(exc).splitlines()[0]
        else:
            if not (len(inputs) == len(targets) == len(metas)):
                reconstruct_ok = False
                detail = "layer line counts differ"
            else:
                seen_sources: set[str] = set()
                for inp, tgt, meta in zip(inputs, targets, metas, strict=True):
                    eid = derive_training_example_id(
                        source_example_id=meta.source_example_id,  # type: ignore[attr-defined]
                        task_id=meta.task_id,  # type: ignore[attr-defined]
                        training_data_policy_id=meta.training_data_policy_id,  # type: ignore[attr-defined]
                        input_template_id=meta.input_template_id,  # type: ignore[attr-defined]
                        target_template_id=meta.target_template_id,  # type: ignore[attr-defined]
                        rendered_input=inp.text,  # type: ignore[attr-defined]
                        rendered_target=tgt.text,  # type: ignore[attr-defined]
                    )
                    try:
                        example = SupervisedTrainingExample(
                            training_example_id=eid, input=inp, target=tgt,  # type: ignore[arg-type]
                            trace=meta,  # type: ignore[arg-type]
                        )
                    except ValidationError as exc:
                        reconstruct_ok = False
                        detail = str(exc).splitlines()[0]
                        break
                    if meta.source_example_id in seen_sources:  # type: ignore[attr-defined]
                        reconstruct_ok = False
                        detail = f"duplicate source example {meta.source_example_id}"  # type: ignore[attr-defined]
                        break
                    seen_sources.add(meta.source_example_id)  # type: ignore[attr-defined]
                    if not audit_training_example(example).passed:
                        leakage_ok = False
                    if (meta.task_id != manifest.task_id  # type: ignore[attr-defined]
                            or meta.training_data_policy_id  # type: ignore[attr-defined]
                            != manifest.training_data_policy.training_data_policy_id
                            or meta.feature_policy_id != manifest.feature_policy_id  # type: ignore[attr-defined]
                            or meta.label_policy_id != manifest.label_policy_id):  # type: ignore[attr-defined]
                        ids_ok = False
                    example_ids.append(eid)
    else:
        reconstruct_ok = False
    checks.append(_c("examples_reconstruct", reconstruct_ok, detail))
    checks.append(_c("no_training_leakage", leakage_ok))
    checks.append(_c("policy_ids_consistent", ids_ok))

    if reconstruct_ok:
        checks.append(_c("example_ids_match_manifest",
                        tuple(example_ids) == manifest.training_example_ids))
        checks.append(_c("count_matches_manifest",
                        len(example_ids) == manifest.example_count))

    recomputed = compute_training_corpus_digest(
        schema_version=manifest.schema_version,
        corpus_format_version=manifest.corpus_format_version,
        training_corpus_id=manifest.training_corpus_id, task_id=manifest.task_id,
        training_data_policy_id=manifest.training_data_policy.training_data_policy_id,
        input_template_id=manifest.input_template.input_template_id,
        target_template_id=manifest.target_template.target_template_id,
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        source_prepared_digest=manifest.source_prepared_digest,
        source_dataset_digest=manifest.source_dataset_digest,
        example_count=manifest.example_count,
        training_example_ids=manifest.training_example_ids,
        generated_by=manifest.generated_by, files=manifest.files,
    )
    checks.append(_c("corpus_digest_matches", recomputed == manifest.training_corpus_digest))

    return TrainingVerificationResult(
        verified=all(c.passed for c in checks), training_corpus_digest=digest,
        checks=tuple(checks),
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


class TrainingPair(StrictModel):
    """The ONLY thing a trainer receives: one rendered input + one target."""

    schema_version: Literal[1] = 1
    input_text: str = Field(min_length=1)
    target_text: str = Field(min_length=1)


@dataclass(frozen=True)
class LoadedTrainingCorpus:
    """Audit-facing reconstruction: all three layers + manifest."""

    manifest: TrainingCorpusManifest
    examples: tuple[SupervisedTrainingExample, ...]


def _require_verified(root: Path) -> TrainingCorpusManifest:
    result = verify_training_corpus(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise TrainingStoreError(f"training corpus failed verification: {detail}")
    return TrainingCorpusManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())


def load_training_pairs(corpus_dir: str | Path) -> tuple[TrainingPair, ...]:
    """TRAINER-FACING loader: verified (fail-closed), returns ONLY input/target
    pairs — never source identity, trace metadata, digests, or evaluation data."""
    root = Path(corpus_dir)
    _require_verified(root)
    inputs = _parse_lines((root / INPUTS_FILE).read_bytes(),
                          SupervisedTrainingInput, INPUTS_FILE)
    targets = _parse_lines((root / TARGETS_FILE).read_bytes(),
                           SupervisedTrainingTarget, TARGETS_FILE)
    return tuple(
        TrainingPair(input_text=i.text, target_text=t.text)  # type: ignore[attr-defined]
        for i, t in zip(inputs, targets, strict=True)
    )


def load_training_corpus(corpus_dir: str | Path) -> LoadedTrainingCorpus:
    """AUDIT-FACING loader: verified (fail-closed), returns all three layers."""
    root = Path(corpus_dir)
    manifest = _require_verified(root)
    inputs = _parse_lines((root / INPUTS_FILE).read_bytes(),
                          SupervisedTrainingInput, INPUTS_FILE)
    targets = _parse_lines((root / TARGETS_FILE).read_bytes(),
                           SupervisedTrainingTarget, TARGETS_FILE)
    metas = _parse_lines((root / METADATA_FILE).read_bytes(),
                         TrainingTraceMetadata, METADATA_FILE)
    examples = tuple(
        SupervisedTrainingExample(
            training_example_id=derive_training_example_id(
                source_example_id=m.source_example_id,  # type: ignore[attr-defined]
                task_id=m.task_id,  # type: ignore[attr-defined]
                training_data_policy_id=m.training_data_policy_id,  # type: ignore[attr-defined]
                input_template_id=m.input_template_id,  # type: ignore[attr-defined]
                target_template_id=m.target_template_id,  # type: ignore[attr-defined]
                rendered_input=i.text, rendered_target=t.text,  # type: ignore[attr-defined]
            ),
            input=i, target=t, trace=m,  # type: ignore[arg-type]
        )
        for i, t, m in zip(inputs, targets, metas, strict=True)
    )
    return LoadedTrainingCorpus(manifest=manifest, examples=examples)
