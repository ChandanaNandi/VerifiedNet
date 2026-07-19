# Gate 6.2 (Part 4) — Feature / Label / Trace Separation

**Status:** IMPLEMENTED (Gate 6.2 Part 4 of 4). This document describes the code
in `verifiednet.datasets` that separates each exported example into model-visible
FEATURES, evaluation-only LABELS, and non-model TRACE METADATA, and persists the
result as an immutable "prepared" corpus. It implements the ADR-0018 one-way
truth flow (verified runs → projection → splitting → export → separation →
evaluation → models); no new ADR is introduced.

Part 4 does not train, evaluate, or score anything. It is the last preparation
step before evaluation, and its single job is to make label leakage into a model
structurally impossible for the enumerated cases.

## 1. Why separation is mandatory

An exported `AssignedDatasetExample` mixes three concerns that must never reach a
model together: the input a model may legitimately see, the authoritative answer
it is being tested against, and bookkeeping identity (example/group id, split,
digests). If any label or identity field is visible as an input, every downstream
metric is compromised. Part 4 splits those concerns into three distinct types and
audits the model-visible payload.

## 2. The three projections

**Features (`DatasetFeatures`) — the ONLY model-visible values.** An EXPLICIT
allowlist, not a blacklist over a full dump. v1 exposes exactly: `topology_hash`
and `backend` (permitted inference-time context) and generic evidence pointers
`baseline_evidence` (always) and `onset_evidence` (accepted only). Evidence is
referenced by a `FeatureEvidenceRef` carrying ONLY a generic role path (e.g.
`evidence/baseline.json`) — never `run_id`, `run_digest`, or any identity — so no
bookkeeping value enters the model-visible payload. The evaluator resolves the
concrete artifact by joining the role path with `trace.run_id`.

Features never contain: fault family, scenario id, ground truth, recovery,
rejection code/phase, mutation outcome, `example_id`/`group_id`, partition/split,
`split_policy_id`, any digest, any `dataset_*` field, or any label value.

**Labels (`AcceptedLabels` | `AbstentionLabels`) — a discriminated union.** An
invalid cross-kind combination cannot be constructed. Accepted labels carry the
diagnosis target (`fault_family` = source `template_id`), `scenario_id`, and the
authoritative `ground_truth_reference` + `recovery_reference`, all derived
directly from the accepted `IncidentRecord` projection — never inferred from
feature text. Abstention labels carry `expected_outcome="abstain"` plus the
authoritative machine facts `rejection_code` and `failed_phase`, and NEVER a
fault-family, healthy, negative, or "no fault" label.

**Trace metadata (`DatasetTraceMetadata`) — never model-visible.** Identity
(`example_id`, `group_id`, `run_id`, `run_digest`), `partition`, `split_policy_id`,
schema/dataset versions, `source_index_digest`, and the `incident_reference` used
for evaluation orchestration and audit.

`SeparatedDatasetExample` binds the three with strong validators: kind↔labels
agreement, accepted↔trainable partition + onset present, abstention↔abstention
partition + onset absent.

## 3. Versioned policies

`FeaturePolicy` and `LabelPolicy` are frozen and versioned; each exposes a
deterministic `policy_id` (`feat-…` / `label-…`) that is a pure hash of the
version + configuration — never of time, environment, hostname, or ordering.
The feature policy's allowlist is locked to the canonical v1 constant
(`FEATURE_ALLOWLIST_V1`); changing the feature or label contract bumps the
version and therefore the id. `separate_dataset` enforces a single feature-policy
id and a single label-policy id across the batch (fail closed on mixed policies).

## 4. Structural feature-leakage audit — guarantee and limitation

`audit_feature_payload` walks the ACTUAL serialized feature dict (not just the
Python model) at any nesting depth and, failing closed on any ERROR, detects:

- a FORBIDDEN KEY NAME anywhere (label/identity/split/bookkeeping field, or any
  `dataset_*`);
- a FORBIDDEN VALUE — an evaluator-only scalar (a label or trace identity value)
  copied verbatim into the feature payload.

`audit_separated_example` collects the evaluator-only scalars from the example's
labels + trace and runs that walk over `features.model_dump()`. `separate_example`
runs it and refuses to emit an example whose features leak.

**Guarantee:** this proves the ABSENCE of the enumerated structural leaks and of
verbatim evaluator-only scalar values in the model-visible payload — even against
a `model_construct`-bypassed or nested injection (proven by test).

**Limitation (stated, not overstated):** it does NOT and cannot prove the absence
of arbitrary SEMANTIC leakage — e.g. an evidence file whose *content* implies the
answer, or a feature that is statistically predictive. That risk is bounded by
the feature allowlist and the evidence contract, not by this audit. One intended,
documented structural property: accepted examples carry onset evidence and
abstention examples do not; this reflects the genuine task difference (diagnosis
vs abstention are distinct tasks, and abstention is eval-only) and is not a
within-task train/test leak.

## 5. The persisted "prepared" corpus

Part 4 writes a NEW derived representation into its OWN directory and never
touches the Part 3 export (`manifest.json`, split JSONL, or `dataset_digest`):

```
prepared/
  manifest.json          # PreparedManifest + self-validating prepared_digest
  features/{train,validation,test,abstention}.jsonl
  labels/{train,validation,test,abstention}.jsonl
  metadata/{train,validation,test,abstention}.jsonl
```

The three layers share one deterministic order (by `example_id`) so line *i* of
each file is the same example; the model-facing loader returns only the features
file. The `PreparedManifest` carries the source `dataset_digest`, the policy ids +
embedded policies, the counts, the per-file hashes, and a non-recursive
self-validating `prepared_digest` (same discipline as the Part 3 dataset digest).
`write_prepared` is atomic under a `.INCOMPLETE` marker removed only after
`verify_prepared` passes; `verify_prepared` re-derives the digest, re-hashes every
file, reconstructs each example across the three layers, and re-runs the feature
leakage audit on every one.

## 6. Narrow public API

`load_features` (MODEL-FACING) returns ONLY features, per partition — it never
returns labels or trace metadata, so a model consumer cannot accidentally receive
the answer. `load_prepared` (EVALUATOR-FACING) verifies first and returns the full
separated examples. Making the accidental path hard is the point of the two
separate loaders.

## 7. Determinism, immutability, reproducibility

The transform is pure (no filesystem writes, subprocess, network, randomness, or
timestamps); the writer is the only IO. Two builds from the same Part 3 export
produce byte-identical feature/label/metadata files, an identical manifest, and an
identical `prepared_digest` (proven in-memory and on two written directories). A
byte-fingerprint of both the verified run library and the Part 3 export is
identical before and after the full separation pipeline, and the pipeline
completes with subprocess and the process runner sabotaged.

## 8. Limitations and next steps

The v1 policies expose a deliberately small feature surface appropriate to the
tiny v1 corpus; richer, additional evaluation tasks would introduce new versioned
feature/label policies (new ids), not runtime conditionals. Part 4 closes Gate
6.2. The prepared corpus — features separated from evaluator-only truth, with a
structural leakage guarantee — is the stable input the evaluation gate (Gate 7)
will consume.

> **Gate 18A addendum (additive).** An additive feature policy v2
> (`feat-228b357dd9f256fa`, `datasets.evidence_features`) exposes a bounded set of
> OBSERVABLE network-state facts and deterministic baseline→onset deltas derived
> from the authoritative evidence bundles — the same inputs the Gate 5 oracle
> consumes, never its output. The Part-4 allowlist, the v1 policy id, and the
> leakage audit are unchanged; the v2 audit (`audit_features_v2`) extends the same
> walk with an allowlist lock, a fault-family-string guard, and an artifact-path
> guard, and every field is categorized observable / delta — never a diagnostic
> conclusion. See `architecture/gate18/discriminative-evidence-features.md` and
> ADR-0036.
