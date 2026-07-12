# 0004 — `Phase` is a canonical `StrEnum` with a coercing `PhaseField`

**Status:** Accepted (Gate 3 dependency-freeze correction 4)
**Date:** 2026-07-11

## Context

Incident phase (baseline / onset / recovery / precondition) is referenced across schemas,
verifiers, collectors, and the fault lifecycle. It was originally a `Literal[...]` string
type, and the fault scenario passed raw strings (`"onset"`) into `evidence_provider`. Raw
strings are easy to typo, carry no identity, and blur the boundary between a phase and an
arbitrary string.

## Decision

Make `Phase` a canonical `StrEnum` (`BASELINE`/`ONSET`/`RECOVERY`/`PRECONDITION`). Schema
fields use `PhaseField = Annotated[Phase, BeforeValidator(_coerce_phase)]`, which accepts
either a `Phase` member or its string value and always stores the enum member. Canonical
JSON output is unchanged (`"onset"`), so no serialized data or hashes change. All
lifecycle call sites pass `Phase` members, never raw strings.

The coercing validator was chosen over a plain `StrEnum` field because Pydantic strict
mode rejects a raw string for an enum field; coercion keeps both enum and string inputs
valid (avoiding churn in existing call sites) while guaranteeing the stored value is
always the canonical member.

## Consequences

- Phase is now a first-class type with a fixed membership; invalid phases are rejected.
- `evidence_provider(Phase.ONSET)` is self-documenting and typo-proof.
- Serialization and every existing content hash are byte-for-byte unchanged.

## References

- Gate 3 dependency-freeze report (correction 4)
- `../gate3/contracts.md` (verdict/phase semantics)
