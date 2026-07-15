# Gate 12 — Evaluate and Benchmark the First Trained Checkpoint

**Status:** IMPLEMENTED (Gate 12). The measurement gate: the Gate 11
checkpoint-backed predictor is evaluated through the UNCHANGED Gate 7 engine
and compared through the UNCHANGED Gate 9 benchmark against a MATCHED
base-model predictor and the two deterministic rule baselines. It implements
ADR-0029 on top of ADR-0019/0021/0028. **Nothing here optimizes anything: no
retraining, no prompt change, no decoding change, no corpus change, no
hyperparameter tuning.**

## 1. Why the evaluation and benchmark contracts remain unchanged

Gate 7's engine takes any `Baseline` (a `spec` + `predict(features)`), and
Gate 9's `run_benchmark` takes a sequence of them — the checkpoint predictor
and the new matched base predictor plug in with ZERO engine changes. That was
the entire point of ADR-0020's boundary: this gate proves it. Task id,
scoring, normalization, metrics, ranking, and both artifact formats are
byte-compatible with Gate 7/9.

## 2. The matched base-model predictor

The only scientifically useful comparison is: same architecture, same prompt,
same decoding, same task, same corpus — **different weights only**. The base
side is NOT an Ollama model or another family; it is the approved original
local snapshot (`Qwen/Qwen2.5-0.5B-Instruct` at the pinned immutable 40-hex
revision), verified fail-closed from disk (`verify_base_model_dir`: required
files only, no symlinks, structural safetensors parse, config architecture
match, content hashes) and wrapped in a `VerifiedBaseModelBundle`
(`basemodel-…` content identity, no model loading at construction,
moment-of-use `reverify()`). The bundle satisfies the same
`VerifiedInferenceBundle` protocol as the Gate 11 checkpoint bundle, so the
SAME `HfCheckpointInferenceBackend` serves both sides — one inference stack,
no second code path. `BaseModelPredictorSpec` (`basepred-…`) binds the exact
snapshot bytes plus the identical prompt/decoding/normalization/backend/
precision/device fields the checkpoint spec binds, enabling one-to-one
fairness checks. Mutable revisions are unrepresentable.

## 3. Fairness: matched means matched

`assess_matched_pair_fairness` compares `MATCHED_FAIRNESS_FIELDS`
(prompt-template id, decoding config id, normalization policy id, backend
family, inference precision, device policy id, inference-compatibility id)
plus task, prepared digest, and feature/label policies. Any difference lands
in `confounded_fields`, the pair is `fair=False`, and the interpretation
layer can then only conclude `confounded` — a confounded comparison can never
be worded as a fine-tuning effect (proven by test).

## 4. Paired comparison and disagreement report

`build_paired_comparison` aligns the two runs by `example_id` (fail-closed on
any task/corpus/policy/target/partition/alignment mismatch) and derives exact
counts — both-correct, both-incorrect, base-correct/trained-incorrect,
base-incorrect/trained-correct, identical-vs-differed predictions, invalid
counts per side, abstention-decision changes — twice: over all aligned
examples and over the non-train subset (the training partition can never be
evidence). Each differed example becomes a `DisagreementRecord` with the
structured predictions, unchanged Gate 7 outcome categories, and a
`TransitionCategory` (`unchanged_correct`, `unchanged_incorrect`, `improved`,
`regressed`, `changed_but_still_incorrect`) plus an abstention-change flag.
This is evaluator-only evidence: it is never fed back into training (the
training layer cannot even parse a comparison artifact — proven by test), and
no chain-of-thought is requested or stored.

## 5. Interpretation policy: wording only

`BenchmarkInterpretationPolicy` (`interp-…`, frozen, content-addressed)
versions the honesty thresholds instead of hiding them: default minimum 30
eligible test examples for a directional claim, minimum 1 changed prediction,
train partition Literal-excluded from conclusions, fixture-generated corpora
Literal-locked to engineering conclusions. `interpret_paired_comparison` is a
pure function of (comparison, policy, provenance) producing one of:
`confounded`, `no_observed_effect`, `inconclusive_underpowered`,
`better_on_this_corpus`, `worse_on_this_corpus`, `mixed_on_this_corpus`,
`unchanged_on_this_corpus` — plus sorted qualifiers (regressions are ALWAYS
surfaced; a single changed example is labeled anecdotal; underpowered results
carry "engineering proof only — insufficient evidence for model-quality
conclusions") and raw-count reasons. The policy has no access to metrics or
ranking and cannot change them.

## 6. Persistence and verification

Evaluations use the unchanged Gate 7 store; the benchmark uses the unchanged
Gate 9 store. The paired result persists separately as
`comparisons/<cmp-…>/{manifest.json, summary.json, disagreements.jsonl}` —
immutable, canonical, content-addressed (`cmpdig-…`), atomic under
`.INCOMPLETE`, overwrite-refusing, free of timestamps/paths/machine facts.
The verifier recomputes hashes, digest, disagreement alignment against the
counts, and the interpretation itself; it deliberately does NOT re-run any
model. Runtime distinction (documented, tested): real model text is not
claimed bit-identical across machines — the one authoritative run's persisted
structured predictions, bound into the immutable evaluations, ARE the
evidence, and every derived metric is recomputed from those records.

## 7. Statistical honesty for the first benchmark

The evaluation corpus is FIXTURE-GENERATED (the same deterministic Gate 6
chain the test suite uses), not a persisted project corpus — so by policy the
strongest possible conclusion is an engineering proof. The corpus is tiny
(single-digit examples; the exact counts are in the persisted artifacts), far
below the 30-example directional threshold. Invalid-prediction counts per
side are first-class in both the Gate 9 comparison rows and the paired
counts. Expected honest outcome for the one-example/one-step checkpoint:
"engineering proof succeeded; model-quality evidence remains inconclusive."

## 8. Proof obligations discharged by tests

Unchanged Gate 7/9 semantics (contract), fairness proof (a deliberately
confounded pair is marked and can only conclude `confounded`), paired-count
sum invariants, exhaustive conclusion-mapping honesty (regressions always
surfaced; fixture always engineering-only; train-only changes read as no
observed effect), comparison-id sensitivity, store round-trip + per-byte
tamper evidence + overwrite refusal, feature-only boundary for BOTH matched
predictors, no-network full pipeline, no-training traps armed across the
whole measurement phase, source immutability across prepared corpus /
dataset export / training artifacts / checkpoint / base model, build-twice
byte-identical benchmark and comparison artifacts, and the training layer
refusing comparison artifacts as input. The real end-to-end run is one
integration test gated on `VERIFIEDNET_RUN_REAL_GATE12=1` +
`VERIFIEDNET_REAL_CHECKPOINT_DIR` + `VERIFIEDNET_BASE_MODEL_DIR` + the
`training-hf` extras, asserting structural consistency only.

**Update (Gate 13):** the two weaknesses this gate exposed are now measured
foundations — evaluation corpora are registered, versioned, quality-verified
artifacts with explicit provenance and an eligible-test-example count
(ADR-0030), and the real predictors' JSON failures are named deterministic
categories with per-run compliance statistics in a separate structured-output
report. See `../gate13/evaluation-corpus.md`.

**Update (Gate 14):** the eligible-test shortfall is being closed through
append-only corpus versions: v2 carries 22 eligible test / 18 validation
examples from 30 stable identities across three approved topology variants —
still below this gate's 30-example directional threshold, and said so in the
registered coverage artifact (ADR-0031). See
`../gate14/corpus-expansion.md`.

## 9. Explicitly out of scope

No further training, prompt optimization, corpus expansion, checkpoint
deployment, publication, RAG, agents, warm starts, adapters, or production
integration. Documentation may recommend future experiments; nothing here
executes them.

## Gate 15 note

The matched base-versus-trained design, fairness checks, paired comparison,
and interpretation policy defined here are consumed UNCHANGED by the Gate 15
controlled experiment, which adds a preregistered frozen SUCCESS policy on
top: outcomes derive from raw paired counts, a confounded comparison is an
`experiment_failed`, and rank alone can never establish improvement
(ADR-0033; see `../gate15/controlled-experiment.md`).
