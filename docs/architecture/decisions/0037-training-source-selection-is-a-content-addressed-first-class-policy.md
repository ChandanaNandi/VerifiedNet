# 0037 — Training source-selection is a content-addressed first-class policy; balancing selects but never duplicates, synthesizes, or crosses the train firewall

**Status:** Accepted

## Context

Gate 18B held the pinned model, v2 representation, boundary objective, budget,
and success policy constant and changed only the evidence representation; the
trained model produced the first held-out accuracy gain (3/36) but collapsed the
three `active`-peer-state fault families onto the training majority. The Gate 19
diagnosis (read-only, on the persisted result) proved the v2 representation is
sufficient — the seven observable fields yield one payload per family with zero
collisions and a deterministic four-flag oracle scores 206/206 accepted, 36/36
test — and that every test payload was seen in training. The remaining
bottleneck is training-family imbalance: the natural first-64 corpus is
`25 / 21 / 17 / 1` across the four families, and even 17 neighbor-removal
training examples were misclassified.

Until Gate 19, source selection was implicit: `cap_training_corpus` took the
first N examples in canonical corpus order (`select_corpus_slice` mirrors this at
execution). That is adequate when composition is not under test, but the next
controlled experiment changes composition, and an experiment's independent
variable must be an explicit, auditable, content-addressed artifact — not a
side effect of enumeration order.

## Decision

Training source selection is a **first-class, content-addressed, frozen policy**.
A selection policy binds its format/version, allowed partition, target total,
family order, per-family allocation, scarcity rule, within-family ordering, and
final ordering; applying it yields a self-validating selection result binding the
source prepared digest, the selected sources and their families, the
deterministic order, per-family counts, and a selection digest. Changing any
bound field or any selected identity changes the policy/result id.

Balancing MAY change which sources are selected and their provenance order. It
MUST NOT duplicate, oversample, or synthesize examples; it MUST draw only from
the frozen train partition (never validation/test, preserving the test-set
firewall); it MUST read only accepted labels and never model predictions,
evaluation records, benchmarks, or confusion; and it MUST fail closed on a
missing/short family, an unsupported family, a duplicate identity, or a quota
that cannot be satisfied — never silently redistributing a missing quota. A
different availability profile requires a new policy identity, never silent
adaptation.

The selection changes composition ONLY: for any selected source the v2 feature
derivation, prompt render, target, objective, tokenizer, and token budget are
byte-identical, and the resulting corpus keeps the canonical
`source_example_id` ordering invariant (the round-robin order is provenance in
the result, not corpus order). When an experiment binds a selection policy it
remains subject to ADR-0033 (preregistered, one-run, one-checkpoint, frozen
success policy, test-set firewall).

## Consequences

- Composition becomes a legible independent variable: two experiments that differ
  only in source selection differ only in their corpus id, and a deterministic
  corpus comparison proves shared sources render byte-identically.
- Budget-preservation is a policy choice: the Gate 19A default keeps 64 examples
  (and 64 optimizer steps) by equalising abundant families and taking all
  available of a scarce family, rather than shrinking the corpus — which would
  change a second variable.
- The train-partition firewall is load-bearing: split-scarce families (remote-AS,
  with 4 train examples) cannot be topped up from validation/test, so a balancing
  policy is bounded by availability and must represent scarcity as an explicit
  quota, not hide it.
- This ADR governs the selection contract only. It authorizes no training run and
  no experiment; binding a balanced corpus in a preregistered one-run experiment
  (Gate 19B) remains subject to ADR-0033. Whether balancing removes the collapse
  is an empirical question for Gate 19B; no accuracy claim is made here.

## References

- `architecture/gate19/family-balanced-selection.md` (Gate 19A policy, selection,
  corpus integration, comparison, and the real-chain proof).
- `architecture/gate18/discriminative-evidence-experiment.md` (Gate 18B: first
  held-out gain, majority-class collapse) and the Gate 19 diagnosis.
- ADR-0033 (preregistered one-run experiments), ADR-0034 (training never imports
  evaluation), ADR-0031 (corpus versions are append-only; coverage never
  overrides deterministic splitting).
