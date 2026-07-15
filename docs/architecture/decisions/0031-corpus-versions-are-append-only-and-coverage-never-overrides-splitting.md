# 0031 — Corpus versions are append-only descendants; coverage targets may drive new verified scenario generation but may never override deterministic splitting

**Status:** Accepted (owner decision, Gate 14)
**Date:** 2026-07-15

## Context

The measurement foundation needs to GROW (v1 has 2 eligible test examples;
ADR-0029 demands 30 for a directional claim), and growth is exactly where
evaluation corpora silently rot: examples get moved into test "just this
once", a split salt gets tweaked until the numbers look right, an old version
gets edited in place, or the choice of what data to add starts tracking which
examples the current model gets wrong. ADR-0018 fixed where examples come
from and ADR-0030 fixed that corpora are registered artifacts — neither fixed
how corpora may CHANGE.

## Decision

1. **Corpus versions are append-only descendants.** A new version is a new
   content-addressed registration binding its parent's id + digest, the
   frozen expansion policy and plan that motivated it, and the generation
   campaign that produced its runs. Parents are never edited, overwritten, or
   re-registered; version listing returns every verified version in
   deterministic order; self-parenting and expansion bindings on version 1
   are unrepresentable.

2. **Coverage targets drive GENERATION, never ASSIGNMENT.** An expansion
   policy states minimum coverage (with mandatory-versus-advisory explicit);
   deficits justify producing NEW verified runs for NEW stable scenario
   identities through the unchanged authoritative chain. The deterministic
   split policy — salt, ratios, group-id algorithm — is byte-unchanged; the
   planner may PREDICT a fully-defined identity's split using the exact
   production splitter (verified exact after projection), but no example is
   ever included, excluded, moved, or forced by partition, and the planner
   has no input channel for model predictions, evaluation results, or
   benchmark rankings.

3. **Unmet mandatory targets cannot register.** The expansion binding's
   `targets_satisfied` is Literal-locked true and requires every mandatory
   check to pass — an under-target descendant is structurally unregistrable,
   and advisory shortfalls (e.g. dimensions the scenario system does not yet
   support) stay visible in the artifact rather than being silently waived.

4. **Growth is honest about weight.** Repeated runs of one stable identity
   add examples only inside that identity's single partition and never add
   scenario diversity; identity counts and per-partition distinct-scenario
   counts are first-class coverage facts, so "22 test examples from 5
   identities" cannot masquerade as 22 independent test scenarios.

## Consequences

- v1 remains byte-identical forever and every later measurement can name
  exactly which corpus version (and lineage) grounded it.
- "We need more test data" has one legal shape: approve new identities
  (parameters, topologies, orientations, templates, or real lab runs), run
  the campaign, let the unchanged splitter place the groups, and register a
  descendant — with the deficit → plan → campaign → registration chain
  auditable end to end.
- Selection bias via model feedback is structurally excluded from corpus
  construction; Gate 12 failure analyses may motivate COVERAGE priorities in
  documentation, but no artifact in the expansion chain can carry model
  outputs.

## References

- `docs/architecture/gate14/corpus-expansion.md`
- ADR-0018 (datasets derive from verified runs), ADR-0029 (matched
  comparisons + thresholds), ADR-0030 (registered corpus versions).
