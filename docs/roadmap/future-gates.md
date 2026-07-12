# VerifiedNet — Roadmap (Future Gates)

The platform is built in strict gates. Gates 0–3 are complete (offline architecture and
contracts, 238 tests). Gate 4 is planned and approved (first live incident). Everything
from Gate 4 onward is **planned and not yet implemented** — listed here so the trajectory
is explicit. No capability is assumed to exist until its gate ships and is tested.

This roadmap is coordinated with `../architecture/final-platform-vision.md`, which defines
the eight architectural layers and the immutable deterministic trust core. The layers are
a *destination*; they never replace or weaken Gates 0–3.

## Gate list and dependency order

Gates run in dependency order. Completed Gates 0–4 are not renumbered.

| Gate | Focus | Layer(s) | Status |
|---|---|---|---|
| 0–3 | Verified foundation, offline: contracts, runtime, verifiers, fault lifecycle | 1 | **complete** |
| 4 | First live verified incident (two-router FRR; accepted + precondition-rejected) | 1 | approved, next |
| 5 | More fault families and lab backends (SONiC-VS, EVPN/VXLAN, SR Linux; ACL/Batfish) | 1 | planned |
| 6 | Verified dataset engine and leakage-safe splits (incident corpus, provenance) | 2 | planned |
| 7 | Deterministic rule baselines and evaluation framework/infrastructure | 3 | planned |
| 8 | Base SLM benchmark | 3, 4 | planned |
| 9 | Networking SLM fine-tuning (behind ModelAdapter) | 4 | planned |
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

## Immediate next: Gate 4 (unchanged)

Gate 4 remains exactly: one live two-router FRR lab; one accepted remote-AS-mismatch
incident; one healthy-lab precondition-rejected incident; real evidence; deterministic
verification; restoration and cleanup; manifests and artifacts. **No model, RAG, GraphRAG,
memory, agents, or persistent workflow.** See the approved Gate 4 plan; its scope is not
expanded by this roadmap.

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
