# Gate 9 — Multi-Predictor Benchmark Framework

**Status:** IMPLEMENTED (Gate 9). This document describes the deterministic
multi-predictor benchmark layer in `verifiednet.evaluation` that compares multiple
predictors against the same prepared corpus, under the same task, through the
UNCHANGED Gate 7 evaluation engine, and emits immutable, content-addressed
comparison + ranking artifacts. It implements ADR-0021. Gate 9 adds no model
capability; it ensures fair, reproducible, side-by-side benchmarking.

## 1. What the benchmark holds constant

Every predictor runs under identical conditions — same task, feature/prompt
contract, scoring policy, normalization policy, and feature policy — via
`evaluate_prepared_corpus` (Gate 7, unchanged). The benchmark compares predictors;
it never changes evaluation, never exposes labels/metadata/split/identity to a
predictor, and never mutates an evaluation artifact or any earlier stage.

## 2. Predictor registry

`PredictorRegistry` holds the predictors to compare and is deterministic:
registration order never affects results (it always yields predictors sorted by
identifier) and duplicate identifiers are refused. A predictor's benchmark
identifier is its Gate-7 `BaselineSpec.baseline_id` — the same id the evaluation
engine and evaluation manifests already use — so rule baselines and model-backed
predictors are compared uniformly. Each `PredictorEntry` exposes the predictor
spec, identifier, supported task ids, and supported feature-policy ids.

## 3. Benchmark specification and identifier

`BenchmarkSpec` (frozen) records the benchmark name/version, task id, prepared
digest, the sorted predictor identifiers, normalization policy id, and scoring
policy version. Its `benchmark_id = "bench-" + sha256_canonical({those fields with
predictor identifiers sorted})[:16]` is content-addressed and ORDER-INDEPENDENT
(changing predictor order does not change the id); the model validates its own id.

## 4. Runner

`run_benchmark(prepared, *, task, predictors)` is pure: it fails closed on an empty
predictor set, a duplicate identifier, or a predictor built for a different task;
evaluates predictors in sorted-identifier order (input order is irrelevant);
reuses the Gate 7 engine per predictor; and returns a `BenchmarkResult`
(spec + evaluation runs + comparison + ranking). It never mutates predictors or
evaluation artifacts.

## 5. Comparative metrics

Per predictor, a `ComparisonRow` carries deterministic, comparable metrics:
accepted evaluated/correct counts and accepted diagnosis accuracy (exact match),
abstention count/correct and abstention accuracy, invalid-prediction count, and
total evaluation count. All ratios are the deterministic 6-place decimal strings
from Gate 7 (or `None` for a zero denominator). No wall-clock timing enters any
immutable artifact.

## 6. Ranking

`compute_ranking` is a pure, fully tie-broken total order. Tie-break order,
documented explicitly:

1. accepted diagnosis accuracy, descending (`None` lowest);
2. abstention accuracy, descending (`None` lowest);
3. invalid-prediction count, ascending (fewer is better);
4. predictor identifier, ascending (stable final tie-break).

Because the predictor identifier is a strict final key, ranks form a dense total
order (`1..n`) with no ambiguous ties.

## 7. Immutable storage, digest, verifier

A benchmark is written to `benchmarks/<benchmark_id>/` — separate from evaluations
— as `manifest.json` + `comparison.json` + `ranking.json`, atomically under a
`.INCOMPLETE` marker, refusing to overwrite an existing benchmark. The
`BenchmarkManifest` embeds the spec and the evaluation identifiers (one per
predictor) and carries a non-recursive self-validating `benchmark_digest` derived
from the spec, the evaluation identifiers, and the path-sorted content-file hashes.

`verify_benchmark` is structured and fail-closed: manifest present/parses,
schema/format supported, exact expected file set, per-file hash, digest
re-derivation, and — crucially — it RE-COMPUTES the ranking from the stored
comparison and confirms the comparison covers exactly the manifest's predictors
and evaluation identifiers. It never trusts the stored ranking. `read_benchmark`
verifies first and fails closed.

## 8. Determinism and reuse

Benchmark results are independent of predictor execution order, registration
order, filesystem order, dictionary iteration, machine, and timestamps. Evaluation
logic is not duplicated — benchmarking consumes Gate 7 evaluation runs, which
remain the single source of truth.

## 9. Guarantees proven by test

Order independence (shuffled predictors → identical benchmark id, spec, ranking);
benchmark-id stability; deterministic total-order ranking; predictors still
receive only features during benchmarking; and fail-closed handling of duplicate
predictors, task mismatch, corrupted comparison/manifest, digest mismatch, missing
files, and an inconsistent ranking. Gate 7 evaluations and the Gate 8 predictor
interface are unchanged.

## 10. Limitations and next steps

The v1 corpus is tiny, so a benchmark ranking demonstrates the framework, not a
statistical comparison of predictor quality. Gate 9 stops here: no prompt
optimization, ensembling, voting, reranking, fine-tuning, or retrieval. Any later
optimization gate adds predictors to the benchmark set but may not change what the
benchmark holds constant across predictors, nor bypass the evaluator.
