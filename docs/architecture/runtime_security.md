# Runtime Execution and Security Model (Gate 3)

## Execution split (Gate 2 §7, validated Gate 2.5 §8)

1. **Low-level process runner** (`runtime/process.py`) — the only module in the
   tree allowed to import `subprocess`. Explicit argv lists only (str argv is a
   TypeError); `shell=True` never used (AST-banned tree-wide); timeout mandatory
   (`timeout_s <= 0` rejected); output truncated to `max_output_bytes` with a
   `truncated` flag; no retries; no Docker awareness (a `docker exec …` argv is
   just data composed by a Gate 4 adapter).
2. **Policies** (`runtime/policy.py`) — `CommandPolicy` (read): binary allow-list,
   vtysh show-only rule, forbidden-token ban, shell-metacharacter rejection
   (adapted from NeuroNOC's `_assert_show_command`). `MutationCommandPolicy`:
   explicit vtysh command-sequence templates (remote-as revert, clear bgp) —
   everything else denied. `TargetPolicy`: allowed targets come from topology.
3. **Two executors** (separate modules, AST-relevant):
   - `ReadOnlyExecutor` (`runtime/readonly.py`) — what collectors receive.
   - `MutationExecutor` (`runtime/mutation.py`) — what only faults receive;
     collectors are AST-banned from even importing this module.

## Transcript rules

- Mutation commands: transcript entry durably written (flush+fsync) **before**
  execution; a write failure raises and blocks the mutation (write-ahead pattern,
  reimplemented from the closcall guarded-mutation specification).
- Read commands: transcript failure never blocks the read but marks the result
  `transcript_ok=False`; downstream, an incomplete transcript rejects the run
  (`RejectionCode.TRANSCRIPT_INCOMPLETE`).
- Ordering: transcript entries carry RunContext sequence numbers — run-level
  ordering never depends on wall clocks.

## ExecResult taxonomy

`OK, DENIED_COMMAND, DENIED_TARGET, TIMEOUT, TARGET_NOT_FOUND, NONZERO_EXIT,
INTERNAL_ERROR` + `truncated` flag + `transcript_ok` flag. Denials are recorded
without executing. Unexpected exceptions propagate — nothing is silently swallowed.

## AST security boundary

One consolidated, policy-driven guard (replacing the three per-package copies in
the NeuroNOC source): package import rules (see package_boundaries.md), the
subprocess single-call-site rule, tree-wide `shell=True` and `os.system` bans.
The guard validates itself against six deliberately violating fixture modules.
Known limitation (accepted, documented): AST scanning cannot see dynamic imports;
runtime policies are the second layer of defense.

## Determinism rules

- All content hashes go through canonical JSON (UTF-8, sorted keys, `","`/`":"`
  separators, UTC-Z datetimes, enum values, stringified IP objects, sets as sorted
  lists, NaN/Infinity rejected, floats via shortest round-trip repr).
- Identifiers are content-derived (`ev-<sha256[:16]>`) or RunContext-sequence
  derived — never random UUIDs, never wall-clock-derived.
- Volatile fields excluded from reproducibility comparisons: wall-clock
  timestamps, run_id, compose project names (Gate 4), host paths, durations.
- Clocks and sleeps are injected everywhere; tests use a fake monotonic clock.
- Onset confirmation requires two consecutive successful polls; reachability
  requires 3/3 probe successes (the 4/15 floor from the EVPN lab source is
  explicitly rejected).
