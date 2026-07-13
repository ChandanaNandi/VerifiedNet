# 0017 — Gate 4 composition root and run index

**Status:** Accepted (owner decision, Gate 4 Step 6)
**Date:** 2026-07-13

## Context

Gate 4 Steps 1–5 produced live accepted and rejected `IncidentRecord`s and a
self-contained, integrity-verified per-run artifact directory (ADR-0016). Three
responsibilities were still the caller's: assembling the two manifests, driving a
run end to end, and keeping a durable catalogue of completed runs. ADR-0013
already designates `verifiednet.orchestrator` as the platform composition root
for the long term; Gate 4 needs the concrete, minimal realization of that
boundary — and nothing resembling the future agent harness.

## Decision

**Run index (low-level persistence).** A new `verifiednet.artifacts.index` module
maintains an integrity-verifiable index of completed runs. An index root holds one
canonical, atomically written `index.json` plus one Step-5 run directory per run
(`<index_root>/<run_id>/`). Each `RunIndexEntry` is Pydantic v2, frozen,
`extra="forbid"`, schema-versioned, with unsafe/absolute-path, unsafe-`run_id`,
and 64-hex-digest validators. The `index_digest` is `sha256_canonical` of the
`run_id`-sorted list of `{run_id, run_dir, run_digest, status, topology_hash}` —
non-recursive (it never hashes itself), the same discipline as the Step-5 run
digest. A run is added ONLY after its directory independently verifies; an
`.INCOMPLETE` run is refused; a duplicate `run_id` is rejected while a duplicate
`incident_id` is ALLOWED (content-derived ids; `run_id` is the unique key).
Verifying the index re-checks its digest and canonical bytes, re-verifies every
referenced run and its `run_digest`, and flags any unindexed run directory (a
hidden run is a failure, not a silent omission). Loading through the index
verifies the index, resolves exactly one entry, re-verifies that run, confirms
the digest, and returns a typed `LoadedRun` — never executing a command,
contacting Docker, or writing. This module imports only schemas, common, and the
sibling artifact modules; it never imports live execution, labs, or the
orchestrator.

**Composition root.** `verifiednet.orchestrator` is the thin Gate 4 composition
root. `assemble_verified_run` performs one-call persist → independent
`verify_run_dir` → `add_run_to_index` → `verify_run_index` →
`load_verified_run_from_index`, returning typed paths, digests, and loaded models;
it owns no Docker and no fault execution. `manifests.py` maps already-observed
values into `RunManifest`/`EnvironmentManifest`, enforcing required environment
keys and never inventing an absent optional value. `live_run.py` holds the two
production entry points — `run_accepted_incident` and
`run_precondition_rejected_incident` — each owning one run: start lab → await
healthy convergence → execute ONE approved path → build the record → (finally:
restore-if-injected, stop backend) → assemble + index + verify. An accepted
record is built ONLY when the ledger reaches `RECOVERY_VERIFIED` with every
verdict committable; the rejected path asserts zero mutation and a `PENDING`
ledger. Assembly happens after teardown so the persisted snapshots are complete.

**Boundary.** The composition root is the top of the dependency graph. The AST
security guard gains one rule: no `src` package below the root may import
`verifiednet.orchestrator`. It is proven by a violating fixture, an
orchestrator-may-import-itself self-test, and a real-tree scan. (When a `cli`
package is introduced in a later gate it becomes the one permitted importer, per
ADR-0013; no `cli` exists in Gate 4.)

**No agent platform.** This root performs no natural-language planning, no dynamic
fault selection, no retries, no parallelism, no scheduling, and no model
invocation. It is deliberately NOT a DAG engine, workflow engine, event bus,
scheduler, or plugin layer.

A shared `artifacts.durable.atomic_write_bytes` helper was extracted so the index
and the Step-5 writer share one audited atomic-write path (temp → flush → fsync →
`os.replace` → parent-dir fsync).

## Consequences

- Completed runs are discoverable and reload-verified through a single durable
  index; a tampered or hidden run makes the whole index refuse to verify.
- The dependency graph stays acyclic with a single, obvious, AST-enforced top.
- The composition layer persists ALREADY-VERIFIED outcomes; it creates no truth,
  and no model output is stored, indexed, or verified as truth.
- Format and content are deterministic; whole-index byte identity across two live
  runs is NOT (real wall-clock timestamps) — documented, not hidden.
- Driving a run is now a single call, but only the two Gate 4 paths exist; a
  general orchestrator/CLI remains later work.

## References

- `../gate4/run-index-and-composition-root.md`, `../gate4/gate4-completion-report.md`
- ADR-0013 (orchestrator and agent boundaries), ADR-0016 (canonical run artifact
  directory), ADR-0003 (canonical JSON and ids), ADR-0009/0010 (ground truth is
  model-free)
