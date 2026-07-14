# 0024 — Training execution is event-sourced, deterministic, and simulated before it is real

**Status:** Accepted (owner decision, Gate 10C)
**Date:** 2026-07-14

## Context

ADR-0023 made training runs fully described before any execution exists. The
next danger is execution management itself: the moment a run can start, fail,
be cancelled, retried, or resumed, an unguarded orchestrator accumulates
untestable state — ad-hoc status flags, wall-clock ordering, silent in-place
retries — and the record of what actually happened becomes unauditable exactly
when it matters most (a failed run). Execution management must be built and
proven while execution is still simulation, so its correctness never depends
on GPUs, frameworks, or timing.

## Decision

1. **Execution is a state machine with a closed transition table.**
   `PLANNED → VALIDATED → STARTING → RUNNING → COMPLETED`, with
   `RUNNING → FAILED`, `RUNNING → CANCELLED`, and
   `FAILED → RESUMED → RUNNING`. The table is exhaustive; anything not listed
   is illegal and unrepresentable (events validate their transition at parse
   time). `COMPLETED` and `CANCELLED` are terminal; `FAILED` is final for the
   artifact but resumable by a new execution.

2. **The execution record is an ordered, hash-chained event log — no
   timestamps.** Ordering is sequence numbers plus a hash chain seeded by the
   execution id (a log cannot be grafted onto another execution). Progress is
   epoch/batch/optimizer-step events with cumulative counts; wall-clock time,
   durations, and hostnames never appear, so identical runs are byte-identical.

3. **A deterministic execution's log is REPLAYABLE from its header — and is
   verified by replay.** Given the planned counts, resume offset, final state,
   and completed steps, the entire expected event sequence is re-derivable.
   The model validator replays that skeleton and compares frame-by-frame:
   a dropped, duplicated, reordered, or edited event fails at parse time.
   Verification recomputes; it never trusts stored derived values.

4. **A retry is a NEW execution, never an in-place mutation.** The execution
   id derives from the training plan id, trainer capability id, execution
   policy id, and retry number — changing the retry count changes the
   identity. A resume begins with the explicit `failed → resumed → running`
   transition, binds the previous execution id and its completed-step count,
   and continues exactly where the failure stopped (proven property: failed
   progress + resumed progress equals the uninterrupted run's progress,
   for every possible failure point). Retry policy is fail-closed:
   `allow_resume` and `max_retries` are content-addressed policy, and one
   execution identity has exactly one authoritative outcome on disk.

5. **Failure and cancellation are explicit scripts, never accidents.** The
   only engine in this gate, `FakeExecutionEngine`, simulates completion,
   failure, cancellation, and resume by pure arithmetic — no randomness, no
   timing, no sleeping, no spontaneous outcomes. Every terminal event carries
   a deterministic reason.

6. **Gate 10C executes nothing real and can prove it.** Executions are
   Literal-locked `simulated=True` with the fake engine id; no ML framework is
   imported (AST-enforced and import-trapped through the full lifecycle);
   the pipeline succeeds with subprocess, process runner, network, and
   inference backends sabotaged; execution artifacts contain only bookkeeping
   files; and Gates 6–10B artifacts stay byte-identical (proven by
   fingerprint).

## Consequences

- A future real trainer backend slots in UNDER a proven lifecycle: states,
  events, resume, retry, and verification are already correct, so the real
  backend adds only genuine execution — and its honest nondeterminism will be
  a recorded claim, not an excuse for an unauditable log.
- Post-mortems of failed runs read a verified, replayable event log instead of
  scraping scheduler output.
- The cost: the deterministic replay check applies only to `deterministic`
  claims; a real backend will need a relaxed (but still explicit) consistency
  discipline for genuinely nondeterministic progress. That relaxation must be
  designed, not inherited silently.

## References

- `../gate10/training-execution.md`
- ADR-0023 (training runs are planned before executed), ADR-0022 (training
  data is train-only), ADR-0009/0010 (deterministic ground truth; models are
  never ground truth)
