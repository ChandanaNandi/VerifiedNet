# Gate 19B — Family-Balanced Corpus Experiment

Gate 19B is the preregistered, one-run controlled experiment that binds the
Gate 19A family-balanced source-selection policy (`fbsel-ab6bd447a29fa253`). It is
Gate 18B with **exactly one variable changed** — the training source-selection
policy — and it tests whether balancing the training corpus removes the
majority-class collapse Gate 18B exhibited.

## Design (one independent variable)

Everything is held byte-for-byte identical to Gate 18B except the training-source
composition: the same pinned model (`Qwen/Qwen2.5-0.5B-Instruct` @ `7ae5576…`),
tokenizer, v2 feature policy (`feat-228b357dd9f256fa`), v2 prompt
(`prompt-d4ff1ee1c637ea70`), boundary objective (`objpol-7e6428964eae2db8`),
target, budget (64 examples / 2 epochs / 64 optimizer steps / 448 seq / batch 2 /
lr 2e-5 / seed 15), registered v3 corpus (`evalcorpus-8c932345efc3e6e6`), parser,
normalization, decoding, scoring, benchmark, ranking, comparison, reliability, and
the frozen success policy (`esucc-ab21b8d6e2ab7a70`). The **only** change is the
training source-selection rule: Gate 18B's natural first-64 (≈ `25 / 21 / 17 / 1`)
is replaced by the Gate 19A budget-preserving family-balanced policy
(`20 / 20 / 20 / 4`). The base and treatment SLM arms remain byte-matched on
features, prompt, tokenizer, decoding, parser, and scoring — the training corpus
composition, and therefore the weights, are the only difference. The balanced
corpus (`traincorpus-0f2973ccf0ef7b8e`, digest `traindig-c14585d6c819e945afd05ed0`)
shares 32 sources with the Gate 18B first-64 (32 added / 32 removed); every shared
source renders byte-identically, and the experiment id differs solely because of
the training-corpus identity — proven offline in the unit / contract / property /
failure tiers.

## Operational result (exp-8fd0bbe476f699fd)

Fresh balanced fine-tune, one run / one checkpoint
(`realckpt-3445b562ee6920c699170745`, digest `realdig-105967709a15b34364a3db11`,
parent `None`), loss `3.348996 → 0.000724` over 64 steps / 2 epochs. Outcome
**`improved`**, derived by the frozen `esucc-ab21b8d6e2ab7a70` policy.

- **Structured-output validity preserved: `0/230 → 230/230`.** The base model is
  `0/230` valid; the balanced treatment is `230/230` valid with zero invalid
  predictions — the boundary-aligned validity fix is unaffected by the
  composition change.
- **The majority-class collapse was substantially reduced.** The trained model
  now emits **three** of the four families (`iface_admin_shutdown` ×138,
  `bgp_prefix_withdrawal` ×46, `bgp_neighbor_removal` ×46) where Gate 18B emitted
  only two (`iface` ×184, `prefix` ×46, `neighbor` ×0). The dominant-family count
  fell from `184/230` to `138/230`. Balancing the abundant active-state families
  (`iface` 25 → 20, `neighbor` 17 → 20) let the model bind the `peer_removed`
  delta flag it previously ignored.
- **Held-out accuracy rose on the family balancing could reach.** On the 36
  eligible accepted-test examples the treatment scored `6` correct versus the
  matched base's `0` (paired `base_incorrect_trained_correct = 6`,
  `base_correct_trained_incorrect = 0`, zero regressions). Per-family test recall:
  `bgp_neighbor_removal` **0/3 → 3/3**, `bgp_prefix_withdrawal` 3/3 (held),
  `bgp_remote_as_mismatch` 0/30 (unchanged). Held-out **macro/balanced accuracy
  doubled: 0.333 → 0.667**; micro accuracy rose `3/36 → 6/36`. Overall accepted
  exact-match accuracy rose **`93/206 = 0.451` → `139/206 = 0.675`**, ranking the
  trained arm first, far above the fixed-prior and evidence-rule baselines (both
  `0.325`) and the invalid base (`0.000`).
- **Remote-AS remained `0/30` — the scarcity limit.** The frozen v3 split allocates
  only four remote-AS examples to the train partition, and the test-set firewall
  forbids importing the 30 test / 33 validation remote-AS examples. Balancing
  could not add remote-AS coverage, so its recall is unchanged. This is the
  expected boundary of the imbalance hypothesis, not a representation or binding
  failure: the four-flag oracle still scores remote-AS perfectly.

The v3 registration, prepared chain, run chain, base model, and the Gate 16B / 17B
/ 18B prior-artifact roots are fingerprinted byte-identical before and after; the
test-set firewall passed before any held-out truth was consulted; the run is
strictly offline. The Gate 12 interpretation layer records `better_on_this_corpus`.

## Cross-gate comparison (factual; prior outcomes not reinterpreted)

| Gate | independent variable | trained validity | test micro | test macro | neighbor recall | remote-AS recall | outcome |
|------|----------------------|------------------|-----------|-----------|-----------------|------------------|---------|
| 15   | first real fine-tune | `0/230` | `0/36` | 0.000 | 0/3 | 0/30 | `unchanged` |
| 16B  | v2 contract conditioning | `0/230` | `0/36` | 0.000 | 0/3 | 0/30 | `unchanged` |
| 17B  | boundary objective | `230/230` | `0/36` | 0.000 | 0/3 | 0/30 | `mixed` |
| 18B  | v2 representation | `230/230` | `3/36` | 0.333 | 0/3 | 0/30 | `improved` |
| 19B  | family-balanced corpus | `230/230` | `6/36` | **0.667** | **3/3** | 0/30 | `improved` |

Gate 18B → Gate 19B holds every control byte-identical except the training
source-selection policy; the single observable deltas — neighbor recall `0 → 3/3`,
macro `0.333 → 0.667`, prediction diversity 2 → 3 families, dominant family
`184 → 138` — are attributable to that policy alone. Prior outcomes are quoted
from their persisted results and are not re-derived.

## Interpretation

Gate 19 diagnosed the Gate 18B collapse as training-family imbalance rather than
representation or capacity. Gate 19B confirms this **for every family the frozen
split covers adequately**: with the abundant active-state families equalised, the
model stopped collapsing them onto the majority, began emitting neighbor-removal,
recovered its full held-out recall (3/3), doubled macro accuracy, and raised
overall accepted accuracy by 22 points — all from changing only which 64 sources
were trained on. The imbalance hypothesis (H1/H4) is supported; a deeper
field-to-label binding or capacity limit (H5/H7) is **not** implicated for the
covered families, because balancing alone fixed them.

The one unresolved family, remote-AS mismatch, is limited by *coverage the
firewall forbids topping up*, not by the learner: only four remote-AS train
examples exist, and the 30 test / 33 validation remote-AS examples cannot cross
into training. Balancing to four did not suffice, which is the honest boundary of
this experiment — the result must not be read as balancing "failing," since it
succeeded wherever coverage allowed. `improved` is the frozen-policy verdict for a
strict, unconfounded, zero-regression held-out gain; the scientific reading is
that class balancing resolves imbalance-driven collapse but cannot manufacture
coverage a split withholds.

## Recommendation for Gate 20

The evidence points to one variable: **increase remote-AS training coverage
without crossing the firewall** — i.e., an append-only corpus/split change (a v4
coverage campaign that generates additional remote-AS train identities, per
ADR-0031), holding the model, representation, objective, budget, and balanced
selection otherwise fixed. This directly tests whether the last unlearned family
follows the same imbalance mechanism once it has adequate train coverage. Only if
adequately-covered remote-AS still fails should Gate 20+ turn to objective/binding
or model-capacity variables. Do not change the feature policy (the oracle proves
it sufficient) or increase epochs/data blindly.

**Follow-up (Gate 20).** The Gate 20 design confirmed the deficit is one TRAIN
remote-AS group (four repeated runs) vs ~10 for the other families, and split the
work into 20A (expansion contracts, no runs), 20B (verified run campaign +
append-only v4), and 20C (one experiment). Gate 20A is complete: it derives ≥ 8
unused, approved, TRAIN-assigned remote-AS identities disjoint from all 22 frozen
groups (reproducing every frozen `group_id` from the production identity
functions), with a fail-closed leakage firewall, a bounded campaign plan, and an
append-only v4 contract — no runs. Gate 20B is also complete: the plan executed on
the real FRR lab (8/8 verified TRAIN groups, 16/16 accepted examples, 0 retries)
and registered an append-only v4 prepared chain (`eaddf66f…` → `3207fada…`) with
all 230 v3 rows byte-identical, 16 TRAIN examples appended, and every held-out
partition unchanged — lifting remote-AS TRAIN coverage from one group / four
examples to nine groups / twenty examples. Gate 20C then ran the single controlled
`16/16/16/16` experiment over that coverage and **falsified the coverage
hypothesis**: with remote-AS spanning all nine independent verified TRAIN groups,
held-out remote-AS recall stayed `0/30`, and the budget-preserving 20→16 reduction of
the abundant families cost neighbor-removal (`3/3 → 0/3`, macro `0.667 → 0.333`) —
identifying a residual field-to-label binding / model-capacity limitation for
remote-AS rather than an imbalance deficit. See
`architecture/gate20/remoteas-expansion-contracts.md`,
`architecture/gate20/remoteas-campaign.md`,
`architecture/gate20/remoteas-coverage-experiment.md`, and ADR-0038.

## Scope

The training source-selection policy is the sole independent variable. No model,
tokenizer, budget, objective, representation, prompt, target, parser,
normalization, decoding, scoring, benchmark, or success-policy change; no warm
start, second run, LoRA, RAG, agents, deployment, or publication. The generated
corpus, checkpoint, and experiment artifacts live outside the repository and are
not committed. See `architecture/gate19/family-balanced-selection.md` (Gate 19A
policy and the real-chain proof),
`architecture/gate18/discriminative-evidence-experiment.md` (Gate 18B), and
ADR-0033 / ADR-0037.
