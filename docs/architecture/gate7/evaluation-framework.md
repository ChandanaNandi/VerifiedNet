# Gate 7 — Deterministic Rule Baselines and Evaluation Framework

**Status:** IMPLEMENTED (Gate 7). This document describes the code in
`verifiednet.evaluation` that evaluates the Gate 6 prepared corpus with
deterministic, offline, model-free baselines and writes an immutable,
content-addressed evaluation result. It implements ADR-0019 (evaluation is
deterministic and model-free; baselines receive only features). No model, LLM,
embedding, training, or process execution is involved.

## 1. Truth flow and the model-visible boundary

```
Verified Runs → Projection → Splitting → Immutable Export →
Feature/Label/Metadata Separation → Deterministic Baseline Prediction →
Evaluation → Immutable Evaluation Results
```

No stage modifies an earlier one. The evaluator loads the prepared corpus, passes
ONLY `DatasetFeatures` to the baseline, and combines predictions with labels and
trace metadata in evaluator-only code. A baseline never sees labels, trace,
identity, split, or a source artifact (proven by a spy-baseline test and by the
`predict(features: DatasetFeatures)` signature).

## 2. Evaluation-task contract

`EvaluationTask` is frozen, versioned, and content-addressed. Its `task_id` is
`"task-" + sha256_canonical(task_contract_without_task_id)[:16]` over the task
name, versions, accepted target type (`fault_family`), abstention target,
permitted partitions, scoring-policy version, and the normalization-policy id —
no time/host/user/env/path. The model validates its own id. The first task
(`diagnosis_task()`) is single-fault-family diagnosis with abstention: accepted
examples predict the fault family; rejected examples predict abstention.

`NormalizationPolicy` is a versioned, deterministic normalizer (strip + casefold
only — no fuzzy matching, edit distance, embeddings, or synonyms). Changing it
changes its `policy_id` and therefore the `task_id`.

## 3. Predictions

`DatasetPrediction` is a discriminated union of `DiagnosisPrediction`
(`fault_family`, matched rules) and `AbstentionPrediction` (`abstain=True`,
`reason_code`, matched rules). Abstention is an EXPLICIT type — never an empty
string, `None`, `"healthy"`, `"no fault"`, a missing value, or an exception.
Each prediction's `prediction_id` is derived from the binding context —
`(baseline_id, task_id, feature_policy_id, canonical feature payload, canonical
prediction payload)` — never from example/group/run id, split, time, order, or
path. Identical features under the same task and baseline always produce the same
prediction id.

## 4. Baselines

A `Baseline` is `predict(features) -> prediction` plus a frozen `BaselineSpec`
whose `baseline_id` hashes name + versions + `task_id` + rule configuration, so
any prediction-affecting rule change changes the id.

- **FixedPriorBaseline** — always predicts one explicit configured fault family; it
  never inspects labels and never abstains. The transparent reference floor.
- **EvidenceRuleBaseline** — an ordered, explicit, versioned rule set over the
  feature ALLOWLIST only. Because the allowlisted features intentionally do not
  reveal the fault family, the rules can only: `R1` abstain when there is no onset
  evidence, else `R2` predict the configured default family; an explicit
  (unreachable) fallback abstains with `no_rule_matched`. It is a transparent
  lower bound, not semantic intelligence.

No fitted baseline is introduced — the first framework is fixed-configuration
only, so there is no training/fitting stage and test labels can never influence a
baseline.

## 5. Scoring, metrics, confusion

Scoring is exact normalized equality (no fuzzy/semantic matching). Structured
outcome categories: `correct_diagnosis`, `incorrect_diagnosis`,
`abstained_on_diagnosis` (accepted side); `correct_abstention`,
`false_diagnosis_on_rejected` (abstention side). Accepted and abstention metrics
are separate. The accepted confusion matrix (`authoritative_class`, `predicted`
or `<abstain>`, `count`, path-sorted) never includes abstention examples.

**Zero-denominator policy:** every ratio is a deterministic 6-place decimal string
derived from integer counts, or `None` when no eligible example exists — never `0`
and never `NaN` (which canonical JSON forbids). An overall accuracy is reported
but never replaces the separate accepted/abstention metrics.

## 6. Evaluation run, id, and digest

`EvaluationRun` carries the task, baseline spec, prepared digest, source dataset
digest, policy ids, ordered per-example records, aggregate metrics, confusion, and
partition summaries. Its `evaluation_id` is `"eval-" +
sha256_canonical({task_id, baseline_id, prepared_digest, scoring_policy_version,
ordered prediction_ids, metrics})[:16]` — non-recursive (it never includes
itself). Records are sorted by `example_id`, so the result is independent of input
iteration order (proven by test).

## 7. Immutable output, verifier, integrity audit

An evaluation is written to `evaluations/<evaluation_id>/` — separate from the
verified runs, the Part 3 export, and the Part 4 prepared corpus — as
`manifest.json` + `records.jsonl` + `metrics.json` + `confusion.json`. The writer
is atomic under a `.INCOMPLETE` marker, verifies before finalizing, and refuses to
overwrite an existing evaluation. The `EvaluationManifest` embeds the task and
baseline spec and carries a non-recursive self-validating `evaluation_digest`.

`verify_evaluation` is structured (never a bare bool): manifest present/parses,
schema+format supported, exact expected file set, no missing/unexpected files,
per-file hash, digest re-derivation, run reconstruction, and the integrity audit.
`audit_evaluation_run` independently RE-COMPUTES per-record correctness/category,
aggregate metrics, confusion, and the evaluation id from the records alone and
fails closed on any ERROR — it never trusts a stored derived value.
`read_evaluation` verifies first and fails closed.

## 8. Guarantees proven by test

Feature-only boundary (spy baseline sees only features); leakage resistance
(evaluation refuses a corpus whose features fail the audit); source immutability
(run library + export + prepared corpus byte-identical before/after); no execution
(subprocess + process runner sabotaged); build-twice reproducibility
(byte-identical directories, identical ids/digests); input-order independence
(shuffled input → identical output); deliberate tampering detected.

## 9. Limitations

The rule baselines are transparent lower bounds; they do not diagnose from
evidence content (the allowlisted features intentionally withhold the answer). The
v1 corpus is tiny, so the reported accuracies demonstrate the framework, not a
statistical benchmark. When Gate 8+ introduces a model/LLM predictor it plugs into
the SAME feature-only boundary and the SAME immutable, self-verifying result
format — no redesign of scoring or the boundary is required.

**Update (Gate 12):** proven again for real weights — the Gate 11
checkpoint-backed predictor and the Gate 12 matched base-model predictor were
evaluated through this engine with ZERO changes to the task, scoring,
normalization, metrics, or artifact format. See
`../gate12/checkpoint-benchmark.md` and ADR-0029.
