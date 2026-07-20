# VerifiedNet — Final Research Summary

This is the terminal scientific record of the VerifiedNet research program. It
freezes the completed work exactly as it stands: it introduces no new experiment,
model, corpus, objective, feature policy, prompt, or data, and it does not
reinterpret any prior frozen outcome. Every identity and number below is
reproducible from the repository and the stored content-addressed artifacts.

## Problem

Can a small language model (SLM) diagnose the fault family behind a network
incident from **observable evidence only**, when every label and split is produced
by deterministic verification rather than by a model? VerifiedNet built the
verified-incident substrate (Gates 0–5), the read-only dataset engine (Gate 6), the
model-free evaluation/benchmark framework (Gates 7–9), the reproducible training
stack (Gate 10), and then ran a sequence of preregistered, one-variable controlled
experiments (Gates 15–20C) to find and remove the bottlenecks between a pinned
0.5B model and correct held-out diagnosis across four fault families
(`iface_admin_shutdown`, `bgp_neighbor_removal`, `bgp_prefix_withdrawal`,
`bgp_remote_as_mismatch`).

Ground truth comes exclusively from injected-fault metadata and deterministic
verifiers; no model ever participates in producing a label, a split, or an oracle
conclusion. Every experiment is preregistered (immutable spec before any training),
one-run/one-checkpoint by construction, firewalled against held-out leakage, and
scored by a single frozen success policy (`esucc-ab21b8d6e2ab7a70`) that makes a
dishonest claim unrepresentable.

## Hypotheses

- **H1 (conditioning).** The model fails because training conditioning differs from
  deployed conditioning.
- **H2 (objective).** The model fails because the training objective is misaligned
  with the inference boundary (it never learns to emit the first output token).
- **H3 (representation).** The model fails because the model-visible features are
  family-ambiguous — the information needed to discriminate families is not present.
- **H4 (imbalance).** The model fails because the training corpus is family-
  imbalanced and collapses minority families onto the majority.
- **H5 (coverage).** Remote-AS specifically fails because it has too little
  independent training coverage (one group).
- **H6 (binding/capacity).** A residual limitation is field-to-label binding and/or
  model capacity under the fixed budget, independent of coverage.

## Experiments (each: one preregistered variable)

| Gate | experiment id | sole variable | frozen outcome |
|------|---------------|---------------|----------------|
| 15   | `exp-45ee0175578f4c25` | first real fine-tune (v1 conditioning) | `unchanged` |
| 16B  | `exp-d04dcb5b19a8d6ed` | v2 contract-aligned conditioning | `unchanged` |
| 17B  | `exp-2d7024f609a37a2c` | boundary-aligned objective `objpol-7e6428964eae2db8` | `mixed` |
| 18B  | `exp-95f59672e1d784ed` | discriminative v2 representation `feat-228b357dd9f256fa` | `improved` |
| 19B  | `exp-8fd0bbe476f699fd` | family-balanced corpus `fbsel-ab6bd447a29fa253` (20/20/20/4) | `improved` |
| 20B  | campaign `rascamp-2241256ebcd32c6c` | verified remote-AS coverage → append-only v4 | (data gate) |
| 20C  | `exp-71ad7144049373a3` | group-aware coverage `gbsel-6c88212e4542dc3b` (16/16/16/16) | `improved` |

Shared frozen controls across the experiment series: pinned
`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae5576…`, tokenizer, 64-example / 2-epoch /
64-step / 448-token budget, seed 15, parser, normalization, decoding, benchmark,
ranking, paired comparison, and success policy `esucc-ab21b8d6e2ab7a70`. Evaluation
ran against registered corpus v3 (`evalcorpus-8c932345efc3e6e6`, digest
`ecdig-e72927cc7d4b6fd0fa141462`); the held-out identities are byte-identical from
Gate 18B onward.

## Results (held-out test; prior outcomes not reinterpreted)

| Gate | trained validity | test micro | test macro | neighbor recall | remote-AS recall |
|------|------------------|-----------|-----------|-----------------|------------------|
| 15   | `0/230` | `0/36` | 0.000 | 0/3 | 0/30 |
| 16B  | `0/230` | `0/36` | 0.000 | 0/3 | 0/30 |
| 17B  | `230/230` | `0/36` | 0.000 | 0/3 | 0/30 |
| 18B  | `230/230` | `3/36` | 0.333 | 0/3 | 0/30 |
| 19B  | `230/230` | `6/36` | **0.667** | **3/3** | 0/30 |
| 20C  | `230/230` | `3/36` | 0.333 | 0/3 | **0/30** |

Gate 20B (data gate, no model): the bounded remote-AS campaign ran 16/16 verified
accepted incidents on the real FRR lab across 8 new independent TRAIN groups,
registered an append-only v4 prepared chain (`eaddf66f…` → `3207fada…`) with all 230
v3 rows byte-identical and every held-out partition unchanged, lifting remote-AS
TRAIN coverage from one group / four examples to nine groups / twenty examples
(readiness `rasready-faf453da2f2dae61`, all checks pass).

## What succeeded

- **Structured-output validity was solved** by the boundary-aligned objective
  (Gate 17): `0/230 → 230/230` valid predictions, and it stayed `230/230` through
  every later experiment.
- **Observable-evidence representation was shown necessary and partially
  sufficient** (Gate 18): the v2 features removed all cross-family payload ambiguity
  (a four-flag oracle scores `36/36`), and the first held-out accuracy gain appeared
  (`3/36`).
- **Family imbalance was confirmed as the dominant remaining cause of collapse**
  (Gate 19): equalising the abundant active-state families recovered neighbor-removal
  (`0 → 3/3`) and doubled macro accuracy (`0.333 → 0.667`) for every family the
  frozen split covered adequately.
- **The verified-data machinery worked end to end** (Gate 20B): a real-lab campaign
  produced authoritative evidence and an append-only corpus expansion with proven
  byte-level immutability of all prior data.

## What failed / was falsified

- **H1 (conditioning) and H2-as-sufficient**: aligning conditioning alone (Gate 16B)
  did not move accuracy (`unchanged`); the objective (Gate 17B) fixed validity but
  not accuracy (`mixed`). Neither was sufficient on its own.
- **H5 (coverage) is falsified** (Gate 20C): with remote-AS covered by nine
  independent verified TRAIN groups and sixteen examples, held-out remote-AS recall
  **remained `0/30`** — the trained model still collapses remote-AS onto
  `iface_admin_shutdown` (30 of 36 test predictions). Adequate independent coverage
  did not teach the `bgp_remote_as_changed → bgp_remote_as_mismatch` binding.
- **Budget entanglement surfaced honestly**: holding the 64-example budget fixed,
  moving four examples out of each abundant family to fund remote-AS cost
  neighbor-removal its held-out recall (`3/3 → 0/3`), so Gate 20C is macro `0.333`
  versus Gate 19B's `0.667`. This is a cost of the equal-family budget under a small
  model, not evidence about remote-AS.

## What remains unknown

The remaining limitation is consistent with **field-to-label binding and/or model
capacity under the fixed experimental constraints** (pinned 0.5B model, 64-example
budget, frozen v2 representation and boundary objective). Whether a larger model, a
different objective/representation that makes the remote-AS delta more separable, or
a larger budget would resolve remote-AS is **not determined by this program** and is
explicitly left as future work. The evidence does not support attributing the
residual failure to imbalance or to insufficient coverage.

## Required conclusions

- Gate 17 proved boundary alignment solved the structured-output failure.
- Gate 18 proved observable-evidence representation was necessary (and removed all
  family ambiguity).
- Gate 19 proved training-family imbalance explained most of the collapse.
- Gate 20 falsified insufficient remote-AS coverage as the remaining explanation.
- The remaining limitation is consistent with field-to-label binding and/or model
  capacity under the fixed experimental constraints. (No claim is made beyond the
  evidence.)

## Reproducibility

Every experiment, corpus, checkpoint, and benchmark is content-addressed and
regenerable from the repository plus the stored inputs (the v3 chain and the
approved pinned model snapshot). Determinism is demonstrated, not asserted: the Gate
20C fine-tune was executed three times and produced the **identical** checkpoint id
`realckpt-beeca94dabe078e37cce019b` each time. The frozen policy identities are
recomputed from code at closure and match their documented values
(`feat-228b357dd9f256fa`, `prompt-d4ff1ee1c637ea70`, `objpol-7e6428964eae2db8`,
`esucc-ab21b8d6e2ab7a70`, split `split-d8d1b0d96d4552e2`, `rasexp-b6512b5825f8f109`,
`fbsel-ab6bd447a29fa253`, `gbsel-6c88212e4542dc3b`). The v3 evaluation corpus and the
Gate 15/16B/17B/18B/19B/20C experiment stores each pass their own
recompute-from-content verifiers; the append-only v4 diff proves 230/230 v3 rows
byte-identical. A researcher can rerun any gate's gated operational test with the
documented environment variables and reproduce the persisted result.

## Final status

The VerifiedNet research program is **complete**. It delivered a reproducible,
model-free verified-incident and evaluation substrate and a sequence of honest,
preregistered, one-variable experiments that located and removed the conditioning,
objective, representation, and imbalance bottlenecks, and then falsified coverage as
the explanation for the one family that remained unlearned. The scientific narrative
is closed; the remaining question (binding/capacity for `bgp_remote_as_mismatch`) is
recorded as future work, outside this project.

## Future work (not part of this project)

The following are **future research directions**, explicitly out of scope for
VerifiedNet as closed here, and each would be a new project with its own
preregistration:

- one single-variable capacity experiment (a larger pinned model) with the budget
  entanglement controlled;
- one single-variable objective/representation experiment aimed specifically at
  making the remote-AS observable delta more separable;
- decoupling the coverage and abundant-family effects by lifting the fixed
  64-example budget.

No feature-policy change is warranted (the four-flag oracle proves the representation
sufficient), and no further data campaign is warranted (coverage is falsified as the
limitation). See `../architecture/gate20/remoteas-coverage-experiment.md`,
`../architecture/gate20/remoteas-campaign.md`,
`../architecture/gate19/family-balanced-experiment.md`, and
`../architecture/decisions/` (ADR-0033, ADR-0036, ADR-0037, ADR-0038).
