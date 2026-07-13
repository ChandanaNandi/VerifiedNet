# 0016 — Canonical per-run artifact directory: durability + integrity contract

**Status:** Accepted (owner decision, Gate 4 Step 5)
**Date:** 2026-07-12

## Context

Gate 4 Steps 1–4 produced live accepted and rejected `IncidentRecord`s in
memory. A run must also be persistable as a self-contained, integrity-verifiable,
offline-replayable record. This needs a durable on-disk contract that is not owned
by the live-execution layer and that never becomes a place where model output
could enter the truth chain.

## Decision

A new low-level `verifiednet.artifacts` package owns per-run persistence. It
imports only `verifiednet.schemas`, `verifiednet.common`, and the PURE data
models for transcript (`runtime.transcript.TranscriptEntry`,
`runtime.invocation`, `runtime.results`) and ledger (`faults.ledger`). It must
NOT import live execution (runtime executors/process), labs, collectors,
verifiers, incident builders/oracle, or scenario implementations — AST-enforced
with a violating fixture.

**Layout.** One run per directory; the directory name IS `run_id`. Fixed files:
`layout.json`, `incident.json`, `run_manifest.json`, `environment_manifest.json`,
`transcript.jsonl`, `ledger.jsonl`, `evidence/{baseline,onset,recovery}.json`,
`hashes.json`, `verification_report.json`. Absent phases are absent (never faked):
a precondition-rejected run has `evidence/baseline.json` only, and empty
`transcript.jsonl`/`ledger.jsonl`. Evidence files are derived from the
`IncidentRecord`'s own sealed bundles (single source of truth), so a rejected run
cannot gain fabricated onset/recovery files.

**Durability.** Canonical JSON files are written atomically: canonical bytes →
temp sibling → flush → `fsync` → `os.replace` → parent-dir fsync. JSONL files are
FINALIZED (the complete in-memory history is written to a temp file and atomically
installed) — explicitly NOT live write-ahead appending; the runtime owns
write-ahead during execution. A `.INCOMPLETE` marker exists throughout
construction and is removed only after independent post-write verification
succeeds. On any failure the marker stays and the run is never reported complete.

**Integrity.** `hashes.json` lists every truth-bearing file's SHA-256 + size +
role (excluding the two META files: `hashes.json`, `verification_report.json`).
The **run digest** is `sha256_canonical` of the path-sorted list of
`{path, sha256, size, role}` over those entries — no recursive self-hashing. The
verifier returns a structured `ArtifactVerificationResult` (not a bool) covering
hash/size/digest integrity, no-unindexed-file, canonical re-serialization,
scenario/template/topology-hash/incident-id cross-links, evidence resolution,
transcript seq monotonicity + mutation pairing, ledger legal transitions, and
final-phase rules (accepted → `RECOVERY_VERIFIED`, rejected → `PENDING`). The
reader refuses `.INCOMPLETE`, verifies before returning, and never executes
commands, contacts Docker, or mutates files.

**Honest evidence linkage.** An accepted run's verification *verdicts* are
computed over transient per-poll evidence, so their ids are not the persisted
bundles; the authoritative verified link is `GroundTruth.accepted_evidence_ids`
(which references the persisted bundles). A precondition-rejected run's single
verdict IS computed over the persisted baseline, so its evidence id is checked to
resolve there.

## Consequences

- Deterministic *format* and content integrity are guaranteed; whole-directory
  byte identity across two live runs is NOT (real wall-clock timestamps differ) —
  this distinction is documented, not hidden.
- Artifacts persist ALREADY-VERIFIED outcomes; they do not create truth and store
  no model output as ground truth.
- The package is orchestrator- and CLI-free; assembling manifests and driving a
  run remain the caller's responsibility (later gates).

## References

- `../gate4/canonical-run-artifacts.md`
- `0003-canonical-json-and-ids.md`, `0009-ground-truth-no-model-output.md`
