# Gate 6.2 (Part 3) — Exported Dataset, Digest, Verifier, and Reproducibility

**Status:** IMPLEMENTED (Gate 6.2 Part 3 of 4). This document describes the code
in `verifiednet.datasets` that produces the immutable, reproducible exported
dataset: the corpus manifest, the on-disk layout, the deterministic
`dataset_digest`, the writer, the reader, and the verifier. It implements the
ADR-0018 decisions that datasets are DERIVED, deterministic, content-addressed,
and read-only over the authoritative run library — no new ADR is introduced.

Part 3 completes Gate 6.2. Later gates (evaluation, baselines) consume the
exported dataset; nothing here trains a model or writes truth.

## 1. What an export is

An exported dataset is an immutable directory built from an already-audited,
already-split corpus of `AssignedDatasetExample`s. It is a projection of verified
runs — it embeds references, split assignments, and counts, never a copy of
evidence, a model output, or an inferred label. The run library remains the only
source of truth; an export is rebuildable from it at any time.

## 2. On-disk layout (stable filenames)

```
<dataset_dir>/
  manifest.json                 # DatasetManifest (corpus manifest + dataset_digest)
  splits/
    train.jsonl                 # canonical JSONL of AssignedDatasetExample
    validation.jsonl
    test.jsonl
    abstention.jsonl            # eval-only rejected examples
```

All four split files are ALWAYS present (an empty partition yields an empty
file), so the file set is fixed and a missing or unexpected file is detectable.
Each split file is canonical JSONL — one `AssignedDatasetExample` per line,
sorted by `example_id`, with exactly one trailing newline. There is no
timestamped, machine-specific, or randomly-ordered content anywhere.

## 3. The corpus manifest (`DatasetManifest`)

The manifest fully describes one export and carries only deterministic build
metadata — never a timestamp, username, hostname, or machine identity:

- `schema_version`, `export_version` — exact (`Literal[1]`); `export_version`
  bumps only when the on-disk bytes/layout change.
- `dataset_version`, `generated_by` — a human label and the fixed tool id
  (`verifiednet.datasets.export`).
- `source_index_digest` — pins the exact verified run library the export derives
  from.
- `split_policy` + `split_policy_id` — the exact policy (salt + integer buckets)
  and its derived id; the manifest validates that the id matches the policy.
- `accepted_count`, `rejected_count`, `example_count`, `partition_counts` — the
  manifest validates `example_count = accepted + rejected`,
  `train+validation+test = accepted`, and `abstention = rejected`.
- `files` — a path-sorted, unique list of `{relative_path, sha256, size}` for the
  four split files (per-file integrity).
- `dataset_digest` — the self-validating content digest (below).

## 4. The deterministic `dataset_digest`

`compute_dataset_digest` is `sha256_canonical` of an explicit payload:
`{schema_version, export_version, dataset_version, generated_by,
source_index_digest, split_policy_id, partition_counts, files}` where `files` is
path-sorted. It is:

- derived **only** from exported content (the per-file hashes) plus deterministic
  build config;
- **non-recursive** — it never includes itself;
- independent of filesystem ordering (the file list is sorted), of the clock, of
  the export machine, and of absolute paths (relative paths only).

The digest is **self-validating**: `DatasetManifest` recomputes it in a model
validator and refuses to construct (or parse) a manifest whose `dataset_digest`
does not match its content. A tampered digest or count therefore fails at parse
time — the reader and verifier inherit this for free.

## 5. Writer, reader, verifier

- **Writer** (`write_dataset`) installs an `ExportedDataset` into a fresh
  directory with atomic writes (temp → fsync → replace → dir fsync, reusing the
  run-artifact durability helpers) under a `.INCOMPLETE` marker that is removed
  ONLY after an independent `verify_dataset` pass. It writes solely into the
  target directory and never touches the verified run library. Running it twice
  on identical input produces byte-identical output.
- **Reader** (`read_dataset`) verifies first and FAILS CLOSED — it raises
  `DatasetReadError` if verification fails — then reconstructs the manifest and
  the assigned examples grouped by partition.
- **Verifier** (`verify_dataset`) returns a structured `DatasetVerificationResult`
  (never a bare bool, never an exception for a verification failure) with named
  checks: manifest present/parses, schema+export supported, the manifest lists
  exactly the expected files, no missing/unexpected files, per-file hash+size
  match, the digest re-derives, each split file holds only its own partition, the
  reconstructed counts match the manifest, and no `example_id`/`run_id` is
  duplicated across the corpus.

## 6. Fail-closed export

`build_dataset` runs the full leakage audit and refuses to build a corpus that
does not pass, and independently rejects a duplicate `example_id`/`run_id` or an
example assigned under a different split policy. A leaky or ambiguous dataset can
never be written.

## 7. Reproducibility guarantee

Given the same verified run library (pinned by `source_index_digest`), the same
`SplitPolicy`, and the same `dataset_version`, two independent exports produce
byte-identical split files, an identical manifest, and an identical
`dataset_digest`. This is proven by test at two levels: in-memory
(`ExportedDataset.output_files()` equality across two builds) and on-disk (two
separate written directories compared file-by-file). No timestamps, UUIDs, random
ordering, filesystem-order dependence, or platform dependence enter the output.

## 8. Immutability

The export writes only into its own output directory. A byte-fingerprint of the
verified run library is identical before and after a full
discover → project → assign → build → write → verify → read pipeline, and the
reserved `IncidentRecord.dataset_*` fields stay `None`.

## 9. Limitations and Part 4 entry conditions

The v1 corpus is tiny (a handful of catalog cases across four families plus one
rejected precondition case), so the export proves the machinery — deterministic
serialization, content-addressed digest, structured verification, and
build-twice reproducibility — not a statistically adequate benchmark. The same
machinery scales unchanged as the verified library grows. Part 4 will layer the
features/labels separation (a features-only view + a withheld-label file for the
future hidden benchmark) on top of this exported dataset; the exported layout and
digest defined here are its stable foundation.
