# 0022 — Training data is train-only, evaluation-isolated, and derived

**Status:** Accepted (owner decision, Gate 10A)
**Date:** 2026-07-14

## Context

Gates 6–9 produce verified datasets, a leakage-safe prepared corpus, a
deterministic evaluation framework, and fair multi-predictor benchmarking. The
next step toward a trained model is the most dangerous one: the moment examples
are converted into supervised input/target pairs, an unguarded pipeline can
silently train on test data, leak evaluator-only truth into model input, or turn
evaluation results into training signal — corrupting every future measurement.
ADR-0018 through ADR-0021 protect the dataset and evaluation layers; none of
them yet governs what may become TRAINING data. This ADR fixes that boundary
before any trainer exists.

## Decision

1. **Only train-partition, accepted-fault, accepted-diagnosis examples may enter
   supervised training.** Validation, test, and abstention examples are
   structurally excluded: the Gate 10A ``TrainingDataPolicy`` locks eligibility
   with Literal types (a policy permitting anything else cannot be constructed),
   and per-example trace metadata binds ``partition="train"`` the same way. A
   later gate may introduce a different training task only through a new,
   explicitly versioned policy — never by loosening this one.

2. **Evaluation and benchmark artifacts are never training sources.** The
   training package may not import ``verifiednet.evaluation`` (AST-enforced),
   and no evaluation record, metric, outcome category, correctness flag, or
   benchmark ranking may appear in training input, target, or corpus derivation.
   Information flows prepared corpus → training corpus, never evaluation →
   training.

3. **Model input is an explicit allowlist rendering of model-visible features.**
   The training input template renders ONLY the Gate 6 feature allowlist plus
   the public candidate class list and output schema — never identity, split,
   digests, policy ids, rejection facts, or label text beyond the intended
   supervised target. Input is never produced by dumping an example and deleting
   fields. The training template is deliberately independent of the Gate 8
   inference prompt (distinct content-addressed identities); any intentional
   sharing must be explicit, never implicit.

4. **The supervised target comes directly from the authoritative accepted
   label,** serialized as canonical JSON so equivalent labels are byte-identical.
   Targets are never reconstructed from feature text and never carry
   correctness, confidence, reasoning, ranking, recovery data, identity, or
   digests.

5. **Training corpora are derived, immutable, content-addressed, and
   independently reproducible.** Every policy/template/example/corpus identity
   is a validated content hash; each example's id binds its rendered input and
   target to its source example and governing policies, so tampering fails at
   parse time. The persisted corpus keeps input/target/metadata in separate
   files; the trainer-facing loader returns ONLY input/target pairs.

6. **Partition isolation is a testable guarantee.** Changing only
   validation/test/abstention examples leaves the training corpus identity and
   its input/target/metadata bytes unchanged; only the manifest's provenance
   pins track the changed source. This is proven by test, not policy.

7. **Gate 10A trains nothing.** No torch/transformers/PEFT/optimizer/checkpoint
   code exists or is imported (AST-enforced); the gate's output is training-data
   infrastructure only.

## Consequences

- A future trainer (Gate 10B+) consumes a verified, leakage-audited, train-only
  corpus through a narrow pairs API — it cannot accidentally see test data,
  evaluator truth, or bookkeeping identity.
- Evaluation integrity survives training: the examples a model is measured on
  are structurally unreachable during training-corpus construction.
- The tiny v1 corpus makes this a methodology proof, not a useful training set —
  documented, as always, rather than hidden.

## References

- `../gate10/training-corpus.md`
- ADR-0018 (datasets derived/leakage-safe), ADR-0019 (deterministic model-free
  evaluation), ADR-0020 (models behind the feature-only boundary), ADR-0021
  (benchmarks compare without changing evaluation)
