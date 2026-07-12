# 0003 — One canonical JSON representation; content-derived identifiers

**Status:** Accepted (Gate 3; raised as Gate 2.5 W5/W11)
**Date:** 2026-07-11

## Context

Content hashes appear throughout VerifiedNet — evidence records, manifests, dataset
provenance, approval digests. If two runs serialize the same logical object to different
bytes, their hashes diverge and reproducibility comparison breaks. The design also needs
identifiers that are stable across runs and platforms, so repeatability tests can compare
records without being defeated by random UUIDs or wall-clock timestamps.

## Decision

Define exactly **one** canonical JSON serializer (`common/canonical.py`) used for every
content hash: UTF-8, sorted keys, `(",", ":")` separators, UTC-`Z` datetimes (naive
datetimes rejected), enum values, stringified IP objects, sets as sorted lists, floats via
shortest round-trip repr, and NaN/Infinity rejected. Nothing else in the codebase may hash
JSON another way. Identifiers are **deterministic**: content-derived (`ev-<sha256[:16]>`)
or `RunContext` sequence-derived — never random UUIDs, never timestamp-derived.
`RunContext` is the single authority for `run_id`, monotonic sequence numbers, clock
access, and inner ids; the clock is injected so tests are fully deterministic.

Volatile fields explicitly **excluded** from reproducibility comparisons: wall-clock
timestamps, `run_id`, compose project names, host paths, and durations. They are recorded
in artifacts but never fed into a hash that is compared across runs.

## Consequences

- Identical inputs produce byte-identical canonical JSON and identical hashes, on any host.
- The earlier ClosCall `_emit` evidence-id collision (`source:subject:metric:at` not
  unique) is fixed by deriving the id from a content hash.
- A property test asserts canonical determinism under key-order shuffling.

## References

- `../gate3/runtime_security.md` (determinism rules)
- `../gate2_5/architecture_validation.md` §13 (determinism risk table)
