# VerifiedNet — Engineering Documentation

This directory is the project's engineering notebook: not just what was built, but
**why**. It is organized by purpose so that any decision, inventory, or rationale can
be found quickly months later.

## Layout

```
docs/
├── README.md                     # this index
├── architecture/                 # how the system is designed, gate by gate
│   ├── final-platform-vision.md  # the long-term destination (8 layers, trust core)
│   ├── gate0/                     # source inventory, licenses, environment assumptions
│   ├── gate1/                     # capability map + code-reuse matrix
│   ├── gate2/                     # Wave A file-level harvest plan
│   ├── gate2_5/                   # architecture validation before implementation
│   ├── gate3/                     # contracts, package boundaries, runtime/security, limitations
│   └── decisions/                # Architecture Decision Records (ADRs)
├── provenance/                   # where reused/adapted code came from + license posture
├── research/                     # deep engineering audits of the source repositories
└── roadmap/                      # planned future gates (4–15)
```

## Reading order (for a newcomer)

1. Top-level `../README.md` — what VerifiedNet is and its current status.
2. `architecture/final-platform-vision.md` — the destination and the layers.
3. `roadmap/future-gates.md` — the gate-by-gate path there.
4. `architecture/decisions/` — the load-bearing choices, each in one short record.
5. `architecture/gate3/contracts.md` + `package_boundaries.md` — the current shape.
6. The gate folders (`gate0` → `gate3`) — the full derivation, in order.
7. `research/` — the source-repo audits that seeded every reuse decision.
8. `provenance/wave_a_provenance.md` — the audit trail for every adapted symbol.

## Conventions

- **Decision records** live in `architecture/decisions/NNNN-title.md`, numbered and
  immutable once accepted (supersede rather than edit). Format: Status / Context /
  Decision / Consequences / References.
- **Knowledge, not transcripts.** Discussions are distilled into concise engineering
  documents (a design note, an ADR, a research page) — raw chat logs are not stored.
- **Design notes** (`docs/design/`), **brainstorming** (`docs/brainstorming/`), and
  **meeting notes** (`docs/meeting_notes/`) are part of this convention; those folders
  are added when there is real content to put in them, rather than kept empty.
- Gate documents are historical: they record the state at that gate and are not
  rewritten later. Corrections are captured in a subsequent gate or an ADR.

## Status

Gates 0–3 complete (offline architecture and contracts). Gate 4 complete: the first live
verified incidents (two-router FRR; accepted + precondition-rejected), canonical per-run
artifacts, a run index, and a thin composition root — see
`architecture/gate4/gate4-completion-report.md`. Gate 5 is in progress: the
evidence-based fault-family plan (Gate 5.0) is in
`architecture/gate5/fault-family-plan.md`; Gate 5 is complete: a verified fault-family
library (BGP remote-AS mismatch, neighbor removal, interface shutdown, prefix
withdrawal), a bounded scenario catalog with reverse-orientation proof, and
cross-family isolation — see `architecture/gate5/gate5-completion-report.md`.
Gate 6 (verified dataset engine) is implemented through Gate 6.2: the engine
design (Gate 6.0) is in `architecture/gate6/` (dataset-engine-plan,
leakage-analysis, dataset-schema, splitting-strategy, gate6-roadmap) with
ADR-0018. Gate 6.1 (read-only models, discovery, integrity-gated projection),
Gate 6.2 Part 2 (rejected-as-abstention projection, deterministic integer-bucket
splitting, fail-closed leakage audit), Gate 6.2 Part 3 (the immutable exported
dataset — corpus manifest, self-validating `dataset_digest`,
writer/reader/verifier, reproducibility), and Gate 6.2 Part 4 (explicit
feature/label/trace separation with versioned policies, a feature-leakage audit,
and the persisted "prepared" corpus with a model-facing features-only loader) now
exist in `verifiednet.datasets` — a read-only, model-free projection that never
mutates a verified run; see
`architecture/gate6/rejected-examples-and-leakage-safe-splits.md`,
`architecture/gate6/exported-dataset-and-reproducibility.md`, and
`architecture/gate6/feature-label-separation.md`.
Gate 7 (deterministic evaluation framework) is implemented in
`verifiednet.evaluation` with ADR-0019: a versioned evaluation-task contract,
deterministic model-free rule baselines (a fixed-prior floor + an
evidence-rule baseline) that receive ONLY model-visible features, abstention-aware
scoring with separate accepted/abstention metrics, and an immutable,
content-addressed evaluation result (manifest + records + metrics + confusion)
with a self-validating `evaluation_digest`, a recompute-from-records verifier, and
reproducibility/immutability/no-execution proofs. No model, LLM, embedding, or
training is involved. See `architecture/gate7/evaluation-framework.md`. Layers 2–8
in `final-platform-vision.md` are **planned, not implemented** — no AI, RAG, GraphRAG, SLM,
agent, memory, or persistent workflow exists yet. The deterministic trust core (labs →
faults → evidence → verification → oracle → incidents → recovery → artifacts → index) is
fixed and is never replaced by a model. See `architecture/gate3/limitations.md`.
