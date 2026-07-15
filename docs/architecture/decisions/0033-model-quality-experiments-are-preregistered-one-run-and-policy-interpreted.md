# 0033 — Model-quality experiments are preregistered, one-run, one-checkpoint, and interpreted by a frozen success policy behind a test-set firewall

**Status:** Accepted (owner decision, Gate 15)
**Date:** 2026-07-15

## Context

With a readiness-authorized evaluation corpus (ADR-0032) the project can ask
its first genuine model-quality question. This is exactly where ML results
silently rot: hyperparameters get tuned against the test set, training is
repeated until a number improves, the hypothesis drifts to fit the result,
a benchmark rank stands in for paired evidence, and infrastructure failures
get reported as "inconclusive model quality". ADR-0029 fixed interpretation
wording and ADR-0026/0027 fixed how real training is authorized and
verified; none of them fixed what a legitimate EXPERIMENT is.

## Decision

1. **Preregistration is mandatory and immutable.** A controlled experiment
   exists only as a content-addressed specification — question, hypothesis,
   frozen metrics, frozen success policy, every bound identity (corpus +
   readiness, training corpus/spec/plan, approved model, matched-inference
   facts), and the runtime envelope — persisted BEFORE any training
   executes. Finalization refuses unless a byte-identical preregistration is
   on disk. Changing anything after seeing data means a NEW experiment in a
   later gate.

2. **One run, one treatment checkpoint.** `maximum_training_runs` and the
   checkpoint ceiling are Literal `1`; retry/resume/intermediate
   checkpoints remain unsupported; a failed run preserves its verified
   failed execution and the experiment ends as `experiment_failed` — never
   silently retried, never re-tuned.

3. **A test-set firewall separates the phases.** The ordered phase
   declaration (preregistered → corpus finalized → plan authorized →
   training completed → checkpoint verified → test evaluation started →
   benchmark completed → result interpreted) admits no backward or skipped
   transition; a structural audit scans the actual serialized training-side
   bytes for every held-out identifier; and the package boundary keeps
   evaluation facts unimportable from training. Held-out truth is readable
   only after the checkpoint is verified.

4. **Outcomes come from a frozen success policy over raw paired counts.**
   `improved` requires ALL of: enough eligible test examples, an
   unconfounded comparison, strictly higher accepted test accuracy, paired
   wins strictly exceeding paired losses, no invalid-output increase, and
   no abstention regression — each Literal-locked. The result artifact
   re-derives its outcome from its own recorded counts, making a dishonest
   claim unrepresentable. Rank, training loss, train accuracy, and
   validation-only movement have no input channel. Infrastructure failure
   is `experiment_failed`, never a quality verdict.

5. **The experiment layer composes forward only.** `verifiednet.experiment`
   is a top layer that may import training and evaluation; nothing imports
   it; it is ML-free; ADR-0022 (training never imports evaluation) is
   unchanged.

## Consequences

- "Retrain until it improves" is structurally impossible inside a gate: the
  second run has no representable specification.
- A result can be `mixed` or `regressed` and the project must say so — the
  wording is derived, not chosen.
- Deterministic caps (e.g. Gate 15's preregistered first-64 canonical-order
  cap under the Gate 10F Literal envelope) are part of the preregistered
  contract, never a post-hoc adjustment.
- Every future experiment (new hyperparameters, more data, LoRA, larger
  models) is a new preregistered specification in a new gate, compared
  through the same frozen machinery.

## References

- `docs/architecture/gate15/controlled-experiment.md`
- ADR-0022 (evaluation is not a training source), ADR-0026/0027 (authorized,
  verified real training), ADR-0029 (interpretation policy), ADR-0032
  (readiness by identity coverage)
