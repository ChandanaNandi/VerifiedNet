# Gate 6.0 — Verified Dataset Engine: Engineering Plan

**Status:** PARTIALLY IMPLEMENTED. Gate 6.1 (models, discovery, read-only
projection) and Gate 6.2 Part 2 (rejected-as-abstention projection, deterministic
integer-bucket splitting, and the fail-closed leakage audit) now exist in
`verifiednet.datasets`; see `rejected-examples-and-leakage-safe-splits.md`. The
corpus writer/reader/verifier, `DatasetManifest`, `dataset_digest`, and export
remain PLANNING ONLY (Gate 6 Part 3). This document remains the governing
engineering specification.

The single governing principle: **the dataset engine is a deterministic,
read-only PROJECTION of already-verified runs. It never creates, relabels,
re-derives, or infers truth, and it never invokes a model.** It extends
ADR-0009/0010 (ground truth is model-free) into the dataset layer.

## 1. What already exists (the authoritative inputs)

Gate 6 consumes ONLY the Gate 4/5 verified run artifacts and the run index —
nothing is regenerated. Per run directory `runs/<run_id>/` (ADR-0016):

- `incident.json` — `IncidentRecord` (frozen; `status` accepted|rejected;
  carries `scenario`, `topology`, `topology_hash`, `fault`, `ground_truth`,
  sealed evidence bundles, phase verdicts, `restoration`, `provenance`,
  `oracle_version`, `created_at`). It already reserves **`dataset_group_id`**
  and **`dataset_split`** (both currently `None`).
- `run_manifest.json` — `RunManifest` (`git_rev`, `lock_hash`, `scenario_id`,
  `template_id`, `topology_hash`, `image_digests`, `transcript_sha256`,
  `seeds`, timestamps, `acceptance_status`).
- `environment_manifest.json` — `EnvironmentManifest` (os/kernel/arch/python,
  container runtime + version, `image_reference`, `image_manifest_digest`,
  `platform_resolved_digest`, `frr_version`, `captured_at`).
- `transcript.jsonl`, `ledger.jsonl`, `evidence/{baseline,onset,recovery}.json`.
- `hashes.json` (hash index + **`run_digest`**), `verification_report.json`,
  `layout.json`.

The **run index** (`index.json`) holds one `RunIndexEntry` per run
(`run_id`, `incident_id`, `scenario_id`, `template_id`, `acceptance_status`,
`run_dir`, `run_digest`, `topology_hash`, `layout_schema_version`, timestamps)
plus an `index_digest`. `verify_run_index` re-verifies every referenced run.

### Immutable / authoritative / never-regenerated

- **Authoritative source of truth:** the run directories + the run index. The
  dataset is DERIVED and rebuildable from them; the dataset is never
  authoritative over a run.
- **Immutable:** every persisted run artifact. The dataset engine must never
  rewrite `incident.json` — including the reserved `dataset_*` fields — because
  any byte change alters `run_digest` and breaks the index. Split assignment
  therefore lives ONLY in the dataset manifest (see §Splitting), never inside
  the authoritative run.
- **Never regenerated:** ground truth, evidence, transcript, ledger,
  verdicts, fault metadata. The engine reads them; it does not recompute them.

### Key determinism finding (leakage-critical)

`incident_id = sha256("inc", {scenario_id, fault.model_dump(), topology_hash})`
and `fault.model_dump()` includes `injected_at` (a wall-clock timestamp) and
run-local sequence numbers. **Therefore two live runs of the SAME catalog case
produce DIFFERENT `incident_id`s and DIFFERENT `run_digest`s.** Neither
`incident_id` nor `run_digest` is a stable cross-run identity. The dataset
engine must derive its leakage-grouping key from the STABLE scenario identity
(template_id + scenario_id + canonical parameters + topology_hash), never from
`incident_id`, `run_digest`, or any timestamped field. This is the single most
important input to the leakage design (`leakage-analysis.md`).

## 2. Mission (Step 3)

**The dataset engine SHOULD:**

- discover verified runs by reading the run index;
- re-verify integrity before including any run (`verify_run_index`, per-run
  `verify_run_dir`, `run_digest` equality, `.INCOMPLETE` refusal, schema-version
  compatibility);
- reject corrupt, tampered, incomplete, unindexed, or duplicate runs — loudly,
  never silently dropped;
- construct deterministic dataset examples as a pure projection of verified
  artifacts;
- assign deterministic, leakage-safe splits;
- emit a versioned, integrity-verifiable dataset manifest with full provenance.

**The dataset engine MUST NOT:**

- relabel incidents, modify evidence, or regenerate any truth;
- infer or fabricate missing labels;
- invoke any AI model, heuristic, or inference;
- re-run the lab or mutate any run artifact (including the reserved
  `incident.json` fields);
- introduce randomness or nondeterminism;
- place two runs that share a leakage group into different splits.

## 3. Dataset object (Step 4 — see `dataset-schema.md`)

A dataset EXAMPLE is a frozen, content-addressed projection of ONE verified
run. Full field list, required/optional/forbidden, and versioning are specified
in `dataset-schema.md`. In summary: it carries the stable identity
(`example_id`, `group_id`, `split`, `run_id`, `run_digest`, `template_id`,
`scenario`, `topology_hash`, `acceptance_status`), the model-free label
(`ground_truth` for accepted runs), evidence/transcript/ledger BY REFERENCE
(run_id + digests) with optional embedded normalized facts, environment
provenance, and build provenance — and forbids any model output, any free-text
label, and any inferred field.

## 4. Integrity (Step 7)

Two-phase, digest-anchored:

- **Before inclusion:** the run index verifies as a whole; each candidate run
  verifies independently and its `run_digest` matches the indexed value; the
  run is not `.INCOMPLETE`; its `layout_schema_version` and every embedded
  `schema_version` are compatible with the dataset build's declared support.
- **After construction:** the engine computes a non-recursive `dataset_digest`
  over the run-id-sorted example identities (the same discipline as
  `run_digest`/`index_digest`); a dataset verifier re-checks that every
  referenced run still verifies and still carries the recorded `run_digest`,
  that no `run_digest`/`example_id` repeats (duplicate detection), that no
  leakage group spans splits, and that the coverage report accounts for every
  indexed run (included, or dropped with a machine reason — never silently).

## 5. Accepted vs rejected (Step 10)

- **Accepted incidents** carry `ground_truth` (model-free label) and become the
  positive, fully-labeled examples split across train/dev/test.
- **Rejected incidents** (precondition-rejected) carry NO ground truth, zero
  mutation, baseline evidence only. A rejected run is not a fault of an unknown
  class and must never be fabricated into a negative label — doing so would
  invent a label and violate the model-free invariant. Recommended policy:
  rejected runs form a separate **abstention / evaluation-only partition**,
  used to test that a downstream system correctly reports "no verified fault",
  and are NEVER placed in the accepted training-label space and NEVER converted
  into synthetic negatives. (Reasoned, not guessed: the fault-class label space
  is defined only by accepted ground truth; a rejected run has no place in it.)

## 6. Versioning, provenance, splitting, leakage, risks, dependencies

These are specified in the companion documents:
`dataset-schema.md` (object + versioning + provenance),
`leakage-analysis.md` (every leakage source + prevention),
`splitting-strategy.md` (deterministic splits + benchmark/challenge partitions),
`gate6-roadmap.md` (substeps, dependency graph, risks, Gate 6.1 recommendation).

The core, finalized invariant of this plan is recorded as ADR-0018.

## 7. Explicitly out of scope for Gate 6

No model, SLM, RAG, GraphRAG, agent, evaluation metric, or benchmark scoring
(those are Gates 7–15). No new fault families, topologies, or lab backends. No
re-derivation of truth. The dataset engine is a deterministic projection layer
on top of the Gate 5 verified library — nothing more.
