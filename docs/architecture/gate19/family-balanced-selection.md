# Gate 19A — Deterministic Family-Balanced Training Selection

Gate 19A adds one additive, content-addressed **training source-selection
policy** — `FamilyBalancedSelectionPolicy` (`fbsel-…`) — plus its selection
result, its integration into the Gate 18B v2 corpus builder, a deterministic
corpus comparison, and the test tiers. It is implementation and verification
only: no training corpus is fine-tuned, no experiment is preregistered, and no
checkpoint, evaluation, benchmark, or interpretation is produced. The v2 feature
policy, v2 prompt, boundary objective, target, parser, scoring, benchmark, and
success policy are byte-unchanged, and the Gate 18B corpus is unaffected.

## Why: Gate 18B collapsed on imbalance, not representation

The Gate 19 diagnosis (read-only, on the persisted Gate 18B result) showed the
remaining bottleneck is neither the representation nor held-out generalization:

- **Representation is provably sufficient.** Restricted to the seven observable
  v2 evidence fields, each fault family maps to exactly ONE payload — four
  payloads, zero cross-family collisions — and a deterministic four-flag oracle
  scores **206/206 accepted and 36/36 test**. All 36 test payloads were seen in
  training. The problem is not the features and not unseen inputs.
- **The model collapsed under imbalance.** Gate 18B's natural first-64 corpus is
  `iface_admin_shutdown 25 / bgp_prefix_withdrawal 21 / bgp_neighbor_removal 17 /
  bgp_remote_as_mismatch 1`. The trained model discriminated only on the coarse
  `bgp_worst_peer_state` field (`established` → prefix; `active` → the majority
  active-state family, iface), collapsing the three active-state families onto
  the majority. It failed even on training data (17 neighbor-removal training
  examples all misclassified) and emitted only two of four families.

The single most justified next variable is therefore the **training
source-selection policy**: does removing the majority-class pressure let the
model bind the discriminative delta flags? Gate 19A builds the balanced-corpus
control; Gate 19B (unstarted) runs the one experiment.

## The sole independent variable

Gate 18B selected the natural first-64 accepted train sources
(`cap_training_corpus`, canonical example-id order). Gate 19A introduces a
family-balanced, budget-preserving selection over the FROZEN train partition.
For any selected source the downstream v2 feature derivation, prompt render,
target, objective, tokenizer, and token budget are byte-identical to Gate 18B —
only the source composition changes.

## Budget-preserving 20/20/20/4 composition

The default policy targets a per-family allocation, in the frozen
`TRAINING_CANDIDATE_FAMILIES` order, of `bgp_neighbor_removal 20`,
`bgp_prefix_withdrawal 20`, `bgp_remote_as_mismatch 4`, `iface_admin_shutdown 20`
— total **64**. This preserves the 64-example budget and the 64-optimizer-step
execution budget while equalising the abundant families and including every
available remote-AS example. Remote-AS is **split-scarce**: the frozen v3 split
allocates almost all remote-AS examples to validation/test (train has only 4),
and the test-set firewall forbids importing them, so its quota is 4 by
necessity, not choice. The abundant active-state families (iface 25 → 20 and
neighbor 17 → 20) are equalised, which is exactly the collapse Gate 18B
exhibited; Gate 19B can therefore cleanly test the iface-vs-neighbor collapse and
provide partial evidence for remote-AS.

The corpus size stays at 64 by design: a strictly-equal 4×4 = 16 corpus is
rejected because it would silently change corpus size and optimizer-step count —
a second variable. Oversampling, duplication, and synthesis are forbidden.

## Deterministic selection

Selection reads only the train partition and only accepted labels. Within each
family, sources are ordered canonically by `(group_id, example_id)`; the first
quota-many are taken (`deterministic_per_family_prefix`); no missing quota is
ever redistributed (`exact_quota_no_redistribution`). The selected sources are
then interleaved **round-robin** in family order (a family is skipped once its
quota is exhausted, so the four remote-AS examples occupy the first four
columns), giving one deterministic order that distributes the composition rather
than grouping it. There is no randomness, no runtime seed, no filesystem-
enumeration dependence, and no timestamp. `policy_id` and `selection_digest` are
content-addressed; changing any quota, family order, or selected source changes
the identity.

The corpus itself is ALWAYS ordered canonically by `source_example_id` (the
content-addressing invariant, identical methodology to every prior gate); the
round-robin order lives in the selection result as auditable provenance.

## Fail-closed behaviour

Selection fails closed when a required family is absent, availability is below a
declared quota, a source lacks an accepted label, a family is unsupported, or a
duplicate identity appears; the policy itself refuses a quota sum that does not
equal the target total or an unsupported family. The corpus builder additionally
refuses a selection built for a different prepared corpus (digest mismatch) or a
selected id that is not an accepted train source. A different availability
profile requires a new policy identity — the policy never silently adapts.

## Test firewall

The selector imports no evaluation package, loads no model, and uses no
network/subprocess/filesystem-enumeration; it never inspects validation/test
labels, model predictions, benchmark rankings, confusion matrices, or Gate 18B
error tables — only accepted labels in the train partition. The training package
retains its AST-enforced no-evaluation-import boundary. Read-only Gate 19
diagnostics used labels for post-hoc analysis only, never in any selection code
path.

## Corpus comparison with Gate 18B

`compare_training_corpora` produces a deterministic report proving both corpora
hold 64 unique sources with no duplicates, records each family distribution, the
source intersection / added / removed sets, ordering differences, and — for
every shared source — byte-identical rendered inputs and targets, plus equal v2
feature-policy, input-template, and target-template ids. The composition policy
is the sole independent variable; the changed selected identities and order are
consequences of that policy, not separate knobs.

## Read-only real-chain proof

A gated, deselected-by-default integration proof
(`VERIFIEDNET_RUN_GATE19A=1` + a v3 root; the tokenizer budget also needs a local
Qwen snapshot) verifies the source chain, reports train-family availability,
selects exactly 64 with the expected 20/20/20/4 counts and deterministic
round-robin lead, builds the balanced v2 corpus, proves deployed==training bytes
and shared-source rendering equality with the Gate 18B first-64 corpus, proves
the unchanged 384/64/448 token budget, and fingerprints the source and prior
artifacts byte-identical before and after. It creates no execution, checkpoint,
evaluation, benchmark, or experiment artifact and runs no fine-tune.

## No fine-tune in Gate 19A; the boundary before Gate 19B

Gate 19A ships the selection policy, the selection result, the corpus-builder
integration, the comparison, and the test tiers. It builds no fine-tuned
checkpoint and binds no experiment. Gate 19B — the preregistered one-run
experiment that binds the balanced corpus under the otherwise-frozen Gate 18B
controls, with a matched base-vs-trained evaluation — remains unstarted. Its
result will test whether balancing removes the collapse (implicating imbalance /
optimization) or not (implicating field-to-label binding or capacity for a later
gate). No accuracy claim is made here. See ADR-0037 and
`architecture/gate18/discriminative-evidence-experiment.md`.
