# Gate 6.0 — Dataset Schema (Object, Provenance, Versioning)

**Status:** the dataset EXAMPLE shape is now IMPLEMENTED in
`verifiednet.datasets.models` (Gate 6.1/6.2 Part 2), extended additively with
`example_kind`, `stable_identity`, and rejected-run source facts, plus the
`SplitPolicy`, `AssignedDatasetExample`, and leakage-audit models; see
`rejected-examples-and-leakage-safe-splits.md`. The `DatasetManifest` and the
non-recursive `dataset_digest` remain PLANNING ONLY (Part 3). Field names below
are the design proposal; the committed contract is the code and its contract
tests (`tests/contract/test_datasets_shapes.py`).

## 1. The dataset example (one verified run, projected)

A dataset example is a frozen, content-addressed projection of exactly one
verified run. It embeds only deterministically-derived, model-free content.

### Required fields

- `dataset_schema_version` — the example format version (starts at 1).
- `example_id` — content-derived: `"ex-" + sha256_canonical(projection)[:16]`,
  where `projection` is the example's own truth-bearing content EXCLUDING
  volatile provenance (timestamps, absolute paths). Stable for a given run.
- `group_id` — the leakage-grouping key (`leakage-analysis.md` §0).
- `split` — the assigned split name (`train` | `dev` | `test` | reserved
  benchmark/challenge names). Carried in the example AND the manifest; it is a
  build-time assignment, never written back to the authoritative run.
- `run_id`, `run_digest` — the source run identity (provenance + integrity).
- `template_id`, `scenario` (id + stable parameters + target), `topology_hash`,
  `backend` — the stable identity components.
- `acceptance_status` — `accepted` | `rejected`.
- `ground_truth` — for accepted runs ONLY: `root_cause_label`, the verifier
  `verdicts`, `accepted_evidence_ids`, `oracle_version`. Verbatim from the run.
- `fault_summary` — for accepted runs: `parameter_name`, `before_value`,
  `after_value`, `target_node` (verbatim from `FaultInjection`, minus the
  volatile `injected_at`/sequence fields).
- `restoration_summary` — `method`, `forced_reset_used`, `completed`.
- `evidence_ref` — the evidence bundle ids per phase + the recorded
  `run_digest`, so a consumer can fetch the verified bundles from the run
  directory. Normalized facts MAY be embedded (see optional).
- `provenance` — `code_commit`, `generator` + version, `oracle_version`,
  `image_manifest_digest`, `arch`, `frr_version`, `dataset_version`,
  `dataset_schema_version`, `split_salt`, source `run_dir` (relative).

### Optional fields

- Embedded `normalized` evidence facts (the deterministic metric maps) — a
  copy for convenience; the authoritative source remains the run's evidence
  bundles. Omitted from `example_id` content only if identical to source
  (it always is — it is copied verbatim).
- `transcript_ref` / `ledger_ref` — by-reference pointers (run_id + digest);
  the full transcript/ledger stay in the run directory.
- Human-readable `description` (from the catalog case), documentation only.

### Forbidden fields

- Any model output, embedding, score, or inferred label.
- Any free-text label or operator prose in the label position.
- Any field the engine COMPUTED by inference rather than copied/derived
  deterministically.
- Any absolute host path, credential, or run-local timestamp inside the
  hashed `example_id` content.
- The reserved `incident.json` `dataset_group_id`/`dataset_split` written back
  to the authoritative run (they stay `None` there).

## 2. A "features-only" view (for future inference)

For Gate 9+ inference without leaking the answer, the engine can emit a
DERIVED features-only projection of each example that OMITS `ground_truth`,
`fault_summary`, and `restoration_summary`, keyed by `example_id`. Labels live
in a separate labels file keyed by the same `example_id`. This separation is a
design requirement now so it needs no schema change later; it is not built in
Gate 6.1 unless a consumer needs it.

## 3. The dataset manifest

One manifest per built dataset:

- `dataset_schema_version`, `dataset_version` (the concrete build's content
  digest or an assigned semver), build `created_at` (provenance only, excluded
  from the hashed content).
- `split_salt` — the fixed salt driving deterministic split assignment.
- `split_ratios` — the configured train/dev/test proportions.
- `source_index_digest` — a snapshot of the run index's `index_digest` at build
  time (pins exactly which verified corpus produced this dataset).
- `upstream_versions` — the `schema_version` of each embedded model
  (`IncidentRecord`, `GroundTruth`, evidence, manifests), `layout_schema_version`,
  run-index schema version, and `oracle_version`.
- `examples` — the ordered list of `example_id` + `run_id` + `group_id` +
  `split`.
- `dataset_digest` — non-recursive `sha256_canonical` over the run-id-sorted
  example identities (`example_id`, `run_id`, `run_digest`, `group_id`,
  `split`), excluding itself. The reproducibility anchor.
- `coverage` — every indexed run accounted for: included, or dropped with a
  machine reason (schema-incompatible, failed verification, rejected-and-eval-
  only, …). No silent truncation.

## 4. Provenance strategy (Step 8)

**Required provenance** (per example and per manifest): the source `run_id` +
`run_digest`, `code_commit`, `oracle_version`, `generator` + version,
`image_manifest_digest`, `topology_hash`, `dataset_schema_version`,
`dataset_version`, `split_salt`, and the `source_index_digest`.

**Optional provenance:** environment detail (kernel, python, runtime version),
build timing.

**Forbidden provenance:** any model output; any credential; any absolute
host-specific path; anything that would make the hashed content
non-reproducible.

**Provenance survives future dataset versions** by (a) copying every required
provenance field verbatim into each new dataset version's examples, and (b)
recording the PREVIOUS `dataset_digest` in the new manifest — forming a
verifiable chain back to the exact verified runs. Because the runs are the
authoritative source and never change, every dataset version's provenance
resolves to the same immutable artifacts.

## 5. Versioning strategy (Step 9)

Versions tracked: `dataset_schema_version` (example format), `dataset_version`
(the concrete build), and the recorded upstream versions (each model's
`schema_version`, `layout_schema_version`, run-index schema version,
`oracle_version`).

**Compatibility & migration rules:**

- A built dataset is IMMUTABLE. A schema-version bump never edits an existing
  dataset in place.
- "Migration" = a fresh, deterministic RE-DERIVATION from the same verified
  runs under the new schema (a pure function; the runs are unchanged). The old
  and new datasets coexist, each with its own digest and provenance chain.
- The engine refuses to build over runs whose embedded `schema_version`s are
  outside its declared support set (loud incompatibility, never silent
  coercion).
- Because datasets are DERIVED and rebuildable from the verified run library,
  bulky dataset artifacts need not be committed to the repository; only the
  build CONFIG (ratios, salt, supported versions) and the resulting
  `dataset_digest` + `source_index_digest` need be recorded for reproducibility.
  Anyone can rebuild the byte-identical dataset from the verified runs + config.

## 6. Immutability summary

The authoritative runs are immutable; the dataset is a derived, versioned,
digest-anchored projection; an example is frozen and content-addressed; a
change of content is a new dataset version, never an in-place edit. This mirrors
the Step 5/6 artifact discipline one layer up.
