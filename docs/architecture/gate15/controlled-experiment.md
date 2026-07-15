# Gate 15 — Controlled Retraining Experiment on Evaluation Corpus v3

**Status:** IMPLEMENTED (Gate 15). The FIRST model-quality experiment: a
preregistered, one-run, matched base-versus-trained supervised fine-tuning
experiment on the v3-era training data, evaluated exclusively on registered
evaluation corpus v3 (`evalcorpus-8c932345efc3e6e6` /
`ecdig-e72927cc7d4b6fd0fa141462`) — the first corpus version whose persisted
readiness assessment (`ready-0b128bea7400a13f`,
`ready_for_controlled_experiment`, ADR-0032) authorizes one. It implements
ADR-0033. **No prompt, parser, scoring, ranking, label, split-policy, or
corpus change; no RAG, agents, deployment, publication, LoRA, warm starts,
or resume.**

## 1. The scientific contract

The question — *does supervised fine-tuning on the expanded, verified v3-era
training corpus improve structured networking-diagnosis performance relative
to the matched pretrained base model?* — is frozen inside a content-addressed
`ControlledTrainingExperimentSpec` (`exp-…`) persisted BEFORE any training
executes. The spec binds the corpus (id + digest + readiness id, with
`readiness_outcome` Literal-locked to `ready_for_controlled_experiment` — an
experiment against an unready corpus is unrepresentable), the hypothesis, the
frozen primary/secondary metrics, the frozen success policy, the training
corpus/spec/plan identities, the approved model
(`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae5576…`, tokenizer pinned identically),
the matched-inference facts (prompt template, decoding, normalization,
scoring version, interpretation policy), and the runtime envelope.
`maximum_training_runs` and `max_treatment_checkpoints` are Literal `1`.
Finalization refuses unless a byte-identical preregistration is already on
disk — a spec is never modified after persistence; a new question is a new
experiment in a later gate.

## 2. Train-only derivation and the preregistered cap

The Gate 15 training corpus derives from the AUTHORITATIVE v3 prepared chain
(never from the registered evaluation-corpus artifact) through the unchanged
Gate 10A builder — structurally train-partition + accepted-labels only, with
the leakage audit on every rendered payload. All **128** accepted train
examples are eligible; the experiment corpus is the deterministic
**first-64** prefix in canonical corpus order (`cap_training_corpus`, the
same first-N-canonical-order rule as the Gate 10F slice). The cap is
PREREGISTERED with its exact structural reason: the Literal-locked Gate 10F
`RealTrainingExecutionPolicy` safety envelope permits at most 64 examples /
64 optimizer steps per bounded real run (and authorization revalidation
checks the PLAN-level counts), plus CPU full-fine-tuning practicality. No
hand selection, no model-output filtering, no benchmark-informed choice —
there is no code path through which one could occur (the training package
cannot import evaluation; AST-enforced).

## 3. One bounded, meaningful run

The preregistered configuration (fixed before any result existed): CPU full
fine-tune of the pinned snapshot via the unchanged Gate 10F authorized
executor — batch 1 × gradient-accumulation 2 (effective 2), **2 epochs over
64 examples = 64 optimizer steps** (exactly the envelope ceiling), sequence
policy 384/64/448, AdamW lr `0.00002`, weight-decay 0, grad-clip 1,
linear-warmup 4 steps, all seeds 15. This is deliberately NOT the Gate 10F.1
one-example/one-step engineering proof: 128 example-passes over 64 distinct
examples spanning all four fault families. Retry and resume remain
Literal-unsupported; checkpointing is on-completion-only; a failed run
preserves its verified FAILED execution and the experiment stops as
`experiment_failed` with no finalized store. Reproducibility posture is
honest: intent, corpus, identities, plan, authorization, ordering, seeds,
objective, optimizer configuration, lineage, and structural evidence
reproduce; bit-identical trained weights across machines are NOT claimed
(`claims_replay_determinism = False`). Validation is diagnostic only —
the training path has no channel for non-train examples, and validation
structured-output metrics are recorded post-checkpoint from the evaluation
runs, never used to select among checkpoints (there is only one).

## 4. The test-set firewall

Held-out truth is unreadable until the checkpoint is verified, and the claim
is proven three ways: (1) the ordered phase declaration — `PREREGISTERED →
TRAINING_CORPUS_FINALIZED → PLAN_AUTHORIZED → TRAINING_COMPLETED →
CHECKPOINT_VERIFIED → TEST_EVALUATION_STARTED → BENCHMARK_COMPLETED →
RESULT_INTERPRETED` — where a log that is not an exact prefix of that
sequence is unrepresentable and a finalized result requires the complete
sequence; (2) the structural `audit_test_firewall`, which scans the ACTUAL
serialized training-side bytes (corpus store, plan, authorization, execution,
checkpoint manifest) for every held-out example/group/run identifier from the
source prepared corpus — content-addressed identifiers are unforgeable
substrings — and verifies every training source is a train-partition
accepted example; (3) the package boundary — training imports no evaluation
module, the executor's signature has no evaluation parameter, and the
classification input model has no field for a rank, a loss, or a
train/validation accuracy.

## 5. Matched measurement, unchanged contracts

Base and trained predictors share ONE inference stack (Gate 11/12): same
architecture, tokenizer, prompt template, candidate-family list, decoding,
normalization, CPU fp32 device policy, and evaluation corpus — model weights
are the only intended difference, and Gate 12's fairness checks remain
authoritative (a confounded comparison classifies as `experiment_failed`,
never as model quality). Evaluations run for the fixed-prior baseline, the
evidence-rule baseline, the matched base model, and the Gate 15 checkpoint
through the unchanged Gate 7 engine; the unchanged Gate 9 benchmark stays a
DESCRIPTIVE comparison (its binding is Literal-marked `descriptive_only`);
Gate 13 structured-output reliability is measured for both model predictors
with the unchanged parser (no permissive parsing) — Gate 12's base produced
prose-wrapped JSON and the first checkpoint produced degenerate repetition,
so whether those failure modes moved is first-class evidence, diagnostics
only, never ranked on.

## 6. The frozen success policy and outcome rule

`ExperimentSuccessPolicy` (`esucc-…`) Literal-locks every `improved`
requirement: ≥ 30 eligible test examples, unconfounded comparison, strictly
higher accepted test accuracy, paired wins strictly exceeding paired losses,
no invalid-prediction increase (tolerance Literal 0), and no abstention
regression. The outcome rule is total and deterministic with fixed
precedence: confounded → `experiment_failed`; underpowered →
`inconclusive`; all criteria → `improved`; degradation + improvement (or
improvement short of the criteria) → `mixed`; degradation alone →
`regressed`; nothing moved → `unchanged`. Property tests prove the honesty
cases: rank alone, training loss, train accuracy, and validation-only
movement have no input channel; fewer invalid outputs with lower accuracy is
`mixed`; one-dimension gains with an accuracy regression are never
`improved`; a failed experiment is never an inconclusive quality verdict.

## 7. The experiment-result artifact

`controlled-experiments/<exp-…>/` holds the preregistered spec plus
`training-binding.json` (corpus/slice/spec/plan/authorization/execution by
id + digest, completed steps/epochs, loss evidence), `checkpoint-binding.json`
(the ONE treatment checkpoint with fail-closed lineage checks),
`evaluation-bindings.json`, `benchmark-binding.json`, `paired-summary.json`
(all-partition, non-train, TEST-ONLY, and per-family paired quadrant
counts), `reliability-summary.json`, `interpretation.json` (the
self-validating `ControlledTrainingExperimentResult` — outcome and every
success check are re-derived from the recorded raw counts under the embedded
policy, so a result claiming `improved` without satisfying every criterion
is unrepresentable), and a hash-binding manifest. Large artifacts are bound
by id + digest, never duplicated. Finalization cross-checks every binding
against the spec and the result (corpus/plan identity, envelope conformance,
checkpoint-execution lineage, paired-count and reliability-count agreement,
benchmark coverage of both model predictors) and refuses a second
finalization; verification re-runs the same cross-checks from disk.

## 8. Layering: the experiment package

`verifiednet.experiment` is a NEW top composition layer (alongside the
orchestrator): the ONE package permitted to import both the training
lifecycle and the evaluation stack, composing them FORWARD into an
experiment. Nothing may import it; it is statically ML-free; and the
ADR-0022 edge is untouched — training still cannot import evaluation. The
AST boundary guard enforces all of this.

## 9. Proof obligations discharged by tests

Spec/policy/result id stability + sensitivity; one-run/one-checkpoint
unrepresentability; readiness-Literal; phase-prefix structural rule +
Hypothesis phase-order properties; outcome totality/determinism/honesty
under Hypothesis; paired-count arithmetic across partitions; firewall
detection of any injected held-out identifier and of smuggled test sources;
preregistration-required/immutable/refuse-second-finalization; per-byte
store tamper detection; cross-check failure paths (wrong corpus, foreign
lineage, benchmark coverage, paired/reliability mismatches); model-free +
network-free offline chain; no-host-facts artifacts; build-twice
byte-identical offline experiments; and the double-gated operational test
that runs the REAL experiment end-to-end with source fingerprints and
without ever asserting improvement.
