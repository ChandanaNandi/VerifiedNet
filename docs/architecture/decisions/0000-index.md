# Architecture Decision Records

An ADR captures one significant decision: the context that forced it, the choice made,
and the consequences accepted. Records are numbered, dated, and immutable once accepted
— a later decision *supersedes* an earlier one rather than editing it.

Records 0001–0009 govern the implemented foundation (Gates 0–3). Records 0010–0014 ratify
the long-term architecture (Layers 4–8); they are accepted as direction but their
implementation is deferred to the mapped future gates — see
`../../architecture/final-platform-vision.md`.

| # | Decision | Status |
|---|---|---|
| 0001 | Eight packages with schemas/interfaces split, AST-enforced boundaries | Accepted |
| 0002 | Runtime execution split: process runner + adapter + read-only/mutation grants | Accepted |
| 0003 | One canonical JSON representation; content-derived deterministic identifiers | Accepted |
| 0004 | `Phase` is a canonical `StrEnum` with coercing `PhaseField` | Accepted |
| 0005 | Mutation commands matched by exact, named command shapes | Accepted |
| 0006 | First vertical slice is a minimal two-router FRR eBGP lab | Accepted |
| 0007 | `RecoveryResult` merged into `IncidentRecord`, not a separate contract | Accepted |
| 0008 | ClosCall-derived behavior reimplemented from specification (licensing) | Accepted |
| 0009 | Ground truth is assembled only from deterministic evidence — never model output | Accepted |
| 0010 | Models are not ground truth (platform-wide invariant) | Accepted (long-term) |
| 0011 | SLM role and the verification boundary | Accepted (long-term) |
| 0012 | Operational memory and GraphRAG | Accepted (long-term) |
| 0013 | Orchestrator and agent boundaries | Accepted (long-term) |
| 0014 | Persistent workflows and the outcome engine | Accepted (long-term) |
| 0015 | Live FRR execution requirements: SYS_ADMIN, API config delivery, pinned interface names | Accepted |
| 0016 | Canonical per-run artifact directory: durability + integrity contract | Accepted |
| 0017 | Gate 4 composition root and run index | Accepted |
| 0018 | Datasets are derived from verified runs; the run library is authoritative | Accepted |

Format for each record: **Status**, **Context**, **Decision**, **Consequences**,
**References** (to the gate document or source that motivated it).
