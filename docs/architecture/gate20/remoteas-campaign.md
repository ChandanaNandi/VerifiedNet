# Gate 20B — Verified Remote-AS Run Campaign and Append-Only v4 Registration

Gate 20B **executes** the bounded remote-AS coverage campaign that Gate 20A
preregistered (contracts only), on the **real two-router FRR lab**, and registers
its verified output as an **append-only v4 prepared chain** descending from the
frozen v3 corpus. It closes the one-group TRAIN deficit that Gate 19B isolated for
`bgp_remote_as_mismatch` — the last family whose held-out recall the family-balanced
experiment could not move — without crossing the leakage firewall. It trains,
checkpoints, evaluates, and interprets **no** model; that is Gate 20C.

## What ran (authoritative operational result)

The Gate 20A plan (`rasplan-8453e82e95dce929`, spec `rasexp-b6512b5825f8f109`)
named 8 unused, TRAIN-assigned, frozen-disjoint remote-AS identities. Gate 20B ran
each identity twice on the live lab through the production entry point
`run_accepted_incident` — healthy convergence → preconditions → inject the planned
remote-AS mismatch → onset → Gate 5 verification → restore → recovery → accepted
`IncidentRecord` → canonical run directory → run index. Every accepted run was
projected through the **unchanged** dataset pipeline and its derived `group_id`
verified to equal its planned identity before it was allowed to become an example.

Campaign result `rascamp-2241256ebcd32c6c` (digest
`rascdig-2241256ebcd32c6c234e08e8`):

| quantity | value |
|---|---|
| planned TRAIN groups | 8 |
| verified TRAIN groups | **8 / 8** |
| accepted TRAIN examples | **16 / 16** |
| rejected runs | 0 |
| total executions | 16 (bound `max_total_executions = 18`) |
| retries used | 0 |
| coverage_ok | **true** |

The eight verified groups are exactly the eight planned identities
(`grp-4a8ed4c4c055d7b6`, `grp-2c6d02d719ecc0a3`, `grp-2346b518496a8904`,
`grp-f8fb1832f9e8fc83`, `grp-d9110ee3ccc6eb2c`, `grp-21d1aefc04357cae`,
`grp-2a7748d2eedc2b3c`, `grp-d9a4714bffb52743`) — the live lab reproduced the
pre-run derived `group_id`s exactly, confirming the Gate 20A identity derivation
against real evidence. Every container/network was torn down after each run; the
v3 chain was fingerprinted byte-identical before and after.

## Acceptance is derived, never asserted

A run becomes an accepted example only if it (a) passed deterministic Gate 5
verification, (b) projected to `ACCEPTED_FAULT`, (c) its `group_id` equals the
planned identity **and** the production splitter assigns that group to TRAIN, and
(d) it did not collide with an existing accepted output. Any failure is recorded
against a fixed failure taxonomy (`infrastructure`, `baseline_precondition`,
`fault_injection`, `evidence_collection`, `verification`, `recovery`,
`identity_mismatch`, `unexpected_group_id`, `output_collision`) and never silently
upgraded. A retry re-runs the **same** `StableScenarioIdentity`; it can only fill a
still-empty slot of an already-planned group and can never create new coverage —
`max_total_executions` bounds the whole campaign to `base + retry_allowance`.

## Append-only v4 (byte-level proof)

v4 is built by appending the 16 newly-projected TRAIN examples to the frozen v3
prepared examples and rebuilding the prepared corpus. Because `build_prepared`
serialises each example from its own trace, every v3 row renders byte-for-byte
identically; the manifest alone carries the new v4 lineage
(`dataset_version = v4-remoteas-expansion`, a fresh content-addressed
`source_index_digest` over the 16 new verified runs). The append-only diff
(`compute_append_only_diff`) compares the two prepared corpora example-by-example:

| lineage | value |
|---|---|
| v3 prepared_digest | `eaddf66f7a6690d1…` (230 rows) |
| v4 prepared_digest | `3207fada7258b7c0…` (246 rows) |
| unchanged v3 rows | **230 / 230** |
| appended accepted | **16** |
| modified prior rows | **0** |
| removed prior rows | **0** |
| prior partition changes | **0** |
| held-out (val/test) rows changed | **0** |
| frozen-group collisions | **0** |
| new independent groups | **8** |

Partition counts move only where an append-only TRAIN expansion may move them:
`train 128 → 144` (+16), while `validation 42`, `test 36`, and `abstention 24`
are unchanged — the held-out partitions are byte-identical, so no test-set truth
was imported and no held-out identity was reassigned. All seven diff checks pass.

## Gate 20C readiness (fail-closed)

Readiness `rasready-faf453da2f2dae61` conjoins nine checks, all passing: ≥ 8
verified TRAIN groups, ≥ 16 accepted TRAIN examples, campaign coverage ok,
append-only integrity, held-out byte-identity, no frozen collision, leakage-clean
firewall, v2 derivability preserved, and 16/16/16/16 feasibility. After the
campaign the remote-AS TRAIN partition holds **9 independent groups / 20 accepted
examples** (up from the single group / four repeated runs that limited Gate 19B) —
enough independent coverage for the future Gate 20C budget-preserving
`16/16/16/16` balance without crossing the firewall.

## Boundary and truth discipline

The offline layer (`experiment/remoteas_campaign.py`: `RemoteAsRunRecord`,
`RemoteAsCampaignResult`, `AppendOnlyPreparedDiff`, `V4ReadinessResult`) records
and verifies only — it imports the `datasets` prepared corpus and the Gate 20A
contracts and no live composition root, lab, or ML (the AST import-boundary guard
enforces this). Live execution, projection, and registration happen exclusively in
the gated operational harness, which may import the composition root because it is
test/harness scope. `IncidentRecord` remains authoritative; no `DatasetExample`
mutates it, and `group_id`/split are never written back into it. The v4 chain, its
new run library, and the Gate 20B result/diff/readiness/lineage artifacts live
outside the repository and are not committed.

## Cross-gate position (prior outcomes not reinterpreted)

Gate 19B improved macro/balanced held-out accuracy `0.333 → 0.667` for every
family the frozen split covered adequately, but `bgp_remote_as_mismatch` stayed
`0/30` because its TRAIN partition held one leakage group. Gate 20B removes that
specific deficit at the data layer — append-only, firewall-clean, held-out
immutable — and Gate 20C (the single controlled `16/16/16/16` training experiment)
will test whether adequately-covered remote-AS now follows the same imbalance
mechanism the other three families did. Gate 20B asserts **no** model result.

See `remoteas-expansion-contracts.md` (Gate 20A), ADR-0038 (append-only expansion
campaigns), ADR-0031 (append-only corpus lineage), ADR-0032 (deterministic split),
ADR-0018 (stable identity / leakage grouping), and
`architecture/gate19/family-balanced-experiment.md`.
