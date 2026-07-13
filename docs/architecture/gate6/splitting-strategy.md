# Gate 6.0 — Splitting Strategy

**Status:** train/dev/test assignment is now IMPLEMENTED in
`verifiednet.datasets.splitting` (Gate 6.2 Part 2); see
`rejected-examples-and-leakage-safe-splits.md`. The reserved
benchmark/hidden-benchmark/challenge partitions remain PLANNING ONLY. All splits
are deterministic, reproducible, and randomness-free, driven entirely by
immutable content.

## 1. Determinism: split assignment is a pure function

Split assignment is a pure function of the leakage `group_id` and the build
config — **no randomness, ever**. The implemented form uses an integer bucket
space (`SPLIT_BUCKET_COUNT = 10_000`) so ratios are exact with no floating-point
instability:

```
bucket(group_id) = int(sha256_canonical({algorithm_version, split_salt, group_id}), 16)
                   % SPLIT_BUCKET_COUNT
train      if bucket <  train_buckets
validation if bucket <  train_buckets + validation_buckets
test       otherwise
```

- `split_salt` is a fixed, non-empty string recorded on the `SplitPolicy` (and,
  in Part 3, the dataset manifest). The same `split_salt` + same bucket counts +
  same `group_id` → the same split, forever.
- Assignment is per GROUP, then every run in the group inherits the group's
  split. Two runs sharing a `group_id` therefore land in the same split by
  construction (the leakage invariant holds by design, and is re-asserted after
  assignment).
- Forbidden mechanisms: `random`/`Math.random`, time-based seeds, `set`/`dict`
  iteration order, filesystem order, or any host-dependent ordering. Ordering
  everywhere is by explicit sort keys (`group_id`, then `run_id`).

## 2. What cannot cross splits

Any two runs sharing a `group_id`. After assignment the engine asserts **no
`group_id` spans splits** (`leakage-analysis.md` §2); a violation fails the
build loudly.

## 3. Reproducibility guarantee

Given the same verified run corpus (pinned by `source_index_digest`), the same
`split_salt`, and the same `split_ratios`, two independent builds produce a
**byte-identical `dataset_digest`**. A determinism check (build twice, compare
digests) is a required Gate 6.1 acceptance test. Volatile provenance
(build timestamp) is excluded from the hashed content, so it does not perturb
the digest.

## 4. The partitions

- **train / dev / test** — the default three-way split at `group_id`
  granularity via the bucket function above. Ratios are config (a sensible
  default such as 70/15/15 is a build parameter, not hard-coded truth).
- **abstention (IMPLEMENTED)** — the eval-only home of rejected
  (no-fault-label) runs. Abstention examples bypass the bucket space entirely
  and are assigned under a fixed `abstention-v1` policy id, so no rejected run
  is ever a train/dev/test member (ADR-0018 §8).
- **future benchmark** — a named, stable subset drawn deterministically from
  `test` (or a dedicated pool) for cross-gate comparison; frozen by its own
  digest so Gates 7/8/12 measure against an unchanging target.
- **future hidden benchmark** — generated identically but with LABELS WITHHELD
  from training/inference consumers (the features-only view in
  `dataset-schema.md` §2 is emitted; the labels file is access-restricted to
  the evaluation harness). Its existence and digest are recorded; its labels
  are not distributed to model-training consumers.
- **future challenge set** — a deliberately HARDER partition built by elevating
  the grouping granularity: whole-family holdout, whole-topology holdout, or
  orientation/parameter-sibling co-location (`leakage-analysis.md` §3). It tests
  generalization to an unseen family/topology and is defined by explicit group
  tags, still fully deterministic.

All partitions are pure functions of immutable content + recorded config;
none uses randomness; each is pinned by a digest.

## 5. Metadata that drives splitting

Only immutable, model-free metadata drives splitting: `template_id`,
`scenario_id`, `target_node`, stable `parameters`, `topology_hash`, `backend`
(all via `group_id`), plus the explicit sibling tags. No timestamp, no
`run_digest`, no `incident_id`, no evidence content, and no model output ever
influences a split.

## 6. Honest limitation for v1

The current library (9 cases, 4 families, 1 topology) yields a small number of
groups; a 70/15/15 split leaves very few groups per split, and a family/topology
holdout challenge set is only meaningful once more families/topologies exist.
Gate 6 v1 therefore proves the SPLITTING MACHINERY is correct, deterministic,
and leakage-safe — it does not claim a statistically adequate benchmark. The
same machinery scales unchanged as the verified library grows.
