# 0021 — Benchmarking compares predictors under identical conditions; it never changes evaluation

**Status:** Accepted (owner decision, Gate 9)
**Date:** 2026-07-13

## Context

Gate 9 adds the ability to evaluate multiple predictors side by side. The risk is
that a comparison layer quietly becomes unfair or non-reproducible — by exposing
labels to a predictor, by varying the prompt/scoring/normalization between
predictors, by ranking non-deterministically, or by letting registration or
execution order change a result. ADR-0019 (deterministic, model-free evaluation)
and ADR-0020 (models behind the feature-only boundary) fix the evaluation of ONE
predictor; this ADR fixes how MANY predictors are compared so those guarantees
survive at the benchmark layer.

## Decision

1. **The benchmark compares predictors; it does not change evaluation.** Every
   predictor is evaluated through the unchanged Gate 7 engine
   (`evaluate_prepared_corpus`) under IDENTICAL conditions: the same task, the same
   feature/prompt contract, the same scoring policy, the same normalization
   policy, and the same feature policy. The benchmark never alters any of these
   per predictor. Evaluation remains the single source of truth; the benchmark
   consumes evaluation runs and never mutates them.

2. **Predictors still receive only features.** Benchmarking introduces no new path
   to labels, trace metadata, split, or identity; each predictor sees only
   `DatasetFeatures`, exactly as in Gate 7/8 (proven by test).

3. **Everything is deterministic and order-independent.** Predictors are evaluated
   in sorted-identifier order; the benchmark identifier and digest are computed
   over canonicalised (sorted) predictor identifiers, so registration and
   execution order never affect the result. Ranking is a pure, fully tie-broken
   function (accepted diagnosis accuracy, then abstention accuracy, then
   invalid-prediction count, then predictor identifier). No timestamps, machine
   identifiers, or runtime durations enter any immutable benchmark artifact.

4. **A predictor's benchmark identifier is its evaluation identity.** Rule
   baselines and model-backed predictors are compared uniformly by their Gate-7
   `baseline_id` — the same identity the evaluation engine and evaluation
   manifests already use — so the benchmark references real, verifiable evaluation
   runs.

5. **Benchmarks are immutable and independently re-derivable.** A benchmark is
   written to its own `benchmarks/<benchmark_id>/` directory (never overwriting an
   evaluation), carries a non-recursive self-validating `benchmark_digest`, and
   the verifier RE-COMPUTES the ranking from the stored comparison and confirms
   coverage of every predictor — it never trusts a stored ranking.

## Consequences

- Multiple predictors can be compared fairly and reproducibly without weakening
  the feature-only boundary, evaluation integrity, or determinism.
- Later gates (optimization, multi-model work) extend the benchmark set but may
  not change what a benchmark holds constant across predictors, nor bypass the
  evaluator.
- The tiny v1 corpus makes benchmark accuracies a methodology demonstration, not a
  statistical ranking — an explicitly documented limitation.

## References

- `../gate9/benchmark-framework.md`
- ADR-0019 (deterministic, model-free evaluation), ADR-0020 (models behind the
  feature-only predictor boundary)
