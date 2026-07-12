# 0007 — `RecoveryResult` merged into `IncidentRecord`, not a separate contract

**Status:** Accepted (owner-approved, Gate 2.5 → Gate 3)
**Date:** 2026-07-11

## Context

The initial contract list proposed a standalone `RecoveryResult`. On review, recovery
verification produces exactly the same shapes as onset verification — evidence records and
phase-tagged verification results — plus some restoration metadata. A separate contract
would largely duplicate `VerificationResult` with a phase rename.

## Decision

Do not create a `RecoveryResult` schema. Represent recovery inside `IncidentRecord` via a
`restoration` section (`RestorationMetadata`: method, whether a forced reset / clear-BGP
was used, restore-command transcript references, completion status, and failure reason)
together with phase-tagged `recovery_results` (`VerificationResult`s) and
`recovery_evidence`.

## Consequences

- One fewer contract to version and keep consistent.
- Recovery is a first-class, fully-recorded part of every incident, including the
  clear-BGP annotation (see below) and the failure reason on the rejected path.
- The forced-reset flag matters scientifically: `clear bgp <peer>` after restore changes
  *timing* (forced reconvergence), not the *state* facts, and is annotated so future
  latency work does not misread it.

## References

- `../gate2_5/architecture_validation.md` §5 (contract-by-contract validation)
- `../gate3/contracts.md` (IncidentRecord)
