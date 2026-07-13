# Gate 6.1 + 6.2 (Part 2) — Rejected Examples, Deterministic Splits, Leakage Guarantees

**Status:** IMPLEMENTED (Gate 6.1/6.2 Part 2 of 4). This document describes the
code that now exists in `verifiednet.datasets` for projecting rejected runs as
abstention examples, assigning deterministic leakage-safe splits, and auditing a
built split for leakage. It implements ADR-0018 §5 (stable grouping), §6
(deterministic splitting), and §8 (rejected incidents are eval-only) — no new
ADR is introduced because no load-bearing decision changed.

Out of scope for Part 2 (deferred to Part 3): the dataset corpus writer/reader,
final `DatasetManifest` population, the non-recursive `dataset_digest`, and bulk
export. Part 2 adds no code that writes a dataset to disk.

## 1. Rejected runs become eval-only abstention examples

A precondition-rejected run carries no fault, no ground truth, and no
restoration (ADR-0018 §8). It is projected by `project_rejected_run` into a
`DatasetExample` whose `example_kind` is `abstention`. Concretely, the
projection re-checks — and fails closed with a typed
`RejectedProjectionError` if any is violated:

- incident `status == "rejected"`;
- `ground_truth is None`, `fault is None`, `restoration is None`;
- a `rejection` record is present with `failed_phase == "precondition"`;
- baseline evidence exists and is sealed; onset/recovery evidence is absent;
- the mutation transcript is empty (no mutation ever executed).

The abstention example carries only **source facts** — `rejection_code` and
`failed_phase` copied verbatim from the authoritative record — and **never a
fault-family label inferred from the scenario**. A rejected run is therefore
never a negative training label; it is an eval-only abstention target that asks
a future system "do you correctly decline when there is no fault?".

`project_accepted_run` is unchanged from Gate 6.1 apart from setting the new
`example_kind = accepted_fault` and the embedded `stable_identity`; it still
carries the ground-truth reference and onset/recovery references.
`project_verified_run` is now a thin status dispatcher over the two.

## 2. Stable identity is embedded so grouping is independently checkable

Every example now embeds a `StableScenarioIdentity` — the timestamp-free tuple
`{template_id, scenario_id, target_node, target_session, stable parameters,
topology_hash, backend}`. The `group_id` is a pure hash of exactly this identity
(`group_id_for_identity`), value-identical to the Gate 6.1 `group_id` (the
`schema_version` field is deliberately excluded from the hashed key so the value
is preserved). Because the identity travels inside the example, the leakage
audit can recompute the `group_id` from first principles and detect any tamper,
rather than trusting the stored string.

Two runs of the same scenario share one `group_id` (they differ only in
timestamped `run_id`/`run_digest`/`incident_id`); a rejected run with a distinct
scenario or distinct parameters is its own group. This is the crux of leakage
safety and is proven by test (`test_rejected_runs_of_same_scenario_share_group`,
`test_group_id_stable_across_repeated_runs`).

## 3. Deterministic, randomness-free split assignment (integer bucket space)

`SplitPolicy` expresses ratios as **integer bucket counts** out of a fixed
`SPLIT_BUCKET_COUNT = 10_000` (so `ratio = buckets / 10_000`), avoiding all
floating-point instability. It records an explicit, non-empty `salt` and an
`algorithm_version`; the three bucket counts must be strictly positive and sum
to exactly `10_000`.

Assignment is a pure function of `(group_id, policy)`:

```
bucket(group_id) = int(sha256_canonical({algorithm_version, salt, group_id}), 16)
                   % SPLIT_BUCKET_COUNT
train      if bucket <  train_buckets
validation if bucket <  train_buckets + validation_buckets
test       otherwise
```

There is no RNG, no `hash()`, no time- or environment-derived salt, and no
reliance on dict/set/filesystem ordering. `assign_group_split` is total over the
bucket space and never returns `abstention` (that is a per-example property, not
a group property). A structurally identical policy re-derives an identical
partition — a determinism property proven with Hypothesis.

Abstention examples **bypass the bucket space entirely**: `assign_example_split`
routes any `abstention`-kind example to the `abstention` partition under the
fixed `ABSTENTION_POLICY_ID = "abstention-v1"`, so no rejected run can ever land
in train/validation/test.

## 4. Assignment never mutates example identity

A split is recorded on a **separate** `AssignedDatasetExample` that wraps the
frozen `DatasetExample` and adds `{partition, split_policy_id}`. The source
example's `example_id` and `group_id` are never rewritten, and the authoritative
`IncidentRecord.dataset_group_id` / `dataset_split` fields stay `None`
(ADR-0018 §2). The wrapper's own validator enforces kind↔partition consistency
(abstention↔abstention; accepted↔a trainable split). `assign_splits` adds a
batch-level check that a single `group_id` never lands in two partitions and
fails closed (`DatasetSplitError`) if it somehow would.

## 5. Fail-closed leakage audit

`audit_leakage` takes the assigned examples and returns a `LeakageAuditResult`
that **cannot report `passed=True` while any ERROR-severity finding exists** —
enforced both by the audit and again by the model validator. Checks:

| Code | Severity | Meaning |
| --- | --- | --- |
| `group_spans_splits` | ERROR | one `group_id` in >1 partition |
| `duplicate_example_id` | ERROR | same `example_id` used twice |
| `duplicate_source_run` | ERROR | same source `run_id` projected twice |
| `group_id_mismatch` | ERROR | stored `group_id` ≠ hash of stable identity |
| `example_id_mismatch` | ERROR | stored `example_id` ≠ hash of `run_id` |
| `invalid_abstention_assignment` | ERROR | abstention example outside abstention |
| `invalid_accepted_assignment` | ERROR | accepted example in abstention / no split |
| `orientation_sibling` | INFO | groups differing only by target orientation |
| `parameter_sibling` | INFO | groups differing only by a non-orientation parameter |

The two ERROR "assignment" checks are defense-in-depth: the `AssignedDatasetExample`
model already forbids those bindings, but the audit re-derives them so a
`model_construct`-bypassed or corrupted binding is still caught (proven by
`test_invalid_abstention_assignment_defense_in_depth`). The sibling findings are
purely informational signals for a future challenge-set design and never affect
`passed`.

## 6. The engine still executes nothing and mutates nothing

Projection, splitting, and the audit are pure functions over already-verified,
already-loaded runs: no filesystem writes, no Docker, no subprocess, no model,
no randomness, no timestamps. Two failure-tier proofs guard this:

- **Source immutability** — a byte-fingerprint of every file in the run library
  is identical before and after a full discover→project→split→audit pipeline,
  and the reserved `dataset_*` fields stay `None`.
- **No execution** — with `subprocess.run/Popen/check_output` and the process
  runner sabotaged to raise, the full pipeline still completes.

## 7. Honest limitation

The v1 library is tiny (a handful of catalog cases across four families and one
topology) plus one rejected precondition case. Part 2 proves the machinery —
rejected-as-abstention projection, deterministic integer-bucket splitting, group
cohesion, and the fail-closed audit — is correct and leakage-safe. It does not
claim a statistically adequate benchmark; the same machinery scales unchanged as
the verified library grows (see `splitting-strategy.md` §6).
