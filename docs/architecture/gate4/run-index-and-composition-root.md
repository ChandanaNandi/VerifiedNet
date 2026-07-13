# Gate 4 Step 6 — Run Index and Composition Root

**Status:** IMPLEMENTED; offline-verified. A thin composition root
(`verifiednet.orchestrator`) assembles a completed run into the canonical
artifact directory (Step 5), records it in a deterministic, integrity-verifiable
**run index**, and can load any run back through that index. Two production entry
points execute the accepted and precondition-rejected live paths through ONE
shared composition function. This layer composes existing verified pieces; it
adds NO new authority. **No AI component determines ground truth. No model output
is stored, verified, or indexed as truth.** No general agent platform, planner,
scheduler, DAG/workflow engine, CLI, dashboard, or Gate 5 capability was added.

## What Step 6 adds (and only this)

Steps 1–5 produced, in memory then on disk, one accepted and one rejected
`IncidentRecord` and a self-contained, integrity-verified run directory. What was
still the *caller's* responsibility was: assembling the two manifests, driving a
run end to end, and keeping a durable catalogue of completed runs. Step 6 supplies
exactly that composition layer and nothing more.

Two new packages/modules:

- `verifiednet.artifacts.index` — the run index (low-level persistence, a sibling
  of the Step 5 writer/reader/verifier). It imports only schemas, common, and the
  other artifact modules; it never imports live execution, labs, or the
  orchestrator.
- `verifiednet.orchestrator` — the composition root: `manifests.py` (map observed
  values into `RunManifest`/`EnvironmentManifest`), `assembly.py` (one-call
  persist → verify → index → load-back), and `live_run.py` (the two live entry
  points).

A shared durability helper (`artifacts.durable.atomic_write_bytes`) was extracted
so the index and the Step 5 writer use one audited atomic-write path.

## The run index

An index root holds one `index.json` plus one subdirectory per run
(`<index_root>/<run_id>/`, the Step 5 layout). `index.json` is canonical JSON,
atomically written, and every entry is added ONLY after its run directory
independently verifies.

Each `RunIndexEntry` (Pydantic v2, frozen, `extra="forbid"`, schema-versioned)
records `run_id`, `incident_id`, `scenario_id`, `template_id`,
`acceptance_status` (accepted|rejected), `run_dir` (relative, == `run_id`),
`run_digest`, `topology_hash`, `layout_schema_version`, `started_at`,
`finished_at`. Validators reject an unsafe/absolute `run_dir`, an unsafe `run_id`,
and any non-64-hex digest.

**Digest rule (non-recursive).** `index_digest` = `sha256_canonical` of the
`run_id`-sorted list of `{run_id, run_dir, run_digest, status, topology_hash}`.
It hashes the entries only, never itself — the same no-self-hashing discipline as
the Step 5 run digest. It is order-independent (sorted) and stable for fixed
inputs.

**Adding a run** loads and verifies the whole run directory first (or raises),
refuses an `.INCOMPLETE` run, rejects a duplicate `run_id`, then atomically
rewrites `index.json`. A duplicate `incident_id` is ALLOWED and expected:
incident ids are content-derived, so two runs of the same accepted scenario
legitimately share one — `run_id` is the unique key.

**Verifying the index** re-parses it, recomputes and checks `index_digest`,
re-checks the canonical bytes on disk, and then re-verifies every referenced run
directory and confirms each stored `run_digest` still matches. It additionally
flags any **unindexed** run directory (a child directory carrying a `hashes.json`
that no entry references) — a hidden run is a failure, not a silent omission. The
result is a structured `RunIndexVerificationResult` (never a bool).

**Loading through the index** verifies the whole index, resolves exactly one
entry for the requested `run_id`, re-verifies that run directory, confirms the
digest matches the indexed value, and only then returns the typed `LoadedRun`. It
never executes a command, contacts Docker, or writes.

## The composition root

`assemble_verified_run(...)` is the one-call assembly: build both manifests →
`write_run_artifacts` (Step 5) → `verify_run_dir` independently → `add_run_to_index`
→ `verify_run_index` → `load_verified_run_from_index` → return typed paths,
digests, and the loaded models. It owns NO Docker and NO fault execution; the live
runner calls it only after a run reaches a valid terminal outcome.

`manifests.py` maps already-observed values into the released schemas. It does not
shell out or query Docker; the caller passes backend environment metadata, git
commit, lock hash, and timestamps. Required environment keys are enforced
(`os_name`, `kernel`, `arch`, `python_version`, `container_runtime`, plus
`image_reference`); an absent optional value (e.g. `frr_version`) is left `None`,
never invented. When no randomness exists, `seeds` defaults to an empty mapping.

`live_run.py` holds the two production entry points. Each owns exactly one run:

```
start lab → wait for healthy convergence → execute ONE approved path
  → build the incident record → (finally: restore if injected, stop backend)
  → assemble + index + verify the run artifacts.
```

`run_accepted_incident` runs the full remote-AS-mismatch lifecycle
(precondition → inject → onset → restore → recovery) with a nested `try/finally`
that guarantees restoration-if-injected and backend teardown even on failure. It
builds an accepted record ONLY when the ledger reaches `RECOVERY_VERIFIED` with
every verdict committable; otherwise it raises `LiveRunError`.
`run_precondition_rejected_incident` runs the healthy-lab rejection: it asserts
zero mutation on the wire and a ledger left at `PENDING`, then assembles. Both
call `assemble_verified_run` AFTER teardown, so the persisted transcript/ledger
snapshots are complete.

This module is the ONLY place that composes the live backend, a scenario, and the
artifact assembly. It performs NO natural-language planning, NO dynamic fault
selection, NO retries, NO parallelism, NO scheduling, and NO model invocation.

## Boundary enforcement

The AST security guard gains one rule: the composition root is the top, so **no
lower package may import `verifiednet.orchestrator`** (the dependency arrow only
points down into it). A deliberately-violating fixture
(`labs_imports_orchestrator.py`) proves the guard fires, a self-test confirms the
orchestrator may import itself, and a real-tree scan confirms no `src` package
below the root imports it. This is the concrete Gate 4 realization of ADR-0013.

## Offline verification (this host)

The whole offline gate is green on this container: `ruff` clean, `mypy` clean
(69 source files), **440 offline tests passed, 22 live tests skipped** (no Docker
here). New offline coverage:

- Run index: add/load round-trip, digest determinism + order-independence, verify
  after two adds (run-id ordering), duplicate `run_id` rejected, tampered
  `index_digest` fails, tampered run payload fails, unindexed run directory
  reported, unknown `run_id` raises, path-traversal `run_dir` rejected at
  validation, load-through-tampered-index raises `ArtifactIntegrityError`.
- Manifests: transcript-hash determinism + order-sensitivity, incident→manifest
  mapping, environment-manifest field mapping, optional `frr_version` left `None`,
  each core key required.
- One-call assembly: write/verify/index/load; determinism for identical inputs;
  two runs into one root; incomplete-metadata rejected.
- **Composition wiring (offline, no Docker):** a self-contained `FrrLabSim`
  process runner drives the REAL `run_accepted_incident` /
  `run_precondition_rejected_incident` end to end — proving the composition root
  assembles, verifies, indexes, and loads back a completed run, that the rejected
  path performs zero mutation and leaves the lab healthy, that an accepted and a
  rejected run share one verified index, and that tampering one indexed run makes
  the whole index refuse to verify.

## Live verification (canonical host)

The Gate 4 closure run executed the full integration tier on the canonical host
(macOS/arm64; Docker 29.1.3 / Compose 2.40.3-desktop.1; pinned
`frrouting/frr:v8.4.1@sha256:0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e`;
FRR 8.4.1_git) against the Step 6 tree on baseline `820a069`:

```
22 integration tests passed, 440 deselected, in 59.29s.
```

Both production entry points ran through real FRR. `run_accepted_incident`
produced an accepted, indexed, reload-verified run (ledger `RECOVERY_VERIFIED`,
mutation transcript paired, `router_b` never mutated).
`run_precondition_rejected_incident` produced a rejected run with zero mutation on
the wire and a `PENDING` ledger. The shared-index live test drove one accepted and
one rejected run into ONE index, loaded each back through it, confirmed distinct
run digests, and confirmed the index refuses to verify after a single persisted run
is tampered. Independent host-side checks confirmed zero `vnet-*` containers and
zero `vnet-*` networks remained after teardown.

## Limitations and explicit non-actions

Single reference host (macOS/arm64). The index and run digests are format- and
content-deterministic, not cross-run byte-identical (real wall-clock timestamps).
The composition root drives exactly the two Gate 4 paths on the two-router lab; it
is NOT a general orchestrator. No agent, planner, scheduler, DAG/workflow engine,
event bus, CLI, dashboard, database, model invocation, remediation approval, or
Gate 5 capability was added. Artifacts and the index persist ALREADY-VERIFIED
outcomes; they never create truth, and no model output is stored as ground truth.
