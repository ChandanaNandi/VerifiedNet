# 0002 — Runtime execution split: process runner + adapter + permission grants

**Status:** Accepted (Gate 3; open questions resolved in Gate 2.5 §8)
**Date:** 2026-07-11

## Context

Four source repositories each grew their own `docker exec` wrapper, mostly with
`shell=True`, string commands, and no timeouts (the EVPN lab's `_run` interpolated
f-strings into a shell with no deadline). VerifiedNet needs one execution primitive that
is safe, testable without Docker, and unable to let a collector mutate a device.

## Decision

Split execution into three layers. (1) A low-level **process runner**
(`runtime/process.py`) — the only module allowed to import `subprocess` — that takes an
explicit argv list (never a shell string), a **mandatory** timeout, and a maximum output
size, and returns a typed result; no retries, no Docker awareness. (2) **Policies**
(`CommandPolicy`, `MutationCommandPolicy`, `TargetPolicy`) that allow-list binaries,
commands, and targets. (3) Two executors in **separate modules** —
`ReadOnlyExecutor` (given to collectors) and `MutationExecutor` (given only to faults) —
with a write-ahead transcript rule for mutations.

Resolved sub-decisions: timeout is mandatory at the runner call (no implicit default);
the aggregate run deadline lives in the caller/orchestrator (a `Budget`), not the runner;
`ExecStatus` has seven categories (`OK`, `DENIED_COMMAND`, `DENIED_TARGET`, `TIMEOUT`,
`TARGET_NOT_FOUND`, `NONZERO_EXIT`, `INTERNAL_ERROR`) plus a `truncated` flag; a mutation
transcript entry is durably written *before* execution and a write failure blocks the
mutation; collectors may never receive a mutation-capable executor (enforced three ways:
constructor type, AST guard, runtime policy); the runner knows nothing about containers —
`docker exec …` is just argv composed by a lab adapter.

## Consequences

- Every test runs against a fake runner and a fake clock; no live services in Gate 3.
- Denied commands are recorded but never executed; unexpected exceptions propagate
  (nothing is silently swallowed).
- The one sanctioned `subprocess` call site is ruff/AST-pinned; new exec paths are
  rejected by CI.

## References

- `../gate3/runtime_security.md`
- `../gate2_5/architecture_validation.md` §8 (six open questions resolved)
- `../provenance/wave_a_provenance.md` rows 1–4
