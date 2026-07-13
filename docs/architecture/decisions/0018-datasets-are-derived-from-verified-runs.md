# 0018 — Datasets are derived from verified runs; the run library is authoritative

**Status:** Accepted (owner decision, Gate 6.0 planning)
**Date:** 2026-07-13

## Context

Gate 6 introduces the Verified Dataset Engine. Without a hard boundary, a
dataset layer can quietly become a second source of truth — relabeling
incidents, caching stale copies, or letting a model influence what counts as a
correct answer. It can also introduce data leakage that every later gate
(evaluation, baselines, SLM, RAG, GraphRAG) silently inherits. This ADR fixes
the load-bearing invariants before any dataset code exists, extending ADR-0009
and ADR-0010 (ground truth is model-free) into the dataset layer.

## Decision

1. **The verified run library and its run index are the ONLY source of truth.**
   A dataset is a DERIVED, rebuildable, deterministic projection of already
   verified runs. A dataset is never authoritative over a run; runs are never
   regenerated to serve a dataset.

2. **The dataset engine is read-only over the authoritative artifacts.** It
   never modifies `incident.json`, evidence, transcripts, ledgers, manifests, or
   the run index — including the reserved `IncidentRecord.dataset_group_id` /
   `dataset_split` fields, which stay `None` in the authoritative run because any
   byte change would alter `run_digest` and break the index. Split and group
   assignment live ONLY in the dataset manifest.

3. **No truth is created, relabeled, inferred, or model-derived.** The engine
   copies ground truth, verdicts, and evidence verbatim; it never invokes a
   model, heuristic, or inference, and it never fabricates a missing label.

4. **Integrity is verified before and after inclusion.** A run is included only
   after `verify_run_index` + `verify_run_dir` + `run_digest` equality +
   `.INCOMPLETE` refusal + schema-version compatibility; the built dataset
   carries a non-recursive `dataset_digest` and pins the `source_index_digest`.

5. **Leakage grouping uses STABLE scenario identity, never timestamped ids.**
   Because `incident_id` and `run_digest` embed `injected_at`/sequence values,
   two runs of the same scenario differ in both; the leakage `group_id` is a
   pure hash of `{template_id, scenario_id, target_node, target_session, stable
   parameters, topology_hash, backend}`. No two runs sharing a `group_id` may
   cross a split (machine-checked invariant).

6. **Splitting is deterministic and randomness-free.** Assignment is a pure
   hash-bucket function of `group_id` + a recorded `split_salt` + configured
   ratios; the same inputs reproduce a byte-identical `dataset_digest`.

7. **Datasets are versioned and immutable; migration is re-derivation.** A built
   dataset is never edited in place; a schema change produces a fresh dataset
   re-derived from the same verified runs, each version pinned by its own digest
   and provenance chain. Because datasets are rebuildable, only the build config
   and digests need be committed, not bulk data.

8. **Rejected incidents are eval-only.** A precondition-rejected run has no
   ground truth and is never turned into a fault-class negative or training
   label; it belongs to a separate abstention/evaluation partition.

## Consequences

- The dependency graph stays acyclic and truth stays model-free at the dataset
  layer: runs → dataset → evaluation/models, never the reverse.
- Datasets are fully reproducible from the verified runs + build config; a
  tampered or missing run is caught before inclusion.
- Downstream gates (7–15) consume stable, content-addressed, leakage-safe
  examples with by-reference access to verified artifacts, without redesign.
- The tiny v1 corpus is a methodology proof, not a statistical benchmark — an
  explicitly documented limitation, not a hidden one.

## References

- `../gate6/dataset-engine-plan.md`, `../gate6/leakage-analysis.md`,
  `../gate6/dataset-schema.md`, `../gate6/splitting-strategy.md`,
  `../gate6/gate6-roadmap.md`
- ADR-0009 (ground truth from deterministic evidence only), ADR-0010 (models are
  not ground truth), ADR-0016 (canonical run artifact directory), ADR-0017
  (composition root + run index)
