# Gate 6.0 — Leakage Analysis

**Status:** PLANNING ONLY. This is the most important Gate 6 design section: a
leakage that survives here is inherited by every later gate (evaluation,
baselines, SLM, RAG, GraphRAG). The rule is conservative by default — **when in
doubt, group together (never split apart).**

## 0. The stable grouping key (the crux)

Because `incident_id` and `run_digest` embed run-local timestamps (see
`dataset-engine-plan.md` §1), they are NOT stable across two runs of the same
scenario. The leakage-grouping key MUST be a pure function of the STABLE
scenario+lab identity only:

```
group_key = canonical{
    template_id,               # fault family
    scenario_id,               # catalog scenario identity
    target_node,               # orientation
    target_session,
    parameters_stable,         # scenario parameters minus volatile fields
    topology_hash,             # the lab topology
    backend,                   # "frr" today
}
group_id = "grp-" + sha256_canonical(group_key)[:16]
```

`parameters_stable` excludes anything run-local (there are none in the current
scenario parameters — they are wrong_asn / prefix / target_node, all stable).
Every run sharing a `group_id` is the "same logical incident" for leakage
purposes and MUST NOT be separated across splits.

## 1. Enumerated leakage sources and prevention

| # | Leakage source | Prevention |
|---|---|---|
| 1 | **Same run** in two splits | A run belongs to exactly one split; dedup by `run_id`; reject repeated `run_digest`. |
| 2 | **Same scenario, different run** (repeat of a catalog case) across splits | Group by `group_id` (stable scenario+topology identity), NOT `incident_id`/`run_digest`; the whole group takes one split. |
| 3 | **Same template / family** across splits | Family is a `group_id` component; a family-holdout *challenge* partition (whole family withheld) is an explicit, separately-tagged split — never accidental. With only 4 families, ordinary train/dev/test split at group granularity; family holdout is a deliberate benchmark axis, not the default. |
| 4 | **Same topology** across splits | `topology_hash` is a `group_id` component. Only one topology exists today; when more exist, topology becomes a primary holdout dimension so a model cannot memorize a topology's answers. |
| 5 | **Same scenario orientation** (`router_a` vs `router_b` of one case) | These are DIFFERENT groups (different `target_node`) but near-duplicates; they are tagged as **orientation siblings** so a strict benchmark can co-locate them in one split, preventing "seen the mirror image" leakage. |
| 6 | **Same parameter variation** (e.g. `ras-ref` vs `ras-alt`) | Different `group_id`s (different `parameters_stable`) but same family+topology; tagged as **parameter siblings**; a strict split may group siblings. Default: separate groups, sibling-tagged. |
| 7 | **Same evidence ids / transcript** reused across examples | Evidence ids are content-derived and per-run; they are provenance, never features. Datasets reference them, never key splits on them. |
| 8 | **Same configuration hashes** (identical healthy baseline `config.sha256`) recurring | Expected (one healthy baseline); it is NOT label leakage but a memorizable constant. Prevention is a *feature-hygiene* requirement handed to Gate 7 eval (do not expose raw baseline hashes as trivially discriminative features); the dataset records them as provenance, flagged non-discriminative. |
| 9 | **Same timestamps** | All timestamps are volatile provenance; excluded from `group_id`, from `example_id` content, and from any feature. |
| 10 | **Same commit / image / environment** across all examples | In v1 every run shares one `git_rev`, one `image_manifest_digest`, one arch — an environment CONFOUND, not per-example leakage. Recorded as provenance so evaluation can control for it; flagged as a limitation to diversify later. |
| 11 | **Same run digest** (identical run) as two examples | Duplicate detection: reject a second example with an already-seen `run_digest`. |
| 12 | **Artifact reuse** (two examples pointing at one run dir) | Each example references exactly one `run_id`; the run→example map is injective; verified at build. |

## 2. Post-split invariant (must hold, machine-checked)

After assignment, the engine asserts: **no `group_id` appears in more than one
split.** If any group spans splits, the build FAILS loudly (no silent fix). This
single invariant is the operational definition of "leakage-safe" for the
default train/dev/test partition.

## 3. Sibling tagging (stricter partitions)

Each group carries deterministic sibling tags computed from its stable key:

- `orientation_sibling_of` — same (template, scenario minus target_node,
  topology).
- `parameter_sibling_of` — same (template, topology), differing only in stable
  parameters.

These tags let a *strict benchmark* or *challenge set* elevate the grouping
granularity (e.g. "hold out an entire family and all its orientation/parameter
siblings") without re-deriving anything. They never relax the default
invariant.

## 4. What leakage prevention deliberately does NOT do

It does not deduplicate by semantic similarity (no model), does not drop runs to
"balance" classes (that would bias the verified library silently), and does not
infer group membership — grouping is a pure hash of immutable identity. Any run
that cannot be grouped deterministically is a build error, never a guess.

## 5. Honest limitation

With 9 catalog cases, 4 families, and 1 topology, a leakage-safe train/dev/test
split yields very few groups per split — too few for statistical training
claims. Gate 6 v1 is a **methodology proof** (the pipeline is correct and
leakage-safe), not a large corpus. Scaling comes from more topologies/families
in later work, at which point the topology and family holdout dimensions
(sources #3/#4) become the primary generalization tests. This is documented, not
hidden.
