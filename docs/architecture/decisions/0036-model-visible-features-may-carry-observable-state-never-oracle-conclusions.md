# 0036 — Model-visible features may carry observable state, never oracle conclusions

**Status:** Accepted

## Context

Gate 17B closed the structured-output problem (valid JSON 0/230 → 230/230) but
left accepted-test accuracy at 0/36: the boundary-aligned model collapsed to a
constant majority-class prediction. The Gate 18 review proved this is an
information ceiling, not a learning failure. The v1 model-visible feature
allowlist (`backend`, `topology_hash`, evidence-*presence* flags) is
label-ambiguous — on the registered v3 chain the 206 accepted examples yield only
6 distinct v1 payloads and every one maps to 3–4 fault families, so
I(features; family) ≈ 0. The `EvidenceRuleBaseline` contract states it directly:
the allowlisted features intentionally do not reveal the fault family. Under such
a representation no model, capacity, budget, epochs, data volume, or objective
can exceed the majority rate.

The authoritative baseline/onset evidence bundles already contain the
discriminating observations (BGP session state and remote-AS, interface
admin/oper state, route presence, reachability). These are the **inputs** the
Gate 5 oracle consumes to derive the fault family; the oracle's **output** is the
label. The prior feature policy withheld the observations (exposing only presence
flags) to avoid leakage, which removed all diagnostic signal along with any risk.

## Decision

Model-visible diagnosis features MAY expose a bounded, deterministic set of
**observable network state** and **deterministic baseline→onset deltas** derived
from the authoritative evidence bundles. They MUST NOT expose the oracle's
conclusion or anything derived from the label: no `fault_family`, ground-truth or
recovery reference, oracle result, expected outcome, rejection code/phase,
identity (`run_id`/`run_digest`/`example_id`/`group_id`/digests), split, or full
artifact path.

The firewall line is oracle-input vs oracle-output: a field is permitted iff it
is a raw observable collector readout or a deterministic delta of two such
readouts, and is never a diagnostic label. Concretely, feature policy v2
(`feat-228b357dd9f256fa`) locks a nine-field observable allowlist; every field is
categorized context / raw-state / delta (`V2_FIELD_CATEGORY`), and
`audit_features_v2` proves — fail closed — that the serialized payload contains no
forbidden key or value at any depth, no field outside the locked allowlist, no
fault-family string, and no artifact-path value. Derivation is pure
(no filesystem, network, subprocess, model, mutation, randomness, timestamp),
reads only collector observations, and fails closed on missing/malformed/
wrong-phase evidence. v1 and every prior artifact remain byte-unchanged.

## Consequences

- Exposing evidence is a firewall-sensitive, identity-bearing change: the
  observable/delta/conclusion classification and the audit are mandatory, and a
  discrimination proof on the real chain must show the features are family-
  separable with zero cross-family collisions and zero leakage before an
  experiment binds them.
- Only the prompt observation block changes; instructions, schema, parser,
  scoring, ranking, and the Gate 17A boundary are frozen. A single shared render
  keeps the deployed prompt and the training input byte-identical.
- This ADR governs the feature/prompt contract only. It authorizes no training
  run and no experiment; binding v2 in a preregistered one-run experiment
  (Gate 18B) remains subject to ADR-0033.
- Separability of the *features* is provable; model *accuracy* is not claimed by
  this ADR and remains an empirical question for Gate 18B.

## References

- `architecture/gate18/discriminative-evidence-features.md` (Gate 18A design,
  derivation, firewall, and the real-chain discrimination proof).
- `architecture/gate17/boundary-aligned-experiment.md` (Gate 17B: valid output,
  0/36 accuracy).
- `architecture/gate6/feature-label-separation.md` (the Part-4 allowlist and
  leakage audit v2 extends).
- ADR-0033 (preregistered one-run experiments), ADR-0034 (contract-aligned
  serialization), ADR-0035 (generation-boundary alignment).
