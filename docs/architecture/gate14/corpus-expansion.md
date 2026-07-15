# Gate 14 — Evaluation Corpus Expansion to v2

**Status:** IMPLEMENTED (Gate 14). A DATA-COVERAGE gate: the registered
project evaluation corpus grows from v1 (22 examples, 2 eligible test) to a
descendant v2 with substantially more held-out coverage — through the
UNCHANGED authoritative chain (scenario → verified run artifact →
IncidentRecord → projection → leakage-safe splitting → export → prepared
corpus → registration). It implements ADR-0031. **No training, no model
loading, no inference, no evaluation run, no benchmark, no prompt change, no
metric change, no split-policy change.**

## 1. Why corpus growth precedes retraining

Gate 12's interpretation policy (ADR-0029) demands 30 eligible test examples
for any directional claim; v1 has 2. Retraining before the measurement
foundation can detect an effect would produce another inconclusive result by
construction. Gate 14 therefore grows coverage first — and it grows STABLE
IDENTITIES, not copies: split assignment is keyed by the stable `group_id`
(template + scenario id + orientation + parameters + topology + backend), so
only new identities can ever reach the validation/test partitions.

## 2. New stable identities (approved, bounded, meaningful)

Three approved two-router topology VARIANTS (`2r-v1` the original, `2r-v2`
AS 65101/65102 + 172.31.0.0/30 + 10.255.1.x, `2r-v3` AS 64601/64602 +
172.29.0.0/30 + 10.255.2.x — distinct `topology_hash` each, same validated
shape) multiply the identity space for every family. Five approved catalog
additions (`EXPANSION_SCENARIO_CATALOG`, additive — `SCENARIO_CATALOG` is
untouched): `ras-alt2` (fourth wrong-ASN orientation) and per-topology PF
cases (`pf-t2-ref/rev`, `pf-t3-ref/rev`, each withdrawing that topology's own
advertised loopback — the fail-closed prefix validation demands it). NR and
IF expose no further parameters, so they are honestly capped at their two
orientations per topology — not padded with text-only duplicates. The
matrix: per topology 4 RAS + 2 NR + 2 IF + 2 PF = 10 identities × 3
topologies = **30 identities**. Runs-per-identity is uniform within a family
(RAS 4, others 6) chosen ONLY to balance family example counts (156 accepted,
imbalance 1.33). Rejected coverage: 6 distinct rejected identities (2
orientations × 3 topologies) × 2 runs = 12 abstention examples — all
`precondition_failed`, because the Gate 6 rejected projection supports
precondition-phase rejections only (a documented contract boundary, reported
as an advisory finding, not silently ignored).

## 3. Partition-blind selection + exact split prediction

The matrix is the COMPLETE cross product of the approved dimensions; nothing
is included or excluded by its (predicted) partition, and the planner's
signature has no channel for model predictions, evaluation results, or
benchmark rankings. Because every candidate identity is fully defined before
execution, `plan_evaluation_corpus_expansion` predicts each group's partition
with the EXACT production splitter (`group_id_for_identity` +
`assign_group_split`) and records run-weighted predicted counts; the
post-projection coverage must match the prediction exactly (tested offline
and operationally). The split policy (salt `gate6`, 80/10/10 buckets) is
byte-unchanged — changing it would break comparability with v1 and every
prior evaluation, and a changed salt is a visibly different
`split_policy_id`, not an override.

## 4. Policy, campaign, and append-only versioning

`EvaluationCorpusExpansionPolicy` (`ecexp-…`, frozen) states the v2 minimums:
total ≥ 80, accepted ≥ 64, abstention ≥ 12, validation ≥ 12, eligible test
≥ 20, ≥ 12 examples and ≥ 3 identities per family, class imbalance ≤ 1.5,
rejection-code coverage `precondition_failed` — MANDATORY, gating
registration; topology variants ≥ 3 and duplicate-content ratio ≤ 20% are
explicitly ADVISORY (the feature allowlist intentionally withholds
distinguishing content, so identical model-visible features across
identities are expected and reported, never pruned). An unmet mandatory
target makes the `CorpusExpansionBinding` unrepresentable
(`targets_satisfied: Literal[True]`), so an under-target v2 cannot register
as an expansion at all. `VerifiedRunGenerationCampaign` (`campaign-…` +
`campdig-…`) immutably records intended identities, expected run counts, and
every produced verified-run id (missing or unexpected runs are validation
errors); it persists under `generation-campaigns/<id>/`. v2 registers through
the UNCHANGED Gate 13 store with three additive, backward-compatible
elements: an optional `expansion` manifest field, an optional
`expansion.json` file, and digest coverage of the binding only when present —
every v1 artifact still verifies byte-for-byte (tested). Versions are
APPEND-ONLY descendants: v2 binds v1's id + digest; v1 is never edited.

## 5. Duplicate-content diagnostics

v1's five duplicate-content groups are now classified: they are DISTINCT
identities producing identical model-visible features — one group per fault
family plus one for abstentions — a direct consequence of the Gate 6 feature
allowlist (topology hash, backend, evidence presence) and the single v1
topology. They are allowed, documented, and do not cross leakage groups
improperly (group cohesion is verified fail-closed). v2's three topology
variants reduce feature collapse across topologies while the within-family,
within-topology duplicates remain expected and reported (advisory ratio).
Duplicate source run ids and duplicate example ids remain hard failures.

## 6. The v1-versus-v2 comparison

`build_corpus_comparison` produces a deterministic, model-metric-free report
(`corpus-comparisons/<ccmp-…>/`): count deltas (total, accepted, abstention,
train, validation, eligible test), diversity deltas (distinct scenarios,
topologies, rejection codes), per-family deltas, duplicate and imbalance
changes, and the mandatory/advisory target verdicts from the binding. It
fail-closes unless the descendant genuinely binds the given parent.

## 7. Scientific threshold honesty

The full campaign's deterministic split yields 5 test groups (4 RAS, 1 NR)
and 3 validation groups of the 30 identities: **22 eligible test accepted
examples and 18 validation examples**. That clears the Gate 14 minimums (20 /
12) but remains BELOW the 30-example ADR-0029 directional threshold — and the
22 test examples span only 5 distinct scenario identities, which the coverage
report states plainly. Reaching 30 by re-running the same few test-landing
identities 6–8× each would be exactly the weak duplication this gate
forbids; the honest paths to 30+ are a v3 campaign with more approved
identities (more topology variants, more RAS parameters, new template
parameters, or real lab runs). Provenance is `project_persisted` with the
generator explicitly recording the deterministic simulated catalog chain —
never called real lab incidents.

## 8. Training isolation and no-model-execution

Training corpora, specs, plans, authorizations, executions, and the real
checkpoint are untouched (fingerprinted before/after the whole expansion;
training engines and the checkpoint writer are trap-proven inert; the new
corpus is NOT training data — the training layer cannot even parse it, and a
future training-corpus version requires its own gate). No model, tokenizer,
or inference backend loads anywhere in Gate 14 (`torch`/`transformers`
absent from `sys.modules` across the whole pipeline; network sabotaged).

## 9. Proof obligations discharged by tests

Policy/plan/campaign id stability + sensitivity to every field; planning
order-independence; split-prediction agreement with the production splitter
(exact, run-weighted, verified after projection); the capped end-to-end
campaign chain (30 identities × 1 run) registering v2 with parent binding,
version listing v1+v2, strict test/validation growth; unmet targets blocking
binding and registration; parent/policy mismatch refusal; campaign run
accounting (missing/duplicate/unexpected runs); per-byte tamper evidence and
overwrite refusal on campaign and comparison stores; v1 + all training
artifacts byte-identical across the pipeline; build-twice byte-identical
corpora over identical inputs including the artifact root (run transcripts
honestly record the execution work-dir as runtime evidence); and the
model/benchmark-free planner signature. The FULL campaign (168 runs) is the
gated operational integration test (`VERIFIEDNET_RUN_GATE14=1` + v1 dir +
output root).

## 10. Explicitly out of scope

No retraining, no model evaluation, no benchmarking, no prompt changes, no
RAG, no agents, no deployment, no new fault family (that requires its own
gate: scenarios, ground truth, contracts, label/task support, baseline
review). Next: if v2's held-out coverage is judged sufficient, a controlled
retraining experiment; otherwise a v3 coverage campaign first.
