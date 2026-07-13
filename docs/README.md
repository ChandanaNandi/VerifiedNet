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
`architecture/gate5/fault-family-plan.md`; Gates 5.1–5.2 (shared lifecycle
enablers + the BGP neighbor-removal family) are implemented and live-verified —
see `architecture/gate5/neighbor-removal-family.md`. Layers 2–8 in
`final-platform-vision.md` are **planned, not implemented** — no AI, RAG, GraphRAG, SLM,
agent, memory, or persistent workflow exists yet. The deterministic trust core (labs →
faults → evidence → verification → oracle → incidents → recovery → artifacts → index) is
fixed and is never replaced by a model. See `architecture/gate3/limitations.md`.
