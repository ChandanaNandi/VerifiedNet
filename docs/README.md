# VerifiedNet — Engineering Documentation

This directory is the project's engineering notebook: not just what was built, but
**why**. It is organized by purpose so that any decision, inventory, or rationale can
be found quickly months later.

## Layout

```
docs/
├── README.md                     # this index
├── architecture/                 # how the system is designed, gate by gate
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
2. `roadmap/future-gates.md` — where it is going.
3. `architecture/decisions/` — the load-bearing choices, each in one short record.
4. `architecture/gate3/contracts.md` + `package_boundaries.md` — the current shape.
5. The gate folders (`gate0` → `gate3`) — the full derivation, in order.
6. `research/` — the source-repo audits that seeded every reuse decision.
7. `provenance/wave_a_provenance.md` — the audit trail for every adapted symbol.

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

Gates 0–3 complete. The platform is offline/architecture only: no live network has
run, no AI capability exists yet, and FRR parser fixtures remain source-derived until
Gate 4 re-records them against a live lab. See `architecture/gate3/limitations.md`.
