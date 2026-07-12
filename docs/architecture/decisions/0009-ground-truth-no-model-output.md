# 0009 — Ground truth is assembled only from deterministic evidence

**Status:** Accepted (non-negotiable principle; enforced in Gate 3)
**Date:** 2026-07-11

## Context

VerifiedNet's central contribution is *verified, reproducible* networking incidents. That
claim collapses if any part of the recorded truth — whether a fault occurred, whether it
was detected, whether recovery succeeded — is produced or influenced by a language model.
Models are permitted in the platform, but only to generate candidate parameters, operator-
language variations, and explanations *grounded in already-verified evidence*.

## Decision

Ground truth (`GroundTruth`) is constructed by the oracle (`incidents/oracle.py`) from
**only** three inputs: recorded fault-injection metadata, deterministic
`VerificationResult`s, and accepted evidence references. `GroundTruth.root_cause_label`
must be a machine label (whitespace/free-text rejected by the schema). The `incidents`
package is AST-forbidden from importing `runtime`, `labs`, `collectors`, or `faults`, and
cannot reach any model provider. Evidence carries a `trusted` flag; a claim can never be
verified by untrusted evidence, and `UNKNOWN`/`INSUFFICIENT` verdicts never commit toward
truth. Any language generation happens strictly *after* the verdicts exist and is
quarantined from this package.

## Consequences

- The truth chain for the BGP scenario is provable end-to-end by collectors and verifiers
  alone (the ten-fact proof matrix), with no model in the loop.
- Onset requires proving the *mismatch* (wrong remote-AS AND not-Established AND peer
  config unchanged), not merely that BGP is down; recovery requires both re-established
  session and restored routes in both directions.
- The design forecloses a whole class of "the model said it worked" failures.

## References

- `../gate2/wave_a_file_harvest_plan.md` §10 (ground-truth proof matrix)
- `../gate3/contracts.md` (GroundTruth, verdict semantics)
- Project principles 11–15
