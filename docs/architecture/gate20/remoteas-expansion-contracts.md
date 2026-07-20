# Gate 20A — Remote-AS Expansion Contracts and Leakage Firewall

Gate 20A implements — CONTRACTS ONLY, no runs — the append-only remote-AS
training-coverage campaign that Gate 20B will execute and Gate 20C will
experiment on. It establishes, before any live run: a deterministic expansion
specification, the approved parameter/topology matrix, the expected stable
identities and their derived `group_id`s, their pre-execution TRAIN assignment, a
fail-closed leakage firewall proving disjointness from every frozen v3 group, an
append-only v4 registration contract, a coverage readiness preview, and a bounded
campaign plan. It generates no scenario, run, dataset, corpus, experiment, or
model artifact.

## Why: remote-AS is a one-group TRAIN deficit

Gate 19B confirmed the imbalance diagnosis for every family the frozen v3 split
covers adequately, but `bgp_remote_as_mismatch` stayed 0/30. The cause is an
independent-group deficit unique to remote-AS, not example count and not the
model:

| family | TRAIN examples / groups | held-out groups |
|---|---|---|
| iface_admin_shutdown | 44 / 11 | 1 |
| bgp_neighbor_removal | 40 / 10 | 2 |
| bgp_prefix_withdrawal | 40 / 10 | 2 |
| **bgp_remote_as_mismatch** | **4 / 1** | **21** |

The four remote-AS TRAIN examples are four repeated runs of a **single** leakage
group. `group_id` is a pure hash of `StableScenarioIdentity` (ADR-0018 §5) and the
split is the deterministic `assign_group_split` over that hash; the Gate 14B
identity-first campaign (ADR-0032) generated the most scenario diversity for
remote-AS (22 groups) and, prioritising held-out coverage, ran mostly the
val/test-bucketing identities — so TRAIN got one. The other families each have
~10 TRAIN groups. The remedy is more independent TRAIN group coverage.

## Example count versus independent group coverage

Gate 19B "balanced to 4" balanced example *count* but not *identity* coverage: the
model saw one remote-AS situation ×4 against ten-group coverage for its
competitors. Under feature policy v2 every remote-AS example maps to the same
observable payload (`remote_as_changed=True`), so added groups raise the
*frequency and independent verification* of the `remote_as_changed → remote-AS`
binding — exactly the signal that recovered neighbor-removal (17→20) — while
remaining legitimately independent (not oversampled).

## Selected expansion approach

The remote-AS identity space is the cross product of the six approved topologies
and the ten approved RAS parameter cases (Gate 14B) — **60 candidate identities**,
of which v3 registered only 22. Gate 20A selects **unused approved (topology, RAS
case) combinations** whose derived `group_id` is (a) absent from every frozen v3
group and (b) deterministically assigned to TRAIN by the frozen split policy. No
new topology, no invented parameter: only approved inputs the verified-run system
already supports. The topology axis is held fixed (no second variable).

## Deterministic identity derivation (production functions reused)

`remoteas_identity(...)` builds the `StableScenarioIdentity` field-for-field as
`datasets.projection.build_stable_identity` would from a run — from plain inputs the
caller supplies (approved catalog parameters and `topology_hash =
sha256_canonical(topology)`, identical to the incident builder). The production
`group_id_for_identity` then yields the exact `group_id` a verified run of that
`(case, topology)` pair would emit, and the production `assign_group_split`
predicts its partition. The caller (the Gate 20B harness / the gated proof) builds
the 60-candidate pool from the live catalog; the offline planning layer never
imports it. On the real v3 chain this derivation **reproduces all 22 frozen
remote-AS `group_id`s exactly**, and of the 60 candidates 39 bucket to TRAIN (v3
used one) — 38 unused TRAIN identities available, far above the target.

## Pre-execution TRAIN assignment

TRAIN membership is not forced; it is the deterministic, model-independent
consequence of each `group_id` under the frozen split policy, computed and fixed
**before** any run or model exists. The planner selects only candidates the
production splitter assigns to TRAIN — it has no parameter through which it could
move a group to a partition.

## Coverage target (≥ 8 groups / ≥ 16 examples)

Measured in **independent TRAIN groups**, not raw examples: ≥ 8 new remote-AS
TRAIN group identities (approaching the 10–11 the other families have) supplying
≥ 16 accepted TRAIN examples at 2 runs per group. Fixed before execution and
independent of any downstream model result — never "generate until accuracy
improves". The eventual Gate 20C corpus target is a budget-preserving
`16/16/16/16 = 64` equal balance (holding the 64-example / 64-step budget fixed).

## Leakage firewall

`audit_expansion_firewall` proves, fail-closed: planned groups are unique; every
planned group is absent from all frozen groups; every planned identity re-derives
to the same `group_id` (canonical, so cosmetic renames — description, display
name, comments, ordering — cannot forge a new eligible group, because `group_id`
hashes only `StableScenarioIdentity` fields); every planned identity is TRAIN; no
held-out group is reassigned; cases/topologies are in the approved sets; the group
and example targets are met by **independent groups, not repeated runs**; and the
inventory binds the spec. Any violation refuses campaign authorization.

## Bounded campaign plan

`RemoteAsCampaignPlan` binds the spec, the expected-inventory digest, the ordered
group ids, runs-per-group, the minimum accepted examples, a **bounded**
`max_total_executions` (= base runs + a preregistered retry allowance), the
fresh-output-root policy, and the offline-lab requirement. A retry of a failed run
is the *same* identity — it never creates new group coverage; `max_total_executions`
must lie in `[base, base + retry_allowance]`. Gate 20A defines the campaign and a
run-authorization contract but **authorizes and executes nothing**.

## Append-only v4 contract and readiness

`build_append_only_plan` records the guarantees Gate 20B's registration must
satisfy against the frozen v3 digest: v3 rows byte-identical, new groups TRAIN-only,
new groups disjoint from v3, held-out partitions unchanged, lineage → v3. No v4
dataset is constructed. `build_readiness_preview` distinguishes planned vs executed
vs verified coverage (in Gate 20A only planned coverage exists) and gates the
campaign on ≥ 8 planned groups, ≥ 16 planned examples, a clean firewall, and
independent-group (not repeat) coverage.

## Truth boundary

The expansion spec **predicts** expected identities and run intentions; only
verified Gate 20B runs may ever become dataset examples. `IncidentRecord` remains
authoritative; `DatasetExample` never mutates it and never writes `group_id`/split
into it. No truth originates from the spec alone. The expansion layer lives in the
offline `experiment` package, reuses the production `datasets` identity/split
functions, takes candidate identities as plain input (the live scenario catalog
and topologies are read by the caller, never by this layer), loads no model, and
imports no live composition root, lab, or ML code — the AST import-boundary guard
enforces this.

## No runs in Gate 20A; the boundary before Gate 20B

Gate 20A ships the expansion spec, the identity derivation, the expected/frozen
inventories, the firewall, the campaign plan, the run-authorization contract, the
append-only v4 plan, the readiness preview, the test tiers, and a gated read-only
real-chain planning proof. It executes no scenario, produces no evidence, exports
no dataset, registers no corpus, and trains no model. Gate 20B — the bounded
remote-AS run campaign and append-only v4 registration — remains unstarted, as
does Gate 20C (the one controlled training experiment). See ADR-0038, ADR-0018,
ADR-0031, ADR-0032, and `architecture/gate19/family-balanced-experiment.md`.
