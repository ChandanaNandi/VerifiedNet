# 0038 — Data-expansion campaigns preregister expected identities, assignments, coverage bounds, and leakage disjointness before any run; retries never create new coverage

**Status:** Accepted

## Context

Gate 19B confirmed that fixing training-family imbalance recovers the affected
families, but `bgp_remote_as_mismatch` stayed 0/30. The cause is a training-side
independent-group deficit: the frozen v3 TRAIN partition holds one remote-AS
leakage group (four repeated runs) versus ~10 for every other family, because the
deterministic split (`assign_group_split`) bucketed remote-AS's 22 registered
identities overwhelmingly into validation/test and the identity-first campaign ran
those held-out identities. Closing the deficit requires generating new, verified,
independently-grouped remote-AS TRAIN identities — a data-expansion campaign that
touches the authoritative corpus and must never contaminate the frozen held-out
evaluation set.

Until now, coverage expansion (Gate 14/14B) fixed a candidate matrix and predicted
splits with the production splitter, but there was no standing contract that a
targeted expansion campaign must preregister its exact expected identities, their
partition assignment, its coverage bounds, and its leakage disjointness *before*
any run executes — nor an explicit rule about what a run retry may and may not
change.

## Decision

A targeted data-expansion campaign is a **preregistered, content-addressed
contract fixed before any run executes**. It must bind: the approved identity
space (templates, cases, topologies — no invented inputs); the deterministic
expected `StableScenarioIdentity` and derived `group_id` of every planned
identity, computed with the **production** identity and split functions
(`group_id_for_identity`, `assign_group_split`), never private logic; the
pre-execution partition assignment of every planned group (a deterministic,
model-independent consequence of its `group_id`, not a forced choice); the
coverage target expressed in **independent group identities** (not raw examples or
repeated runs); and a **bounded** execution count.

Before the campaign may be authorized, a fail-closed firewall must prove: planned
groups are unique; every planned group is disjoint from every frozen group;
canonical identity equality (not human-readable naming) governs disjointness, so
cosmetic metadata cannot forge a new eligible group; no held-out identity is
reassigned; and the target is met by independent groups. Registration of the
resulting data must be **append-only**: existing rows, identities, digests, and
partitions are byte-unchanged, new groups enter their predetermined partition, and
lineage points to the parent corpus (ADR-0031). A **run retry of a failed
execution is the same identity and never creates new group coverage**; the bounded
`max_total_executions` allows a preregistered retry allowance only.

Truth continues to flow one way: the expansion spec *predicts* expected identities
and run intentions; only verified runs may become `DatasetExample`s.
`IncidentRecord` stays authoritative and is never mutated by projection. Split
eligibility is decided before any model training or evaluation.

## Consequences

- A campaign's coverage claim is auditable before it runs: the expected group ids,
  their partition assignment, and their disjointness from the frozen set are
  content-addressed artifacts, verifiable against the frozen corpus digest.
- Coverage is measured in independent identities: balancing raw example counts
  without adding independent groups (as Gate 19B did for remote-AS, bounded by one
  train group) is recognized as insufficient, and oversampling/duplication is
  prohibited (consistent with ADR-0037).
- The held-out evaluation series stays comparable: append-only registration keeps
  every prior identity/digest/partition byte-identical, so an expanded-train
  experiment evaluates on the same held-out identities.
- This ADR governs the expansion *contract*. It authorizes no run and registers no
  corpus; executing the bounded campaign (Gate 20B) and running the one controlled
  training experiment (Gate 20C) remain separate, each subject to their existing
  contracts (Gate 4/5 verification, ADR-0031 registration, ADR-0033 experiments).

## References

- `architecture/gate20/remoteas-expansion-contracts.md` (Gate 20A spec, identity
  derivation, firewall, campaign plan, append-only and readiness contracts, and
  the real-chain planning proof).
- `architecture/gate19/family-balanced-experiment.md` (Gate 19B: the remote-AS
  one-group deficit) and the Gate 20 design.
- ADR-0018 (stable scenario identity and leakage grouping), ADR-0031 (append-only
  corpus expansion; coverage never overrides splitting), ADR-0032 (identity-first
  held-out coverage), ADR-0037 (source-selection is first-class; no duplication).
