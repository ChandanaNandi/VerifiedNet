# Architecture Decision Records

An ADR captures one significant decision: the context that forced it, the choice made,
and the consequences accepted. Records are numbered, dated, and immutable once accepted
— a later decision *supersedes* an earlier one rather than editing it.

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

Format for each record: **Status**, **Context**, **Decision**, **Consequences**,
**References** (to the gate document or source that motivated it).
