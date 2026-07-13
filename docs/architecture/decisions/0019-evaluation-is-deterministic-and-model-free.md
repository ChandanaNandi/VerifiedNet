# 0019 — Evaluation is deterministic and model-free; baselines receive only features

**Status:** Accepted (owner decision, Gate 7)
**Date:** 2026-07-13

## Context

Gate 7 introduces the first evaluation framework over the prepared Gate 6 corpus.
Evaluation is where a predictor's output is compared against authoritative truth,
so it is the exact place a leak or a hidden model dependency would silently
corrupt every downstream comparison (baselines, SLM, RAG, GraphRAG). ADR-0018
established that datasets are derived, model-free, and leakage-safe; it does not
by itself constrain the evaluation layer. This ADR fixes the load-bearing
evaluation invariants before any model or LLM is allowed into the system, so that
later gates plug predictors into a boundary that is already proven correct.

## Decision

1. **The first evaluation framework is fully deterministic and offline.** No
   model training, no hosted or local LLM, no embeddings, no vector database, no
   prompt execution, no fine-tuning, no external inference, and no process
   execution. Every id, metric, and digest is a pure function of content
   (canonical serialization, explicit sorting, integer counts; no
   time/host/user/env/random/`hash()`).

2. **Baselines receive ONLY model-visible features.** A baseline's contract is
   `predict(features: DatasetFeatures) -> DatasetPrediction`. It never receives
   labels, trace metadata, a `SeparatedDatasetExample`, split membership,
   identity, or a source artifact. The evaluator explicitly extracts features
   before calling the baseline; there is no convenience path that hands a full
   example to a baseline. This boundary is enforced by types and proven by test.

3. **Only the evaluator combines predictions with labels and trace metadata.**
   Ground truth stays evaluator-only; a prediction is never allowed to
   reconstruct truth, and truth never flows back into features or the prepared
   corpus. The one-way flow extends: verified runs → projection → splitting →
   export → separation → baseline prediction → evaluation → immutable results.

4. **Predictions, scores, tasks, baselines, and evaluations are content-addressed
   and validated.** `task_id`, `baseline_id`, `prediction_id`, and
   `evaluation_id` are deterministic hashes of their defining content; the models
   validate their own derived ids. Changing any prediction-affecting rule,
   scoring policy, or normalization changes the corresponding id.

5. **Abstention is an explicit outcome, never a fabricated label.** A rejected
   (precondition) example's target is abstention; it is never scored as
   `healthy`/`no fault`, never a negative training label, and never enters the
   accepted fault-family confusion matrix. Accepted and abstention metrics are
   reported separately.

6. **Evaluation results are immutable and independently re-derivable.** An
   evaluation is written to its own `evaluations/<evaluation_id>/` directory
   (never overwriting an earlier stage), carries a non-recursive self-validating
   `evaluation_digest`, and the verifier RE-COMPUTES metrics/confusion/ids from
   the per-example records rather than trusting the stored derived files. The
   feature-leakage audit (ADR-0018 layer) is re-run before prediction; a leaky
   corpus fails closed.

7. **Rule baselines are transparent lower bounds, not intelligence.** They exist
   to validate the framework and establish a reproducible floor; their
   limitations are documented, not hidden.

## Consequences

- The evaluation layer is a pure, offline, model-free consumer of the read-only
  dataset engine; the dependency graph stays acyclic and truth stays model-free.
- When Gate 8+ introduces a model/LLM predictor, it plugs into the SAME
  feature-only baseline boundary and the SAME immutable, self-verifying result
  format — the boundary and the scoring are already proven, so a model cannot
  quietly gain access to labels or reshape the metrics.
- The tiny v1 corpus makes the reported accuracies a methodology demonstration,
  not a statistical benchmark — an explicitly documented limitation.

## References

- `../gate7/evaluation-framework.md`
- ADR-0009 (ground truth from deterministic evidence only), ADR-0010 (models are
  not ground truth), ADR-0018 (datasets are derived, leakage-safe, model-free)
