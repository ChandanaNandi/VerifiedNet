# 0032 — Experiment readiness is determined by independent held-out identity coverage, not raw row count alone

**Status:** Accepted (owner decision, Gate 14B)
**Date:** 2026-07-15

## Context

Gate 14 exposed a failure mode that pure row-count targets cannot catch: v2
met its example minimums (22 eligible test ≥ 20) while those rows spanned
only 5 distinct stable scenario identities. Because split assignment is
keyed by the stable `group_id`, every re-execution of the same identity
lands in the same partition — so re-running a handful of test-landing
identities can satisfy ANY row threshold without adding one independent
held-out case. A model evaluated on such a corpus is measured on 5 things,
however many rows they occupy, and a "significant" result on it would be an
artifact of duplication. ADR-0029 fixed the example threshold for
directional claims; ADR-0031 fixed how corpora may grow; neither fixed what
"enough coverage to run an experiment" MEANS.

## Decision

1. **Readiness is a two-axis verdict.** A corpus version may power a
   controlled experiment only when BOTH the example thresholds (eligible
   test, validation) AND the identity-diversity thresholds (distinct
   held-out test identities, distinct validation identities, topology
   variants) are met. The verdict is one of exactly four outcomes with fixed
   precedence — `quality_failed`, `underpowered`,
   `coverage_threshold_met_but_low_diversity`,
   `ready_for_controlled_experiment` — and rows-without-diversity is
   NAMED (`coverage_threshold_met_but_low_diversity`), never rounded up to
   ready.

2. **The verdict is self-validating and immutable.** The readiness
   assessment re-derives its outcome and every check from its own recorded
   facts and thresholds inside the model validator — an assessment claiming
   readiness its numbers do not support is unrepresentable — and persists
   content-addressed under `readiness-assessments/<ready-…>/`, bound to the
   corpus id + digest and to the frozen policies that supplied the
   thresholds.

3. **Expansion planning is identity-first.** Coverage campaigns select NEW
   stable identities before reproducibility repeats, in an explicit
   deterministic priority order (missing test identity → missing validation
   identity → underrepresented family → underrepresented topology → missing
   parameter dimension → rejected coverage → repeats), tie-broken by
   canonical stable identity. Repeats per identity are bounded (2-4
   accepted runs) and allocated by frozen per-partition rule — repeats are
   reproducibility evidence, never threshold padding. The selection is a
   content-addressed artifact, and identity-minimum checks merge into the
   same fail-closed gate as the example checks, so an identity shortfall
   makes a descendant registration structurally impossible.

4. **Splitting stays sovereign.** The planner predicts partitions with the
   exact production splitter over fully-defined identities and has no
   parameter through which it could assign, move, or exclude an example by
   partition (unchanged from ADR-0031; restated because identity-first
   planning reads split predictions and must never write them).

## Consequences

- The project cannot "reach 30 test examples" by re-executing favorites; the
  only path to readiness is genuinely new approved identities (parameters,
  topologies, orientations, targets) through the authoritative chain.
- Gate 15 (the first adequately-powered controlled experiment) is authorized
  by a persisted `ready_for_controlled_experiment` verdict, not by a row
  count in a report.
- Corpus v3 (36 eligible test examples across 12 identities, 42 validation
  across 14, 6 topology variants) is the first version to carry that
  verdict.
- Future coverage work inherits the vocabulary: any corpus whose rows
  outgrow its identities is visibly `low_diversity`, and the identity
  deltas are first-class rows in every corpus comparison.

## References

- `docs/architecture/gate14b/identity-coverage.md`
- ADR-0029 (interpretation thresholds), ADR-0031 (append-only versions,
  splitting never overridden)
