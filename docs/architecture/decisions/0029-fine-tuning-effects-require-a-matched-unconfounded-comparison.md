# 0029 — Fine-tuning effects may be claimed only under a matched, unconfounded base-versus-trained comparison, worded by a frozen interpretation policy

**Status:** Accepted (owner decision, Gate 12)
**Date:** 2026-07-15

## Context

Gate 11 made a trained checkpoint predict; the next temptation is to say what
the training "did". Without a rule, that claim degrades in two ways: the
comparison drifts (a different prompt, decoding, precision, device, or model
family quietly becomes "the base"), and the wording drifts (one changed
example on a fixture corpus becomes "the model learned networking"). ADR-0019
fixed evaluation determinism, ADR-0021 fixed fair benchmarking mechanics, and
ADR-0028 fixed how weights reach prediction — none of them fixed what may be
SAID about a training effect.

## Decision

1. **A training-effect claim requires a matched pair.** The base and trained
   predictors must share the same architecture scope, prompt template,
   decoding configuration, normalization policy, backend family, inference
   precision, device policy, task, and prepared corpus — verified by an
   explicit fairness assessment whose failures are visible
   (`confounded_fields`), never silent. A confounded pair is structurally
   incapable of an unqualified training-effect conclusion. Substituting a
   different model family or serving stack (e.g. an Ollama model) as "the
   base" is forbidden; the matched base is the approved pinned snapshot
   through the SAME verified-bundle inference stack.

2. **Effect evidence is exact paired counts over aligned example ids.** Raw
   counts precede every ratio; improvements AND regressions are first-class;
   invalid predictions are counted per side; the training partition is
   excluded from conclusions by a Literal-locked policy field.

3. **Wording is governed by a frozen, content-addressed interpretation
   policy — and wording is ALL it governs.** Thresholds (minimum eligible
   test examples, minimum changed predictions) live in the versioned policy,
   never in prose. A fixture-generated corpus permits only an engineering
   conclusion; an underpowered corpus is labeled inconclusive; zero changed
   predictions is "no observed effect"; regressions are always surfaced even
   when a ranking improves. The policy has no access to Gate 7 metrics or
   Gate 9 ranking and cannot alter them.

4. **Measurement never feeds training.** Comparisons and disagreement
   reports are terminal, immutable, content-addressed artifacts; no automatic
   data selection or retraining may consume them (ADR-0022 unchanged, and the
   training layer cannot parse them).

## Consequences

- "Fine-tuning improved the model" becomes a checkable statement: it requires
  a fair pair, non-train evidence, policy-satisfying sample counts, and zero
  hidden confounds — or it must be worded as inconclusive/engineering-only.
- The first real benchmark result is honestly expected to read "engineering
  proof succeeded; model-quality evidence remains inconclusive" — and the
  framework makes that the DEFAULT wording rather than a reluctant footnote.
- Future experiments (more data, more steps) inherit the same rule: better
  numbers change conclusions only through the same fairness + policy gate.

## References

- `docs/architecture/gate12/checkpoint-benchmark.md`
- ADR-0019 (deterministic evaluation), ADR-0021 (benchmarks compare without
  changing evaluation), ADR-0022 (training/evaluation isolation), ADR-0028
  (verified checkpoint-backed prediction).
