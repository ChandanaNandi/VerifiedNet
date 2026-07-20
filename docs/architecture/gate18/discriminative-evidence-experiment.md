# Gate 18B — Discriminative Evidence Representation Experiment

Gate 18B is the preregistered, one-run controlled experiment that binds the
Gate 18A discriminative **feature policy v2** (`feat-228b357dd9f256fa`) and its
v2 deployed/training prompt (`prompt-d4ff1ee1c637ea70`). It is Gate 17B with
**exactly one variable changed** — the model-visible evidence representation —
and it answers the question Gate 18A deferred: whether the 0.5B model can
*learn* the family separation that Gate 18A proved is *present* in the
observable evidence.

## Design (one independent variable)

Everything is held byte-for-byte identical to Gate 17B except the feature/prompt
representation: the same registered evaluation corpus v3
(`evalcorpus-8c932345efc3e6e6`, `ecdig-e72927cc7d4b6fd0fa141462`), the same
ordered 64 training sources, the same target (`traintgt-286e4ecdff06833e`), the
same pinned model (`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae5576…`), tokenizer, budget
(64 examples / 2 epochs / 64 optimizer steps / 448 seq / effective batch 2 /
lr 2e-5 / seed 15), the same boundary-aligned objective
(`objpol-7e6428964eae2db8`), parser, normalization, scoring, benchmark, ranking,
paired comparison, reliability classification, and the frozen success policy
`esucc-ab21b8d6e2ab7a70`. The **only** changed bound fields are the training
input template and the inference prompt: the Gate 16A/17B v1 contract-aligned
presence-flag representation (prompt `prompt-93808d932655a347`) is replaced by
the Gate 18A v2 observable-evidence representation (prompt
`prompt-d4ff1ee1c637ea70`, feature policy `feat-228b357dd9f256fa`). Switching it
yields a distinct `experiment_id` while every other control stays byte-equal —
proven offline in the unit / contract / property / failure tiers. One run, one
checkpoint, fresh from the pinned base (no warm start; lineage forbids a
parent). The base and trained arms are byte-matched on features, prompt,
tokenizer, decoding, parser, and scoring — the weights are the only difference.

The v2 training corpus (`traincorpus-e786b92cc8f40c06`, digest
`traindig-79fa100a58ccf67a719184e5`) is built from the SAME ordered 64 sources
as the Gate 16A/17B v1 corpus: identical `source_example_id` order and identical
targets, with only the input observation block differing (proven in-run against
the contract-aligned v1 corpus). The deployed inference prompt and the training
input are byte-identical for the same v2 features; training never imports the
evaluation package. Reverified token envelope (unchanged, not increased): max
input 290 ≤ 384, max combined 308 ≤ 448.

## Operational result (exp-95f59672e1d784ed)

Fresh v2 fine-tune, one run / one checkpoint
(`realckpt-79cb06cc8d955b2c33a92205`, digest `realdig-eb91ad1a0a52ebb4c35f7929`,
parent `None`), loss `3.406183 → 0.000071` over 64 steps / 2 epochs. Outcome
**`improved`**, derived by the frozen `esucc-ab21b8d6e2ab7a70` policy.

- **Structured-output validity: `0/230 → 230/230`.** The base model is `0/230`
  valid (all `prose_wrapped_json` ×230, byte-consistent with Gate 15/16B/17B).
  The v2 treatment is **`230/230` valid with zero invalid predictions** —
  prompt-compliance rate `1.000000` — reproducing Gate 17B's boundary-aligned
  validity fix under the new representation.
- **Accepted-diagnosis accuracy improved on the held-out test set, for the
  first time in the series.** On the 36 eligible accepted-test examples the
  treatment produced `3` correct versus the matched base model's `0` (the base
  is invalid on all 36); the paired counts are
  `base_incorrect_trained_correct = 3`, `base_correct_trained_incorrect = 0`,
  `both_incorrect = 33`, `trained_invalid = 0` — a strict, unconfounded,
  zero-regression gain over the matched base on ≥ 30 eligible test examples,
  which is what the frozen policy requires for `improved`.
- **The gain is marginal and driven by one family, not robust discrimination.**
  On the full corpus the trained arm reaches `93/206 = 0.451456` accepted
  exact-match accuracy — above the fixed-prior and evidence-rule baselines
  (both `67/206 = 0.325243`) and far above the base (`0/206`) — but this
  aggregate is inflated by train-partition memorization (loss → `7.1e-5`). The
  trained model emits only **two of the four** candidate families across all 230
  predictions (`iface_admin_shutdown` ×184, `bgp_prefix_withdrawal` ×46). On the
  held-out test partition it therefore scores `3/3` on `bgp_prefix_withdrawal`
  but `0/30` on `bgp_remote_as_mismatch` and `0/3` on `bgp_neighbor_removal`:
  the 3 correct predictions are exactly the prefix-withdrawal cases. The model
  still collapses toward a dominant class; it did not learn the full separation
  Gate 18A proved is present.
- **Benchmark ranking (descriptive):** trained v2 checkpoint (rank 1) >
  fixed-prior ≈ evidence-rule (ranks 2–3, `0.325243`) > base v2 model (rank 4,
  all invalid). The Gate 12 interpretation layer records
  `better_on_this_corpus` — corpus-scoped, not a generalization claim.

The v3 registration, prepared chain, run chain, base model, and the Gate 15 /
16B / 17B prior-artifact roots are fingerprinted byte-identical before and after;
the test-set firewall passed before any held-out truth was consulted; the run is
strictly offline (network monkey-patched to fail, `HF_HUB_OFFLINE=1`).

## Cross-gate comparison (factual; prior outcomes not reinterpreted)

Four preregistered one-run experiments, each changing exactly one variable from
its predecessor, evaluated on the same v3 corpus (36 eligible accepted-test
examples) under the same frozen success policy:

| Gate | independent variable | experiment | trained validity | accepted-test correct | outcome |
|------|----------------------|-----------|------------------|-----------------------|---------|
| 15   | first real fine-tune | `exp-45ee0175578f4c25` | `0/230` (invalid ×230) | `0/36` | `unchanged` |
| 16B  | v2 contract-aligned conditioning | `exp-d04dcb5b19a8d6ed` | `0/230` (`empty_output` ×230) | `0/36` | `unchanged` |
| 17B  | boundary-aligned objective | `exp-2d7024f609a37a2c` | `230/230` valid | `0/36` | `mixed` |
| 18B  | v2 discriminative representation | `exp-95f59672e1d784ed` | `230/230` valid | `3/36` | `improved` |

Gate 17B fixed structured-output validity but left accepted-test accuracy at
`0/36` (`mixed`). Gate 18B holds Gate 17B's objective and every other control
byte-identical and changes only v1 → v2 representation; the single observable
delta is the `3/36` held-out accepted-test gain, which is precisely what moves
the frozen outcome from `mixed` (17B) to `improved` (18B). The prior outcomes
are quoted verbatim from their persisted results and are not re-derived here.

## Interpretation

Gate 18A proved the evidence is *separable* (v2: 24 payloads, 0 cross-family
collisions vs v1: 6 payloads, 206/206 ambiguous). Gate 18B shows the 0.5B model
*partially* exploits that separability: with valid structured output preserved,
the discriminative representation produced the experiment series' first held-out
accepted-test accuracy gain (`3/36`) over the matched base and over the Gate 17B
v1 treatment. But the gain is small and family-local — the model emits only two
families and still collapses toward a dominant class — so it is not evidence
that the learner robustly acquired the full four-way mapping. `improved` is the
honest frozen-policy verdict for a strict, unconfounded, zero-regression
held-out gain; it is deliberately *not* a claim of robust diagnosis. Closing
the collapse (decoding, objective shaping beyond the boundary, data balance, or
capacity) is a distinct axis for a future gate. No generalization claim is made.

**Follow-up (Gate 19).** The Gate 19 diagnosis (read-only, on this result)
localised the collapse to training-family imbalance: the seven observable v2
fields give one payload per family with zero collisions and a four-flag oracle
scores 36/36 on the test set, yet the natural first-64 corpus is `25 / 21 / 17 /
1` and even the 17 neighbor-removal training examples are misclassified. The
model discriminates only on the coarse peer-state field and collapses the three
active-state families onto the majority. Gate 19A added a content-addressed
family-balanced source-selection policy (budget-preserving 20/20/20/4), and Gate
19B ran the one experiment binding it: balancing the abundant families recovered
neighbor-removal held-out recall (`0 → 3/3`), doubled macro accuracy
(`0.333 → 0.667`), raised overall accepted accuracy (`0.451 → 0.675`), and reduced
the dominant-family collapse — confirming the imbalance hypothesis wherever the
frozen split covers a family. This gate's `improved` outcome and `3/36` figure are
unchanged. See `architecture/gate19/family-balanced-selection.md`,
`architecture/gate19/family-balanced-experiment.md`, and ADR-0037.

## Scope

Representation is the sole independent variable. No model, tokenizer, budget,
objective, corpus source set, target, parser, normalization, scoring, ranking,
comparison, reliability-classification, decoding, benchmark, or success-policy
change; no warm start, second run, larger budget, LoRA, RAG, agents, deployment,
or publication. The generated corpus, checkpoint, and experiment artifacts live
outside the repository and are not committed. See
`architecture/gate18/discriminative-evidence-features.md` (Gate 18A v2 features
and the separability proof), `architecture/gate17/boundary-aligned-experiment.md`
(Gate 17B), and ADR-0033 / ADR-0035 / ADR-0036.
