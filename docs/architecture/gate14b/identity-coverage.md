# Gate 14B — Evaluation Corpus v3 Coverage Campaign (identity-first)

**Status:** IMPLEMENTED (Gate 14B). A DATA-COVERAGE gate: the registered
project evaluation corpus grows from v2 (168 examples, 22 eligible test
across only 5 distinct identities) to a descendant v3 planned
IDENTITY-FIRST —
through the UNCHANGED authoritative chain (scenario → verified run artifact →
IncidentRecord → projection → leakage-safe splitting → export → prepared
corpus → registration). It implements ADR-0032 on top of ADR-0031. **No
training, no model loading, no inference, no evaluation run, no benchmark, no
prompt change, no metric change, no split-policy change.**

## 1. The Gate 14 lesson, made structural

Gate 14 met its row targets (22 eligible test ≥ 20) while those 22 rows
spanned only **5 distinct held-out scenario identities** — repeated
executions inflate counts without improving independent coverage, and a
model evaluated on 5 identities is measured on 5 things, however many rows
they occupy. Gate 14B therefore plans by STABLE SCENARIO IDENTITY first and
makes the distinction fail-closed: `PartitionIdentityCoverage` counts
distinct leakage groups per partition (refusing to represent split leakage
at all), a frozen `IdentityCoveragePolicy` (`icpol-…`) states MANDATORY
identity minimums (≥ 8 distinct test identities, ≥ 6 distinct validation
identities, ≥ 4 topology variants) alongside the run-allocation rule, and
the readiness verdict below cannot say "ready" from row counts alone.

## 2. New identity dimensions (approved, bounded, meaningful)

Three NEW approved two-router topology variants (`2r-v4` AS 65201/65202 +
172.28.0.0/30 + 10.255.3.x, `2r-v5` AS 65301/65302 + 172.27.0.0/30 +
10.255.4.x, `2r-v6` AS 65401/65402 + 172.26.0.0/30 + 10.255.5.x — distinct
`topology_hash` each, same validated shape) join Gate 14's three. Twelve
approved additive catalog cases: six new RAS wrong-ASN parameter
combinations (`ras-alt3`…`ras-alt8`, values colliding with no approved
topology ASN, alternating orientations) and per-topology PF cases
(`pf-t4/t5/t6-ref/rev`, each withdrawing that topology's own advertised
loopback — the fail-closed prefix validation demands it). `SCENARIO_CATALOG`
and the Gate 14 matrix are untouched; `expansion_topology` resolves over the
full six-variant map while `build_expansion_matrix` keeps its frozen three.
The candidate POOL is the complete cross product: per topology 10 RAS + 2 NR
+ 2 IF + 2 PF = 16 identities × 6 topologies = **96 candidate identities**.

## 3. The identity-first planner (explicit deterministic priorities)

`plan_identity_first_selection` selects from the pool in a fixed priority
order, tie-broken by canonical stable identity (lexicographic `group_id`):
**(1) missing test identity** — every pool identity the production splitter
assigns to test; **(2) missing validation identity**; **(3) underrepresented
family** — remaining candidates of families projected strictly below the
current maximum family projection; **(4) underrepresented topology** and
**(5) missing parameter dimension** — canonically-first backfills for any
approved topology context or case id still absent; **(6) rejected coverage**
(12 abstention identities × 2 runs); **(7) reproducibility repeats** — the
per-partition run counts themselves. Run allocation comes ONLY from the
frozen policy (test 3, validation 3, train 4 — all inside the 2-4
reproducibility band); pool `planned_runs` values are ignored. The planner
PREDICTS partitions with the exact production splitter and has no parameter
through which it could assign, move, or exclude anything; its signature has
no channel for model predictions, evaluation results, or benchmark facts.
The selection itself is a frozen, content-addressed artifact
(`icsel-…` + `icseldig-…`, persisted under `identity-selections/<id>/`) —
the audit trail proving thresholds were not met by re-running favorites.

Over the real pool the selection is **58 identities**: 12 test (36 accepted
test examples), 14 validation (42), 32 train (128, including exactly one
`missing_parameter_dimension` backfill: `ras-alt4`), totalling 206 accepted
+ 24 rejected = 230 runs; family examples 67/46/47/46 (imbalance 1.4565 ≤
1.5); all 6 topology variants; identities per family 22/12/12/12.

## 4. v3 policy, campaign, registration, comparison

`build_expansion_policy_v3` freezes the v3 mandatory minimums: total ≥ 220,
accepted ≥ 196, abstention ≥ 16, validation ≥ 24, eligible test ≥ 30, ≥ 15
examples and ≥ 4 identities per family, class imbalance ≤ 1.5, rejection
code `precondition_failed` (the Gate 6 rejected projection supports
precondition-phase rejections only — abstention diversity comes from 12
distinct rejected identities across all six topologies, never from
unsupported codes). The identity minimums live in the identity policy and
their checks MERGE into the same fail-closed gate
(`combine_target_results`, colliding rule names refused) — so the
`CorpusExpansionBinding` carried by v3 contains the identity checks, and an
identity shortfall makes a v3 registration structurally impossible, exactly
like a row shortfall. The campaign, registration (version 3, parent binding
to v2 by id + digest), and comparison reuse Gate 14's stores unchanged;
`build_corpus_comparison_with_identity_deltas` extends the v2-versus-v3
report with `distinct_test/validation/train/abstention_identities` and
`distinct_identities_total` delta rows, fail-closing unless each identity
coverage was computed from the exact prepared corpus the corresponding
registration binds. v1 and v2 are never edited (fingerprinted before/after
operationally).

## 5. The evaluation readiness assessment (governs Gate 15)

`EvaluationReadinessAssessment` (`ready-…`, persisted under
`readiness-assessments/<id>/`) renders one of four outcomes with a fixed
precedence: `quality_failed` (structural quality verification failed),
`underpowered` (test or validation example counts below threshold),
`coverage_threshold_met_but_low_diversity` (rows suffice but distinct
test/validation identities or topology variants do not — the Gate 14 v2
verdict), `ready_for_controlled_experiment`. The model is SELF-VALIDATING:
outcome and every check are re-derived from the recorded facts and
thresholds, so an assessment claiming readiness its own numbers do not
support is unrepresentable — proven by a failure test in which ten
re-executions of one held-out identity meet every row target and still
cannot produce a "ready" verdict or a v3-style binding.

## 6. Operational v3 result

The gated operational campaign (`VERIFIEDNET_RUN_GATE14B=1`; requires the v1
and v2 registration dirs, the v2 prepared chain, and an output root; no ML
runtime, network sabotaged) runs the full 230 deterministic simulated runs
and registers project corpus v3: **36 eligible test accepted examples across
12 distinct test identities** (v2: 22 across 5), **42 validation examples
across 14 identities** (v2: 18 across 3), 24 abstention examples across 12
identities, 6 topology variants, class imbalance 1.456522. That clears the
30-example ADR-0029 directional threshold WITH independent identity
diversity behind it, and the persisted readiness assessment records
`ready_for_controlled_experiment` — the first corpus version authorized to
power a controlled experiment. Provenance remains `project_persisted` with
the generator explicitly recording the deterministic simulated catalog
chain — never called real lab incidents.

## 7. Training isolation and no-model-execution

Training corpora, specs, plans, authorizations, executions, and the real
checkpoint are untouched; training engines and the checkpoint writer are
trap-proven inert during planning and readiness assessment; no model,
tokenizer, or inference backend loads anywhere (`torch`/`transformers`
absent from `sys.modules`; network sabotaged in the security tier and the
operational run). The planner and readiness signatures are inspected in
tests as the structural proof that no model/benchmark fact can flow in.

## 8. Proof obligations discharged by tests

Identity policy id stability + sensitivity to every field; run-rule bounds
unrepresentable outside 2-4; split-leakage-unrepresentable identity
coverage; planner determinism + pool-order independence + production-splitter
agreement + priority ordering over the REAL 96-identity pool (12/14/58/206/24
verified exactly); selection/readiness store overwrite refusal + per-byte
tamper detection; readiness outcome totality with documented precedence;
repeated-execution low-diversity fail-closed path; prepared-digest and
policy-binding mismatches refused; the capped end-to-end chain (1 run per
identity — identity structure identical to the full campaign) registering
v1→v2→v3 with version listing, identity-delta comparison, and a
`ready_for_controlled_experiment` verdict; and the gated full operational
campaign with v1/v2 byte-immutability fingerprints.
