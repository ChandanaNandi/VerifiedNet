# Gate 6 — Roadmap, Dependency Graph, and Risks

**Status:** PLANNING ONLY. Sequences Gate 6 implementation, places it in the
full gate dependency graph, enumerates risks with mitigations, and recommends
the Gate 6.1 scope.

## 1. Dependency graph (Step 12)

The graph is acyclic; each stage consumes only earlier outputs, and **truth
never flows backward** (datasets never feed ground truth; models never feed
datasets or truth).

```
Gate 5 verified runs + run index   (authoritative source of truth)
        │  (read-only, verified)
        ▼
Gate 6  Verified Dataset Engine     (deterministic projection + leakage-safe splits)
        │
        ├────────────────────────────┐
        ▼                            ▼
Gate 7  Evaluation framework   Gate 8  Deterministic rule baselines
        │                            │
        └──────────────┬─────────────┘
                       ▼
Gate 9  Networking SLM (train on train split only)
                       ▼
Gate 10 Vector RAG  ──►  Gate 11 GraphRAG
                       ▼
Gate 12 Model-quality evaluation (uses hidden/challenge splits)
                       ▼
Gate 13 Orchestrator / agent harness
                       ▼
Gate 14 Safe remediation + rollback
                       ▼
Gate 15 Persistent workflows + operational memory + outcome engine
```

**Why this order is correct:** the dataset (6) must precede everything
model-related, because a model can only be trained/evaluated against a
leakage-safe, verified corpus. The evaluation framework (7) and deterministic
baselines (8) precede the SLM (9) so there is a measuring stick and a
non-model floor before any model is trusted. Retrieval (10/11) follows the SLM
because it augments a model that already exists to benchmark. Model-quality
evaluation (12) needs the hidden/challenge splits the dataset engine defines.
Orchestrator/remediation/workflows (13–15) consume verified incidents by
reference and never re-derive truth. No arrow points from a later gate back
into truth or the dataset — the graph stays acyclic and truth stays model-free.

## 2. Future-gate compatibility (Step 11)

The dataset engine is designed so Gates 7–15 need it without redesign:

- **Gate 7 (eval):** consumes the frozen `test` split + the rejection/abstention
  partition; relies on stable `example_id`s and the features/labels separation.
- **Gate 8 (baselines):** runs deterministic rules over the same examples;
  needs the model-free verdicts already present.
- **Gate 9 (SLM):** trains on `train` only; relies on the leakage invariant and
  the features-only view.
- **Gate 10/11 (RAG/GraphRAG):** index evidence + provenance BY REFERENCE
  (run_id + digest) — the dataset provides stable, verifiable pointers into the
  run artifacts rather than copies.
- **Gate 12 (model eval):** uses the hidden benchmark and challenge set.
- **Gates 13–15:** consume incidents by reference; never re-label or re-derive.

Enablers designed in now: content-addressed `example_id`s; separable
features/labels; by-reference heavy artifacts; explicit challenge partitions.
None of these requires an example-schema change to add later.

## 3. Risks and mitigations (Step 13)

| Class | Risk | Mitigation |
|---|---|---|
| Architectural | Temptation to write split assignment back into `incident.json` (reserved fields), breaking `run_digest`/index | Hard rule (ADR-0018): split assignment lives ONLY in the dataset manifest; the authoritative run is never mutated; a test asserts run digests are unchanged after a build |
| Architectural | Using `incident_id`/`run_digest` as a cross-run key (they embed timestamps) → silent leakage | Group by the STABLE scenario+topology key only (`leakage-analysis.md` §0); a test proves two runs of one case share a `group_id` but differ in `incident_id`/`run_digest` |
| Dataset | Tiny corpus (9 cases, 4 families, 1 topology) → not statistically meaningful | Frame v1 as a methodology proof, not a benchmark; scale families/topologies later; document explicitly |
| Dataset | Rejected incidents mis-used as fault-class negatives (fabricated labels) | Strict policy: rejected → eval-only abstention partition, never a negative label |
| Research | Environment confound (single commit/image/arch) → model learns environment | Record environment provenance per example; diversify environments later; control for it in Gate 7 |
| Research | Baseline-hash / constant-feature memorization | Feature-hygiene guidance to Gate 7; dataset flags non-discriminative constants |
| Reproducibility | Nondeterminism via randomness / iteration order | Pure hash-bucket splitting with recorded `split_salt`; explicit sort keys; build-twice digest-equality test |
| Benchmark | A moving benchmark makes cross-gate comparison invalid | Freeze benchmark/challenge partitions by digest; pin `source_index_digest` |
| Scaling | Dataset artifact sprawl in git | Datasets are derived + rebuildable; commit only build config + digests, not bulk data |
| Maintenance | Schema drift across model versions | Record every upstream `schema_version`; refuse incompatible builds loudly; migrate by re-derivation, never in place |

## 4. Proposed Gate 6 substeps

- **Gate 6.0 — this plan.** Docs + ADR-0018 only. DONE: committed, CI green.
- **Gate 6.1 — dataset schema + read-only discovery + integrity gate. DONE.**
  The frozen example/manifest models; discovery over the run index;
  before-inclusion verification; offline tests over the existing verified runs
  (run library rebuilt offline via the catalog sim).
- **Gate 6.2 (Part 2) — grouping + deterministic splitting + abstention +
  leakage audit. DONE.** Stable-identity `group_id`, rejected runs projected as
  eval-only abstention examples, the pure integer-bucket split function
  (`SPLIT_BUCKET_COUNT = 10_000`), the separate `AssignedDatasetExample`
  binding, group-cohesion enforcement, sibling tags as INFO findings, and the
  fail-closed `audit_leakage`. The abstention partition (originally sketched for
  6.4) landed here because rejected-as-abstention is inseparable from splitting.
  See `rejected-examples-and-leakage-safe-splits.md`.
- **Gate 6.2 (Part 3) — exported dataset + digest + writer/reader/verifier +
  reproducibility. DONE.** The immutable export layout (`manifest.json` +
  `splits/{train,validation,test,abstention}.jsonl`); the self-validating
  non-recursive `dataset_digest`; the full `DatasetManifest` corpus manifest;
  the deterministic `write_dataset`; the fail-closed `read_dataset`; the
  structured `verify_dataset` (missing/unexpected/duplicate/corruption/digest
  detection); and the build-twice reproducibility proof (byte-identical files +
  digests + manifests). See `exported-dataset-and-reproducibility.md`.
- **Gate 6.2 (Part 4) — features/labels separation.** The features-only view; a
  withheld-label file for the future hidden benchmark, layered on the Part 3
  exported dataset.
- **Gate 6.5 — closure.** Completion report + acceptance matrix; propose (NOT
  create) `v0.6-gate6-complete`.

Each substep: offline-only where possible; a live pass only if a step genuinely
needs fresh verified runs; small commits; stop-and-report on any determinism,
leakage, or immutability surprise.

## 5. Recommendation for Gate 6.1 (Step 17)

Implement exactly the **read-only discovery + integrity gate + frozen example
schema** — no splitting, no dataset digest yet. Concretely: define the
`ScenarioCase`-independent dataset example + manifest models (schema_version 1);
a discovery function that reads the run index, re-verifies each run
(`verify_run_index` + `verify_run_dir` + `run_digest` match + `.INCOMPLETE`
refusal + schema-version compatibility), and projects each ACCEPTED run into a
frozen example (model-free, by-reference evidence); and offline tests proving:
projection is a pure function of the verified run, the authoritative run is
never mutated (run digests unchanged after a build), a tampered/incomplete/
unindexed run is refused, and the reserved `incident.json` fields stay `None`.
Explicitly out of scope for 6.1: any splitting, any `dataset_digest`, any
rejected-incident handling, any model, and any live lab run beyond producing the
verified inputs. This isolates the highest-risk invariant (read-only, model-free
projection with integrity) before any split logic is added.

Gate 6.1 must not begin until this plan is approved.
