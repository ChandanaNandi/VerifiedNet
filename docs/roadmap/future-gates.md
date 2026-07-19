# VerifiedNet — Roadmap (Future Gates)

The platform is built in strict gates. Gates 0–3 are complete (offline architecture and
contracts). Gate 4 is complete: the first live verified incidents (two-router FRR;
accepted + precondition-rejected), canonical artifacts, run index, and a thin composition
root. Gate 5 is complete: a verified fault-family library (four accepted families, a
deterministic rejection, a bounded scenario matrix with reverse-orientation proof, and
cross-family isolation) — see `../architecture/gate5/gate5-completion-report.md`.
Everything from Gate 6 onward is **planned and not yet implemented** — listed here so
the trajectory is explicit. No capability is assumed to exist until its gate ships and is
tested.

This roadmap is coordinated with `../architecture/final-platform-vision.md`, which defines
the eight architectural layers and the immutable deterministic trust core. The layers are
a *destination*; they never replace or weaken Gates 0–3.

## Gate list and dependency order

Gates run in dependency order. Completed Gates 0–4 are not renumbered.

| Gate | Focus | Layer(s) | Status |
|---|---|---|---|
| 0–3 | Verified foundation, offline: contracts, runtime, verifiers, fault lifecycle | 1 | **complete** |
| 4 | First live verified incident (two-router FRR; accepted + precondition-rejected); artifacts, run index, composition root | 1 | **complete** |
| 5 | Verified fault-family library: BGP remote-AS mismatch, neighbor removal, interface shutdown, prefix withdrawal; bounded scenario matrix; cross-family isolation | 1 | **complete** — see `../architecture/gate5/gate5-completion-report.md` |
| 6 | Verified dataset engine and leakage-safe splits (incident corpus, provenance) | 2 | Gate 6.2 complete — 6.1 + Parts 2-4: read-only projection, rejected-as-abstention, deterministic integer-bucket splitting, fail-closed leakage audit, immutable exported dataset with self-validating `dataset_digest` + writer/reader/verifier + reproducibility, and feature/label/trace separation with a feature-leakage audit and the persisted "prepared" corpus. See `../architecture/gate6/feature-label-separation.md` |
| 7 | Deterministic rule baselines and evaluation framework/infrastructure | 3 | **complete** — versioned task contract, model-free rule baselines (fixed-prior + evidence-rule) over a feature-only boundary, abstention-aware scoring, immutable content-addressed evaluation results with a recompute-from-records verifier and reproducibility/no-execution proofs (ADR-0019). See `../architecture/gate7/evaluation-framework.md` |
| 8 | Base SLM benchmark | 3, 4 | **complete** — first model-backed predictor (`SlmPredictor`) on the Gate 7 feature-only boundary: versioned prompt template + predictor spec (deterministic ids), pluggable inference backend (deterministic fake by default; optional integration-only Ollama), strict parse/validate/normalize with an explicit invalid-prediction outcome, evaluation framework unchanged (ADR-0020). See `../architecture/gate8/slm-predictor.md` |
| 9 | Multi-predictor benchmark framework (fair, reproducible side-by-side comparison) | 3 | **complete** — deterministic predictor registry, order-independent content-addressed `BenchmarkSpec`, `run_benchmark` over the unchanged Gate 7 engine, deterministic comparison metrics + fully tie-broken ranking, immutable benchmark artifacts with a self-validating `benchmark_digest` and a recompute-ranking-from-comparison verifier (ADR-0021). See `../architecture/gate9/benchmark-framework.md` |
| 10A | Training readiness: supervised training-corpus boundary | 4 | **complete** — Literal-locked train-only/accepted-only `TrainingDataPolicy`, content-addressed input/target templates, self-validating training-example and corpus identities, immutable `training-corpora/<id>/` layout with a trainer-facing pairs-only loader, training leakage audit, and tested partition-isolation/source-immutability/no-execution guarantees (ADR-0022). No model training occurs. See `../architecture/gate10/training-corpus.md` |
| 10B | Reproducible training specification and trainer abstraction (no fine-tuning execution yet) | 4 | **complete** — content-addressed `TrainingSpec` pinning every weight-affecting input (immutable model/tokenizer revisions, canonical decimal hyperparameters, full seed policy, validated batch shape, corpus binding by id+digest), fail-closed capability negotiation, `Trainer` protocol whose authoritative operation is `plan` (no `train()`), exact integer batch/step arithmetic with honest determinism claims, offline `FakeTrainer`, and immutable verified `training-plans/<id>/` artifacts with source-immutability/evaluation-isolation/no-real-training proofs (ADR-0023). No fine-tuning occurs; no ML framework is imported. See `../architecture/gate10/training-plan.md` |
| 10C | Deterministic training execution framework (simulation only) | 4 | **complete** — closed execution state machine (planned/validated/starting/running/completed with failed/cancelled branches and failed→resumed→running), hash-chained timestamp-free event log verified by deterministic REPLAY, content-addressed execution ids over plan+capability+retry-policy+retry-number (a retry is a new execution; one authoritative outcome per identity), scripted failure/cancellation, property-proven resume consistency for every failure point, fail-closed retry policy, and immutable verified `training-executions/<id>/` artifacts (ADR-0024). Simulation only — no ML framework is imported. See `../architecture/gate10/training-execution.md` |
| 10D | Immutable checkpoint artifact and lineage contract (fake checkpoints only) | 4 | **complete** — untrusted candidate vs verified artifact boundary, two-layer identity (logical `checkpoint-` id over format+lineage+roles; content `ckptdig-` digest over verified bytes), lineage bound to a VERIFIED completed execution (plan/spec/corpus/model/tokenizer/capability/policy/retry; parent checkpoints structurally forbidden), deterministic fake producer with magic-prefixed payloads, layered simulation honesty (Literal-locked format, not-real-loadable compatibility, no model-loading API), fail-closed verifier + independent lineage audit, and proofs for source immutability, evaluation isolation, training-content absence, identity ripple, and tamper rejection (ADR-0025). No real weights exist. See `../architecture/gate10/checkpoint-artifact.md` |
| 10E | Real trainer-backend contract and execution preflight | 4 | **complete** — content-addressed `RealTrainerBackendSpec` (single-device HF FULL fine-tuning, the only modeled mode; LoRA deliberately unclaimed), strict immutable-intent vs runtime-evidence boundary (ADR-0026), secret-free `TrainingEnvironmentSnapshot` with PEP 440 package records and device capability, separate immutable model/tokenizer resolution (pinned revision + content hash; mutable aliases unrepresentable; no downloads), 12-stage structured preflight with visible skips, honest determinism categories requiring explicit best-effort acknowledgement, conservative deterministic memory estimation, and immutable verified `training-authorizations/<id>/` evidence whose validity is recomputed rather than trusted. No real training, model loading, or checkpoint; no ML dependency added. See `../architecture/gate10/execution-preflight.md` |
| 10F | First bounded real training execution | 4 | **complete** — authorized-executor boundary with no bypass (authorization required and revalidated before model loading), four content-addressed bounded policies (approved model `bmodel-`, deterministic first-N corpus slice `cslice-`, exact causal-LM objective `objpol-`, Literal-locked runtime ceilings `rexecpol-`), local-only content-hashed model/tokenizer resolution (never downloads), structurally-verified real execution evidence with explicit consistency classes (no replay claims, no quality claims), the first genuine checkpoint format `verifiednet.real-checkpoint-v1` (full-model safetensors, dependency-free structural validation, complete lineage, publication only from verified completed executions), optional `training-hf` extras behind one sanctioned lazy-import module, deterministic offline stub, and a double-gated real integration test with a weight-mutation proof (ADR-0027). No evaluation or benchmarking of the trained checkpoint. See `../architecture/gate10/real-training.md` |
| 10G | Checkpoint-backed prediction behind the Gate 8 feature-only interface, then evaluation and Gate 9 benchmark comparison | 3, 4 | **prediction complete** (delivered as Gate 11) — fail-closed checkpoint eligibility from the on-disk artifact only, verified bundle with no construction-time model loading and moment-of-use re-verification, narrow Literal-locked inference scope (local HF, CPU fp32, no fallback/quantization/adapters/remote code/network), second sanctioned lazy-ML site, `VerifiedCheckpointPredictor` on the unchanged Gate 7/8 boundary reusing the Gate 8 prompt/parser/prediction pipeline, content-addressed `ckptpred-` identity in a Gate-7 `BaselineSpec`, and a double-gated real-inference integration test (ADR-0028). See `../architecture/gate11/checkpoint-predictor.md`. **Evaluation + benchmark delivered as Gate 12** — matched base-model predictor (verified pinned-snapshot bundle through the same inference stack; weights the only difference), unchanged Gate 7 evaluation and Gate 9 benchmark of fixed-prior/evidence-rule/base/trained, explicit fairness checks with visible confounds, exact paired comparison + disagreement report over aligned example ids, frozen wording-only `BenchmarkInterpretationPolicy` (fixture corpora → engineering conclusions; underpowered → inconclusive; regressions always surfaced), immutable content-addressed `comparisons/` store, measurement isolated from training (ADR-0029). See `../architecture/gate12/checkpoint-benchmark.md` |
| 10H | Persisted evaluation corpus and structured-output reliability | 3 | **complete** (delivered as Gate 13) — registered, versioned, content-addressed evaluation corpora with explicit provenance, source-Literal-locked generation policy, deterministic coverage statistics (eligible-test-example count, fault-family/scenario/rejection/topology distributions, split balance, imbalance ratios), fail-closed structural quality verification (duplicates, split leakage, malformed examples, missing evidence), immutable registration + audit + deterministic version listing; deterministic invalid-output categorization (the two real Gate 12 failure shapes are named categories), per-run parser statistics with self-consistent rates, MEASURED prompt compliance, and a separate immutable structured-output report per benchmark — Gate 7/8/9 semantics byte-unchanged (ADR-0030). See `../architecture/gate13/evaluation-corpus.md` |
| 10I | Evaluation corpus expansion to v2 | 3 | **complete** (delivered as Gate 14) — append-only descendant corpus versions with parent binding (ADR-0031); frozen expansion policy (mandatory + advisory targets), deterministic scenario-coverage matrix, partition-blind 30-identity expansion matrix (3 approved topology variants × approved catalog cases incl. 5 additive expansion cases), exact production-splitter prediction verified after projection, immutable generation-campaign record, model-metric-free v1-versus-v2 comparison store; v2 reaches 22 eligible test / 18 validation examples (honestly below the 30-example ADR-0029 threshold; 5 distinct test identities reported). See `../architecture/gate14/corpus-expansion.md` |
| 10J | Evaluation corpus v3 coverage campaign (identity-first) | 3 | **complete** (delivered as Gate 14B) — independent held-out identity coverage becomes the readiness criterion (ADR-0032): frozen identity-coverage policy (≥ 8 distinct test identities, ≥ 6 validation, ≥ 4 topology variants, 2-4 runs per identity), deterministic identity-first planner over the complete 96-identity pool (3 new topology variants `2r-v4/v5/v6` + 12 additive catalog cases) with explicit priority order and content-addressed selection artifact, identity checks merged into the registration-blocking gate, identity-delta corpus comparison, and the persisted self-validating `EvaluationReadinessAssessment`; v3 registers with 36 eligible test examples across 12 identities / 42 validation across 14 (v2: 22 across 5 / 18 across 3), verdict `ready_for_controlled_experiment`. See `../architecture/gate14b/identity-coverage.md` |
| 10K | Controlled retraining experiment on corpus v3 | 3, 4 | **complete** (delivered as Gate 15) — preregistered, content-addressed experiment specification with frozen hypothesis/metrics/success policy (ADR-0033); one-run/one-checkpoint Literal rule; ordered phase firewall + structural held-out-identifier audit; train-only corpus from the v3 prepared chain (128 eligible, preregistered first-64 canonical cap under the Gate 10F envelope); one bounded real CPU fine-tune (64 steps) of the pinned Qwen2.5-0.5B snapshot; matched base-versus-trained evaluation on registered corpus v3 via unchanged Gate 7/9/12/13; self-validating result whose outcome derives from raw paired counts; new top-level `experiment` composition layer. See `../architecture/gate15/controlled-experiment.md` |
| 10L | Contract-aligned retraining experiment | 3, 4 | **16A complete** — additive training-input template v2 rendering byte-identically to the frozen Gate 8 prompt (ADR-0034): mirrored Literal-locked contract text (no evaluation import; cross-layer byte-equality contract/property proofs), v1 renderings and identities pinned, target/objective/eligibility invariant, same-64-source proof, gated real-tokenizer sequence-policy proof. **16B complete** — the second preregistered one-run experiment binding the v2 policy (no production change; the v2 binding flows through the existing spec's training-corpus-policy id), same ordered 64 sources / identical targets / identical budget as Gate 15, fresh-from-base treatment checkpoint (no warm start), matched evaluation on registered corpus v3 through the unchanged Gate 7/9/12/13 machinery, frozen-policy outcome (validity gain without accuracy gain is `mixed`, never `improved`). See `../architecture/gate16/contract-aligned-conditioning-experiment.md` |
| 10M | Objective / representation / selection experiment series | 3, 4 | **17A/17B complete** — boundary-aligned objective (`objpol-7e6428964eae2db8`, ADR-0035) preregistered one-run (17B): validity `0/230 → 230/230`, accuracy `0/36`, outcome `mixed`. **18A/18B complete** — discriminative feature policy v2 (`feat-228b357dd9f256fa`, ADR-0036); 18B changed only v1→v2 representation: first held-out gain (`3/36`), outcome `improved`, but the model collapses the three active-state families onto the majority. **19A complete** — the Gate 19 diagnosis localised the collapse to training-family imbalance (representation proven sufficient: four payloads, one per family, a four-flag oracle scores 36/36 on test), and Gate 19A adds a content-addressed family-balanced source-selection policy (budget-preserving 20/20/20/4, train-only, deterministic, fail-closed; ADR-0037) with no fine-tune. **19B (one experiment binding the balanced corpus) is unstarted.** See `../architecture/gate17/boundary-aligned-experiment.md`, `../architecture/gate18/discriminative-evidence-experiment.md`, `../architecture/gate19/family-balanced-selection.md` |
| 10 | Vector RAG and operational retrieval | 5 | planned |
| 11 | GraphRAG and provenance-aware knowledge graph | 5 | planned |
| 12 | Confidence, grounding, hallucination, robustness, calibration evaluation | 3 | planned |
| 13 | Intelligent orchestrator and agent harness | 6 | planned |
| 14 | Safe remediation, approval binding, rollback | 8 | planned |
| 15 | Persistent workflows, operational memory, and outcome engine | 5, 7, 8 | planned |

### Note on ordering (change from the earlier draft)

This mapping refines the earlier `future-gates.md` draft (which followed the original
project brief). The intentional changes, adopted per the owner's coordination checkpoint:

- The **evaluation framework** is split: baseline + eval *infrastructure* moves earlier
  (Gate 7), while *model-quality* metrics (grounding, hallucination, calibration,
  robustness) sit at Gate 12, after the SLM and knowledge layers exist to be measured.
- **SLM fine-tuning** (Gate 9) now precedes **vector RAG** (Gate 10) and **GraphRAG**
  (Gate 11). This is a deliberate sequencing choice: fine-tuning depends only on the
  dataset (Gate 6) and baselines (Gate 7), so it can proceed before retrieval is built.
- **Orchestrator/agents** move to Gate 13, **safe remediation/rollback** to Gate 14, and
  **persistent workflows + operational memory + outcome engine** to Gate 15.
- The original brief's standalone "dashboard / public benchmark reports / reproducibility
  release" is **not a separate gate** in this mapping. It is treated as a cross-cutting
  release deliverable accompanying Gate 12 (evaluation reports) and Gate 15 (operational
  surface). Flagged here rather than silently dropped.

No other change was required by existing project evidence.

## Gate 4 (complete)

Gate 4 delivered exactly: one live two-router FRR lab; one accepted remote-AS-mismatch
incident; one healthy-lab precondition-rejected incident; real evidence; deterministic
verification; restoration and cleanup; manifests, canonical per-run artifacts, a run index,
and a thin composition root that executes both paths. **No model, RAG, GraphRAG, memory,
agents, or persistent workflow.** See `../architecture/gate4/gate4-completion-report.md`;
its scope was not expanded. Gate 5 is next and does not renumber earlier gates.

## Standing rules across all future gates

- No capability (RAG, GraphRAG, SLM training, agents, memory, workflows) is claimed until
  implemented and tested. Planned ≠ done.
- No performance number is invented; every metric comes from a reproducible run.
- The SLM is one model track inside the platform, compared against deterministic rules,
  the base model, vector RAG, GraphRAG, fine-tuning, and their combinations — never
  presented as the whole platform.
- Ground truth stays model-free (ADR-0009, ADR-0010); no model output becomes a training
  label without deterministic verification (ADR-0011).
- The package dependency graph stays acyclic; the orchestrator is the composition root
  (ADR-0013).

## Two SLM tracks (future)

- **Track A** — a small decoder-only Transformer built from scratch, for education and
  architectural understanding; not presented as the operational model unless evaluation
  proves it.
- **Track B** — a practical open-weight SLM adapted via continued pretraining / SFT /
  LoRA-QLoRA, benchmarked against every baseline above.
