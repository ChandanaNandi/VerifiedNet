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
| 10B | Reproducible training specification and trainer abstraction (no fine-tuning execution yet) | 4 | planned — consumes the Gate 10A corpus exclusively via `load_training_pairs` |
| 10C+ | Networking SLM fine-tuning (checkpoints, behind ModelAdapter) | 4 | planned (deferred behind 10A/10B) |
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
