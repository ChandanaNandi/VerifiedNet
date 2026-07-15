# 0030 — Evaluation corpora are registered, versioned, quality-verified artifacts; structured-output reliability is measured evidence, never a parsing change

**Status:** Accepted (owner decision, Gate 13)
**Date:** 2026-07-15

## Context

Gate 12's honest conclusion — "engineering proof succeeded; model-quality
evidence inconclusive" — had two root causes that no existing ADR governed:
the evaluation corpus was an anonymous transient fixture (no identity, no
version, no recorded provenance, zero eligible test examples), and the real
predictors' dominant failure mode (unparseable JSON) was visible only as an
undifferentiated invalid count. Without rules, both degrade: conclusions get
grounded on whatever corpus happened to be lying around, and parsing quietly
"improves" to make outputs count.

## Decision

1. **A measurement-grounding corpus must be a REGISTERED artifact.** An
   evaluation corpus is a Gate 6 prepared corpus bound into an immutable,
   content-addressed, VERSIONED registration carrying explicit provenance
   (`fixture_generated` vs `project_persisted`), a frozen generation policy
   whose source is Literal-locked to verified run artifacts, deterministic
   coverage statistics (including the eligible-test-example count), and a
   fail-closed structural quality verdict — duplicate ids, split leakage,
   malformed examples, and missing evidence refuse registration; imbalance is
   reported, never silently rebalanced. A registration for an unverified
   corpus is unrepresentable.

2. **Structured-output reliability is measured, never repaired.** Invalid
   model output is categorized deterministically from the unchanged Gate 8
   reason codes and bounded excerpts; compliance with the response contract
   is a reported rate. The authoritative parser, the prompts, Gate 7 scoring,
   and Gate 9 ranking may not change to improve these numbers — reliability
   reports are separate immutable artifacts keyed to a benchmark, and rates
   are never ranked on.

3. **Conclusions name their corpus.** Any future measurement claim must be
   attributable to a specific registered corpus version and its provenance;
   the Gate 12 interpretation policy's fixture/underpowered rules apply
   against the registered coverage facts, not against prose.

## Consequences

- "Not enough evaluation data" is now a checkable, versioned fact (the
  registered coverage report), and fixing it is an auditable corpus-version
  increment — not a silent fixture change.
- The JSON-reliability problem is quantified per predictor and per category
  (prose-wrapped output vs degenerate repetition vs schema violations),
  giving any future remediation gate a measured baseline to be judged
  against under ADR-0029's matched-comparison rule.
- Parsing can never be loosened to flatter a model: the parser is pinned by
  contract test, and reliability improvements must show up as measured
  compliance, not as reclassified outputs.

## References

- `docs/architecture/gate13/evaluation-corpus.md`
- ADR-0018 (datasets derive from verified runs), ADR-0019 (deterministic
  evaluation), ADR-0021 (benchmarks compare without changing evaluation),
  ADR-0029 (matched, unconfounded comparisons with policy-governed wording).
