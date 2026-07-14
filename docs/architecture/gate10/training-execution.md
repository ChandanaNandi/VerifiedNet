# Gate 10C — Deterministic Training Execution Framework (Simulation Only)

**Status:** IMPLEMENTED (Gate 10C). This document describes the execution half
of `verifiednet.training` — how a verified Gate 10B plan is executed, observed,
interrupted, resumed, and persisted as an immutable, replayable event log. It
implements ADR-0024. **No real training occurs in Gate 10C**: execution happens
only through the deterministic simulator — no torch/transformers/PEFT, no
gradients, no optimizer or scheduler execution, no model/tokenizer loading, no
checkpoints, no randomness, no timing, no sleeping.

## 1. Why execution management is its own gate

```
TrainingPlan (Gate 10B, verified)
        ↓
FakeExecutionEngine.execute(plan, policy [, scripted failure/cancellation])
        ↓
TrainingExecution  (hash-chained event log, replay-validated)
        ↓
training-executions/<execution_id>/   (immutable)
        ↓                                   ↑
FakeExecutionEngine.resume(failed, plan) — a NEW execution, retry_number + 1
```

Once runs can start, fail, and retry, the orchestration itself becomes a
correctness surface: which transitions are legal, what the authoritative record
of a run is, what a resume may claim. Gate 10C builds that machinery while
execution is still pure arithmetic, so every lifecycle rule is proven before a
real backend exists (Gate 10E) and before checkpoints exist (Gate 10D).

## 2. Execution lifecycle

`ExecutionState` with a CLOSED transition table (`LEGAL_TRANSITIONS` — anything
not listed is illegal and unrepresentable):

```
PLANNED → VALIDATED → STARTING → RUNNING → COMPLETED
                                 RUNNING → RUNNING     (progress events)
                                 RUNNING → FAILED
                                 RUNNING → CANCELLED
                      FAILED → RESUMED → RUNNING       (a NEW execution)
```

`COMPLETED`/`CANCELLED` are terminal (no outgoing transitions). `FAILED` is
final for its artifact but resumable by a new execution. An execution artifact
may only end in `FINAL_STATES = {completed, failed, cancelled}`.

## 3. Events: ordered, hash-chained, no timestamps

`ExecutionEvent` carries: sequence number, event type, the explicit
`state_before → state_after` transition, optional epoch/batch/step indices,
the cumulative `completed_steps`, a deterministic `detail`, and the hash chain
(`prev_event_hash → event_hash`). Event 0 chains from the execution id itself,
so a log cannot be grafted onto another execution. There are NO timestamps
anywhere: ordering is sequencing + chaining, which is exactly why identical
runs are byte-identical. Event types:

`execution_validated`, `execution_starting`, `execution_started`,
`batch_completed`, `optimizer_step_completed`, `epoch_completed`,
`execution_completed`, `execution_failed`, `execution_cancelled`,
`execution_resumed` — each restricted (`EVENT_TRANSITIONS`) to the transitions
it may carry; an event with a legal transition but the wrong type fails to
parse.

## 4. Execution identity

```
execution_id = "trainexec-" + sha256_canonical({
    training_plan_id, trainer_capability_id, execution_policy_id, retry_number
})[:16]
```

`ExecutionPolicy` (`execpol-…`, self-validating) is the content-addressed
retry contract: `max_retries` (0–8) and `allow_resume` (retries without resume
are rejected as incoherent). Changing the retry number changes the execution
id: **a retry is a different execution**, with its own artifact — never an
in-place mutation. Consequence, proven by test: one execution identity has
exactly ONE authoritative outcome on disk; a second, conflicting outcome for
the same attempt is refused by the store.

## 5. Replay verification (the core mechanism)

Because the simulator is deterministic, an execution's ENTIRE event log is a
pure function of its header: planned steps/batches/epochs, accumulation,
resume offset, final state, completed steps. `expected_event_frames` re-derives
that skeleton, and the `TrainingExecution` model validator compares it
frame-by-frame against the stored events — types, indices, cumulative counts,
transitions, details — plus sequence contiguity, the hash chain, the derived
epoch count, the execution id, and the execution digest
(`execdig-…`, bound to the header and the ordered event hashes). A dropped,
duplicated, reordered, or edited event — even one with internally consistent
hashes and a fixed-up manifest — fails at parse time.

## 6. The fake execution engine

`FakeExecutionEngine` (`fake-execution-engine-v1`, Literal-locked on the
execution record) simulates epoch/batch/optimizer-step progress by exact
integer arithmetic over the plan's counts (partial windows flush; a step-budget
run may stop mid-epoch, completing no further epoch). Failure and cancellation
are EXPLICIT scripts — `fail_after_step` / `cancel_after_step`, validated to
lie strictly inside the run — so no outcome is ever spontaneous, and every
terminal event carries a deterministic reason
(`simulated_failure_after_step_N`). Both scripts together are rejected as
ambiguous.

**Resume semantics.** `resume(failed, plan)` requires: the previous execution
actually FAILED (completed/cancelled are not resumable), the policy allows
resume, `retry_number + 1 ≤ max_retries`, and the SAME training plan. The new
execution begins with the explicit `failed → resumed → running` transition,
binds `resumed_from_execution_id` and `resumed_from_completed_steps`, and
continues at exactly the next optimizer step. Property, proven for every
possible failure point: **failed-run progress + resumed-run progress equals
the uninterrupted run's progress — in order, no overlap, no gap.**

## 7. Immutable execution store

```
training-executions/<execution_id>/
    manifest.json    events.jsonl
```

`events.jsonl` is one canonical-JSON event per line, in order. The manifest
(`TrainingExecutionManifest`, self-validating `execstore-…` digest) embeds the
full header — policy, planned counts, resume binding, final state, progress
counts, event count, execution digest — so the reader reconstructs the complete
`TrainingExecution` from manifest + events and the model validator re-checks
everything. Writer discipline as everywhere: atomic writes under `.INCOMPLETE`,
post-write verification, overwrite refusal. `verify_training_execution` is
structured and fail-closed (directory/marker/manifest/schema/files/hashes/
event parse/replay/count/digest checks); `read_training_execution` verifies
first, then returns the typed artifact.

## 8. Guarantees proven by test

Closed transition tables (every state present; terminal states empty; every
event type maps only to legal transitions); illegal and type-mismatched
transitions unrepresentable; deterministic execution ids with plan/policy/
retry sensitivity; deterministic end-to-end execution (same inputs →
byte-identical artifacts, write-twice fingerprint equality); replay-verified
logs (drop/duplicate/reorder/edit all fail, including an attacker who fixes up
file hashes, event count, and store digest); scripted-failure validation;
resume consistency for EVERY failure point (property-tested), resume refusals
(not-failed, forbidden, retries exhausted, different plan), self-resume and
fresh-run-with-resume-link rejected; one authoritative outcome per identity;
step-budget mid-epoch stop derives no phantom epoch; store corruption
(corrupted log, tampered manifest digest, missing file, missing dir) fails
closed; no subprocess/network/Ollama (sabotaged through the full lifecycle);
no ML framework imports (AST boundary + meta-path import traps); execution
directories contain only bookkeeping files (never weights or checkpoints);
and Gates 6–10B artifacts (runs, dataset, prepared, evaluations, benchmarks,
training corpus, training plans) stay byte-identical under the full pipeline.

## 9. Simulation guarantees and limits

Everything in this gate is Literal-locked `simulated=True` under the fake
engine id — nothing can be mistaken for real training. The replay check is
valid precisely BECAUSE the engine claims `deterministic`; a real backend
(Gate 10E) will produce genuinely nondeterministic progress and will need an
explicit, relaxed consistency discipline (recorded claims, not replayed
skeletons) — that relaxation is a future design decision, not something to
inherit silently. Checkpoint artifact CONTRACTS now exist (Gate 10D,
implemented — see `checkpoint-artifact.md`, ADR-0025): a verified COMPLETED
execution is the only legal checkpoint source, and the fake producer binds its
lineage to this gate's execution artifacts. Real weights still do not exist.
Wall-clock time is deliberately absent and stays absent until an execution
layer genuinely needs it.
