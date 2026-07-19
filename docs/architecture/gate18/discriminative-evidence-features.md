# Gate 18A — Discriminative Evidence Representation (feature policy v2)

Gate 18A adds one additive, content-addressed **feature policy v2** that exposes
bounded, deterministic, non-leaking observable network state to the model, plus
its evidence-derivation layer, a v2 leakage firewall, and a v2 observation
render. No training, no experiment, no plan/authorization/execution/checkpoint/
evaluation/benchmark/comparison/result. The v1 policy, models, allowlist, prompt
ids, and every prior artifact are byte-unchanged.

## Why: Gate 17B solved validity, not diagnosis

Gate 17B's boundary-aligned model emitted valid JSON for all 230 examples
(0/230 → 230/230) yet scored **0/36** accepted-test accuracy: it collapsed to a
constant majority-class prediction. The Gate 18 review proved the cause is not
the learner but the **input representation**. The v1 model-visible features are
`backend`, `topology_hash`, and evidence-*presence* flags. On the registered v3
chain those are label-ambiguous: across the 206 accepted examples there are only
**6 distinct v1 payloads and all 6 map to 3–4 different fault families** — two
examples with different faults are byte-identical to the model. With
I(features; family) ≈ 0, no capacity, budget, epochs, data, or objective can
exceed the majority rate. The evidence-rule baseline's own contract says it
plainly: *"the allowlisted features intentionally do not reveal the fault
family."*

## Observable evidence vs oracle output

The authoritative baseline/onset evidence bundles
(`verifiednet.schemas.evidence`) already contain the discriminating observations
a diagnostician reads — per-neighbor BGP session state and remote-AS,
interface admin/oper state, route presence, reachability — in each record's
`normalized` map. These are the **inputs** the Gate 5 oracle consumes; the
oracle's **output** is the fault family (the label). Gate 18A exposes the
observations, never the conclusion. That is the firewall line: model-visible
diagnosis features may carry bounded observable network state and deterministic
baseline→onset deltas, but never the oracle's verdict, a label, an identity, a
split, or an artifact path.

## Feature policy v2

`FeaturePolicyV2` (`feat-228b357dd9f256fa`) locks a nine-field observable
allowlist. `DatasetFeaturesV2` exposes, in addition to the permitted context
(`backend`, `topology_hash`): raw observable state — `bgp_worst_peer_state`
(FSM enum), `interface_any_admin_down`, `interface_any_oper_down`,
`reachability_all_success`; and deterministic baseline→onset deltas —
`bgp_peer_removed`, `bgp_remote_as_changed`, `bgp_route_withdrawn`. Every field
is categorized `context` / `raw_state` / `state_delta` in `V2_FIELD_CATEGORY`;
none is a diagnostic conclusion. `derive_features_v2` is pure and deterministic
(no filesystem, network, subprocess, model, mutation, randomness, or timestamp),
reads only the collector observations, and fails closed on a wrong-phase or
missing baseline bundle. Abstention/precondition examples (no onset) legitimately
derive healthy raw state with all deltas `False`.

## Leakage firewall

`audit_features_v2` (with a raw-payload variant `audit_features_v2_payload` for
defense-in-depth) proves, fail-closed: no forbidden identity/label/split key or
verbatim evaluator-only value at any depth (the Part-4 walk, extended with the
four fault-family strings); every model-visible field is inside the locked v2
allowlist; no value is a fault-family string; no value resembles a full artifact
path; and every field is context / raw-state / delta. On the v3 chain, all 206
derived payloads pass with zero failures.

## Discrimination proof (real v3 chain)

Deriving v2 for the 206 accepted examples: **v1 has 6 unique payloads, all
family-ambiguous (206/206 examples ambiguous); v2 has 24 unique payloads with
zero cross-family collisions (0/206 ambiguous).** Each family is separated by
its own observable flag — `bgp_neighbor_removal`→`peer_removed`,
`bgp_prefix_withdrawal`→`route_withdrawn`, `bgp_remote_as_mismatch`→
`remote_as_changed`, `iface_admin_shutdown`→`admin_down` — with full coverage.
The representation makes the four faults linearly separable from observations;
whether the 0.5B model *learns* that mapping is Gate 18B's question, not a claim
made here. (Separability of the features is proven; perfect model accuracy is
not asserted.)

## Prompt observation-block change and Gate 17A boundary preservation

The v2 prompt keeps the Gate 8 instructions, candidate-family list, and response
schema byte-frozen; only the observation block changes. A single shared render,
`render_evidence_observation_block` in `datasets`, is the sole source of truth,
so the deployed inference prompt (`render_diagnosis_prompt_v2`) and the training
input (`render_training_input_v2`, using the mirrored Gate 16A constants) are
byte-identical for the same v2 features — training still never imports the
evaluation package. The prompt ends without a trailing newline, so the Gate 17A
boundary-aligned objective (input + target + EOS, input-only masking) keeps the
supervised first-target-token context byte-identical to the raw deployed prefix.
The gated real-chain proof confirms deployed==training bytes for every example
and that all 64 selected training examples fit the unchanged 384/64/448 token
envelope with no truncation.

## Backward compatibility

The v1 `FeaturePolicy` (`feat-4f792db1ef08ee5f`), `DatasetFeatures`, the v1
allowlist, the v1 prompt id, and every Gate 6–17 artifact are byte-unchanged; v2
uses new content-addressed ids; no existing corpus is overwritten and no prior
evaluation result is reinterpreted.

## No training in Gate 18A; the boundary before Gate 18B

Gate 18A ships the v2 feature policy, the derivation, the firewall, the v2
render, and the test tiers (including a gated read-only real-chain proof). It
builds no training corpus, binds no experiment, and runs no model. Gate 18B —
the preregistered one-run experiment binding the v2 features/prompt under the
otherwise-frozen Gate 17B controls, with a matched base-vs-trained evaluation —
is complete: it produced the series' first held-out accepted-test accuracy gain
(`3/36`, outcome `improved`) while confirming the model still collapses toward a
dominant family. See `architecture/gate18/discriminative-evidence-experiment.md`
and ADR-0036.
