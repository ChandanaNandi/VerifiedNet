# Gate 4 Step 5 — Canonical Per-Run Artifact Directory

**Status:** IMPLEMENTED AND LIVE-VERIFIED. One accepted run and one rejected run
each persist to a self-contained, integrity-verified, offline-replayable
directory. Artifacts persist verified outcomes; they do NOT create truth. No
model output is stored as ground truth. No general orchestrator, no CLI, no
Gate 5 capability was added. Live transient run directories are not committed.

All numbers below are observed on the canonical host (source commit `8256a24`,
Docker 29.1.3 / Compose 2.40.3, pinned `frrouting/frr:v8.4.1@sha256:0f8c174d…`).

## Directory layout

```
runs/<run_id>/
├── layout.json                 # declared structure (schema v1)
├── incident.json               # the IncidentRecord (canonical JSON)
├── run_manifest.json
├── environment_manifest.json
├── transcript.jsonl            # one canonical JSON line per TranscriptEntry
├── ledger.jsonl                # one canonical JSON line per LedgerRecord
├── evidence/
│   ├── baseline.json
│   ├── onset.json              # accepted only
│   └── recovery.json           # accepted only
├── hashes.json                 # ArtifactHashIndex + run_digest (META)
└── verification_report.json    # ArtifactVerificationResult (META)
```

A precondition-rejected run has `evidence/baseline.json` only and empty
`transcript.jsonl` / `ledger.jsonl`. Evidence files are derived from the
`IncidentRecord`'s own sealed bundles, so a rejected run cannot gain fabricated
onset/recovery files. The directory name IS `run_id`; there are no hidden files
required for correctness and no machine-random names.

## Schema versions

`layout_schema_version = 1`; `ArtifactHashIndex`, `RunLayout`,
`ArtifactVerificationResult`, `ArtifactEntry`, `ArtifactHash`, `CheckOutcome` are
all Pydantic v2, frozen, `extra="forbid"`, versioned, with relative-path-only and
lowercase-hex-SHA-256 validation.

## Durability per file type

Canonical JSON (`layout`, `incident`, both manifests, `evidence/*`, `hashes`,
`verification_report`): canonical bytes → temp sibling → flush → `fsync` →
`os.replace` → parent-dir fsync. JSONL (`transcript`, `ledger`): the complete
in-memory history is written to a temp file and atomically installed —
**finalization**, explicitly NOT live write-ahead appending (the runtime owns
write-ahead during execution). A `.INCOMPLETE` marker exists throughout
construction and is removed ONLY after independent post-write verification
passes; readers refuse any directory that still carries it.

## Hash index and run digest

`hashes.json` lists every truth-bearing file's relative path, role, SHA-256 and
size (excluding the two META files). The **run digest** is `sha256_canonical` of
the path-sorted list of `{path, sha256, size, role}` over those entries —
excluding `hashes.json` and `verification_report.json`, so there is no recursive
self-hashing. It is stable for byte-identical fixed inputs, changes when any
truth-bearing file changes, and is included in the verification result.

Honesty about determinism: the *format* is deterministic and content integrity is
guaranteed, but two different live runs do NOT share a whole-directory digest —
they carry real wall-clock timestamps. Offline byte-identical determinism is
proven with fixed-clock inputs; live runs are proven integrity-consistent, not
byte-identical to each other.

## Integrity verifier

`verify_run_dir` returns a structured `ArtifactVerificationResult` (never a bool)
whose checks include: layout/index parse; dir-name == run_id; per-file hash + size;
run-digest recomputation; no unindexed file (except META); canonical
re-serialization of every JSON/JSONL payload; scenario_id / template_id /
topology_hash / incident_id cross-links to the manifest and topology; ground-truth
evidence resolution (accepted) and precondition evidence resolution (rejected);
transcript seq validity + monotonicity; an env-dump guard on recorded argv;
mutation pending/completed pairing by `command_id` (accepted) and zero-mutation
(rejected); ledger legal transitions; and final-phase rules
(accepted → `RECOVERY_VERIFIED`, rejected → `PENDING`). It never mutates or repairs.

## Reader / replay

`load_run` refuses `.INCOMPLETE`, verifies every hash and rule before returning a
typed `LoadedRun`, and never executes a command, contacts Docker, or writes. A
unit test proves a full load with `subprocess.run`/`Popen` sabotaged.

## Accepted-run consistency rules

Ground truth present; baseline + onset + recovery evidence present and sealed;
`ground_truth.accepted_evidence_ids` all resolve to written evidence; mutation
pending/completed pair by `command_id` with no unmatched pending; ledger ends at
`RECOVERY_VERIFIED`; acceptance_status == "accepted".

## Rejected-run consistency rules

No ground truth; baseline evidence only; the precondition verdict's evidence id
resolves in the persisted baseline; zero mutation transcript entries; ledger has
zero records (final phase `PENDING`); acceptance_status == "rejected".

## Live results (instrumented)

```
ACCEPTED : 11 files  transcript=118  ledger=7  evidence=[baseline,onset,recovery]  verify=True
           tamper incident.json -> verify=False
REJECTED :  9 files  transcript=22   ledger=0  evidence=[baseline]                 verify=True
           tamper incident.json -> verify=False
```

Both integration tests write the run directory under the test `tmp_path`, verify
it independently, load it back, and assert the loaded `IncidentRecord` equals the
original; teardown and zero-resource cleanup still succeed. Full integration tier:
21 passed; zero containers/networks remained.

## Tamper-detection coverage (offline)

Detected: tampered incident / evidence / transcript / ledger; missing indexed
file; extra unindexed file; `.INCOMPLETE` present; wrong directory name; tampered
run digest; unsafe/absolute path; existing-target collision; accepted-missing-
recovery; rejected-with-mutation-transcript; unmatched mutation pending; illegal
ledger transition; run_id/incident mismatch. On writer failure the `.INCOMPLETE`
marker is preserved and no verification report is written.

## `.INCOMPLETE` semantics

Present during construction; removed only after successful independent
verification. Readers refuse it; the verifier reports it as a failed check
(`no_incomplete_marker`) in post-hoc mode and tolerates it in during-write mode.

## Limitations

Single reference host (macOS/arm64). The run digest is not cross-run byte-stable
by design (real timestamps). Assembling manifests and driving a run remain the
caller's responsibility; a general orchestrator/CLI is deliberately later work.

## Explicit non-actions

Artifacts persist verified outcomes; they do not create truth. No model output is
stored as ground truth. No general orchestrator exists yet; no CLI exists yet; no
Gate 5 capability was added; live transient run directories are not committed.
