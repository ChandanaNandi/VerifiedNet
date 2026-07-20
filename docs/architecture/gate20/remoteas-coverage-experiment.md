# Gate 20C — Remote-AS Coverage Controlled Experiment

Gate 20C is the single preregistered, one-run controlled experiment that tests
whether independently verified remote-AS TRAIN group coverage — the coverage Gate
20B added to the append-only v4 chain — resolves the last unlearned fault family,
`bgp_remote_as_mismatch`. It is Gate 19B with **exactly one variable changed**: the
training source-selection policy. The result **falsifies the coverage hypothesis**:
held-out remote-AS recall stayed `0/30`.

## Design (one independent variable)

Everything is held byte-for-byte identical to Gate 19B except the training-source
composition, which is the deterministic consequence of the single changed policy:

| | Gate 19B (control) | Gate 20C (treatment) |
|---|---|---|
| selection policy | family-balanced `fbsel-…` | group-aware `gbsel-6c88212e4542dc3b` |
| composition | 20 / 20 / 20 / 4 | **16 / 16 / 16 / 16** |
| remote-AS examples | 4 | **16** |
| remote-AS independent TRAIN groups | 1 | **9** |
| source of remote-AS coverage | frozen v3 | append-only v4 (Gate 20B) |
| training corpus | `traincorpus-0f2973ccf0ef7b8e` | `traincorpus-2ff70e333f22fdee` |

The pinned base model (`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae5576…`), tokenizer, v2
feature policy (`feat-228b357dd9f256fa`), v2 prompt (`prompt-d4ff1ee1c637ea70`),
boundary objective (`objpol-7e6428964eae2db8`), budget (64 examples / 2 epochs / 64
steps / 448 seq / batch 2 / lr 2e-5 / seed 15), target, parser, normalization,
decoding, scoring, benchmark, ranking, comparison, reliability, and the frozen
success policy (`esucc-ab21b8d6e2ab7a70`) are unchanged. The base and treatment SLM
arms are byte-matched on features, prompt, tokenizer, decoding, parser, and scoring;
the training corpus composition — and therefore the weights — is the only
difference. The two corpora share 32 sources (32 added / 32 removed); every shared
source renders byte-identically, and the experiment id differs solely because of the
training-corpus identity. Evaluation used the **byte-identical v3 held-out
identities** from Gates 18B/19B; the v3, v4, Gate 20B, and Gate 19B artifacts were
fingerprinted immutable before and after.

The group-aware policy fills each family's quota by drawing one example per
independent `group_id` in rotation, with a fail-closed minimum-independent-group
floor (remote-AS ≥ 8). On the real v4 chain it selected 16 remote-AS examples
spanning all **9** independent TRAIN groups (the one legacy v3 group plus the eight
Gate 20B groups), no group contributing more than two.

## Operational result (exp-71ad7144049373a3)

One fresh fine-tune from the pinned base, one checkpoint
(`realckpt-beeca94dabe078e37cce019b`, parent `None`). Outcome **`improved`**, derived
by the frozen `esucc-ab21b8d6e2ab7a70` policy — the paired comparison is against the
matched base arm, which is `0/230` valid, so any valid held-out gain with zero
regressions scores `improved`.

- **Structured-output validity preserved: `0/230 → 230/230`.**
- **Remote-AS held-out recall remained `0/30` — the coverage hypothesis is
  falsified.** Despite nine independent remote-AS TRAIN groups and sixteen verified
  examples, the trained model still does not emit `bgp_remote_as_mismatch` for the
  held-out remote-AS examples: on the 36 eligible test examples it predicted
  `iface_admin_shutdown` 30 times, `bgp_prefix_withdrawal` 3, and
  `bgp_remote_as_mismatch` 3 — collapsing the 30 held-out remote-AS cases onto the
  majority family rather than binding the `bgp_remote_as_changed` evidence delta.
- **Neighbor-removal regressed `3/3 → 0/3` versus Gate 19B.** The budget-preserving
  reduction of the abundant families from 20 to 16 examples (to make room for the 16
  remote-AS examples under the fixed 64-example budget) cost neighbor-removal the
  held-out binding it had learned in Gate 19B; the trained model emitted three of the
  four families but not `bgp_neighbor_removal`.
- **Held-out accuracy fell relative to Gate 19B.** Test micro accuracy
  `6/36 → 3/36`; macro/balanced accuracy `0.667 → 0.333`
  (per-family test recall: neighbor `0/3`, prefix `3/3`, remote-AS `0/30`). The
  fixed-prior and evidence-rule baselines score `30/36` on test (they answer
  remote-AS by construction); the trained arm ranks below them and above the invalid
  base.

The run is strictly offline; the network was stubbed; the test-set firewall passed
before any held-out truth was consulted; the v3/v4/Gate 20B/Gate 19B sources are
fingerprinted byte-identical before and after. The Gate 12 interpretation layer
records `better_on_this_corpus` (its verdict for the trained-vs-base pairing).

## Cross-gate comparison (prior outcomes not reinterpreted)

| Gate | independent variable | remote-AS train (ex / groups) | trained validity | test micro | test macro | neighbor recall | remote-AS recall | frozen outcome |
|------|----------------------|-------------------------------|------------------|-----------|-----------|-----------------|------------------|----------------|
| 18B  | v2 representation | 4 / 1 | `230/230` | `3/36` | 0.333 | 0/3 | 0/30 | `improved` |
| 19B  | family-balanced corpus | 4 / 1 | `230/230` | `6/36` | 0.667 | 3/3 | 0/30 | `improved` |
| 20C  | group-aware coverage | **16 / 9** | `230/230` | `3/36` | 0.333 | 0/3 | **0/30** | `improved` |

The central question — *does raising remote-AS independent TRAIN coverage from one
group to nine produce non-zero recall on the same 30 held-out remote-AS examples?* —
is answered **no**. Prior outcomes are quoted from their persisted results and are
not re-derived.

## Interpretation

The imbalance/coverage hypothesis that carried Gates 18B–19B does **not** extend to
`bgp_remote_as_mismatch`. Gate 19B showed that equalising the abundant active-state
families recovered every family the frozen split covered adequately, and reasoned
that remote-AS stayed `0/30` only because it had one TRAIN group. Gate 20C removed
exactly that deficit — nine independent verified groups, sixteen examples, firewall-
clean, held-out immutable — and the model still collapsed remote-AS onto the
majority. Coverage was therefore **necessary-to-test but not sufficient**: the
remaining barrier is specific to binding the `bgp_remote_as_changed` observable to
its label in a 0.5B model under this budget, not a shortage of independent training
scenarios.

The experiment also surfaced a budget-sensitivity finding that must not be
mis-attributed: holding the 64-example budget fixed, moving four examples out of each
abundant family (20 → 16) to fund remote-AS lost neighbor-removal's held-out recall
(`3/3 → 0/3`). This is an honest cost of the equal-family budget under a small model,
not evidence about remote-AS. It means the two effects are entangled at this budget:
one cannot both fully cover remote-AS and hold the other families at their Gate 19B
strength within 64 examples.

## Recommendation for final closure

Coverage is falsified as the remaining explanation for remote-AS. The closure gate
should record a **residual field-to-label binding / model-capacity limitation for
`bgp_remote_as_mismatch`**, distinct from the imbalance mechanism that governed the
other three families. Any follow-up should be a **separately approved** single-
variable experiment on one capacity/binding lever (for example, a larger pinned
model, or an objective/representation change that makes the remote-AS delta more
separable) with the budget entanglement controlled — not additional data, not more
epochs, and not a feature-policy change (the four-flag oracle proves the
representation sufficient). Gate 20C does **not** begin that follow-up.

## Scope

The training source-selection policy is the sole independent variable. No model,
tokenizer, budget, objective, representation, prompt, target, parser, normalization,
decoding, scoring, benchmark, or success-policy change; no warm start, second run,
LoRA, RAG, agents, deployment, publication, or additional data campaign. The
generated corpus, checkpoint, and experiment artifacts live outside the repository
and are not committed. See `remoteas-campaign.md` (Gate 20B),
`remoteas-expansion-contracts.md` (Gate 20A),
`architecture/gate19/family-balanced-experiment.md` (Gate 19B), and ADR-0033 /
ADR-0037 / ADR-0038.
