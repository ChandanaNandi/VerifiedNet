# Gate 10D — Immutable Checkpoint Artifact and Lineage Contract

**Status:** IMPLEMENTED (Gate 10D). This document describes the checkpoint
layer of `verifiednet.training` — what a trained checkpoint IS, how it is
identified, stored, verified, and bound to its provenance. It implements
ADR-0025. **No real model checkpoint exists yet**: every payload in this gate
is fake, deterministic, and unmistakably simulated — no real training, no
model/tokenizer loading, no ML framework, no genuine weights.

## 1. Where the checkpoint sits

```
Training Corpus → Training Spec → Training Plan → Training Execution
                                                        ↓ (verified, COMPLETED)
                                      assess_checkpoint_eligibility
                                                        ↓
                            FakeCheckpointProducer → CheckpointCandidate (UNTRUSTED)
                                                        ↓
                                      write_checkpoint → verify_checkpoint
                                                        ↓
                                   checkpoints/<checkpoint_id>/  (immutable)
                                                        ↓
                          Future checkpoint-backed predictor → Evaluation → Benchmark
                                        (LATER gates — not integrated here)
```

Gate 10B's `checkpoint_policy="none"` is untouched: the TRAINER still writes
nothing during execution. Checkpoint production is a separate, post-execution
artifact operation over a verified completed execution.

## 2. Candidate versus verified checkpoint (the core distinction)

Three things, never conflated: the **format spec** (the declared contract),
the **candidate** (backend output, untrusted), and the **verified artifact**
(a persisted directory that passed verification). `CheckpointCandidate`
deliberately carries raw content and NO hash fields — candidate-supplied
integrity claims are unrepresentable; the writer recomputes every hash/size
from the bytes it actually writes and re-verifies the persisted artifact
before removing `.INCOMPLETE`. Instantiating a manifest model does not make a
checkpoint trusted; only `verify_checkpoint` over the artifact does.

## 3. Logical identity versus content digest

```
checkpoint_id  = "checkpoint-" + sha256_canonical({format_spec_id, lineage_id,
                  declared_file_roles, simulated, model_spec_id,
                  tokenizer_spec_id, checkpoint_version})[:24]
checkpoint_digest = "ckptdig-" + sha256_canonical({manifest config blocks,
                  path-sorted file entries: hash/size/role/serialization})[:24]
```

The logical id never depends on paths, machine-local metadata, or payload
bytes — it is lineage + declared shape. The digest is the verified content:
flipping ANY payload byte (property-tested for every byte position), or
changing any lineage/compatibility/format value, changes it. The manifest
self-validates both.

## 4. Lineage

`CheckpointLineage` (`ckptlin-…`, self-validating) binds: source execution id
AND digest, plan id AND plan digest, request id, training spec id, corpus id
AND digest, model spec id, tokenizer spec id, trainer implementation and
capability ids, execution policy id, retry number, and
`parent_checkpoint_id: None` — structurally absent in this gate. **Resumed
executions do not create checkpoint ancestry**: resume lineage already lives
inside the execution artifact (`resumed_from_execution_id`); a checkpoint from
a resumed-then-completed execution binds to that execution (retry number and
all) and invents no parent, because no prior checkpoint was consumed. Warm
starts, adapter continuation, and checkpoint-resume chains are a later gate's
explicit contract.

## 5. Compatibility contract

`CheckpointCompatibility` (`ckptcompat-…`) declares what may consume the
checkpoint: format spec id, model/tokenizer spec ids, architecture id, and a
metadata-only predictor adapter version. In Gate 10D it is Literal-locked
honest: `simulated_only=True`, `loadable_as_real_model=False`, and the
supported real inference backend list is locked EMPTY (max_length=0). Any
future API asked to load a Gate 10D checkpoint as a real model has nothing to
negotiate with — and no such API exists in this package.

**Update (Gate 11):** the checkpoint-backed predictor now exists for the REAL
format only (`verifiednet.real-checkpoint-v1`, Gate 10F). The Gate 10D fake
format is unchanged and remains structurally ineligible: its manifest cannot
even validate as a real-checkpoint manifest, so the Gate 11 eligibility
assessment fails closed on it. See `../gate11/checkpoint-predictor.md`.

## 6. File roles and safe paths

Every file: relative path, role, size, sha256, serialization id, required
flag. Exactly four roles exist in a Gate 10D artifact (checkpoint metadata,
fake model payload, model config metadata, tokenizer compat metadata), each
exactly once; resume/adapter roles are declared in the enum but FORBIDDEN by
the format spec. Paths must be canonical, forward-slash, non-absolute,
`..`-free, and under `payload/`; duplicates of path or role, undeclared
files, symlinks, and executable payloads are rejected. Entries are path-sorted
everywhere.

## 7. The fake producer and exact payload layout

`FakeCheckpointProducer` consumes ONLY a verified completed execution
artifact, its verified plan artifact, the fake format spec, and the explicit
production policy. Output layout:

```
checkpoints/<checkpoint_id>/
    manifest.json
    payload/
        checkpoint.json             (checkpoint metadata: ids, step count)
        config.json                 (model configuration METADATA only)
        model.fakebin               (magic + 256 deterministic synthetic bytes)
        tokenizer-metadata.json     (tokenizer compatibility METADATA only)
```

`model.fakebin` starts with `VERIFIEDNET-FAKE-CHECKPOINT-V1\n` followed by
counter-chained SHA-256 blocks seeded ONLY by content-addressed identities
(execution/plan/spec/model/tokenizer/format ids + completed step count).
Deliberately not a real weight extension; no randomness, timestamps, or host
data; and structurally no training rows, labels, example/group ids, trace
metadata, or evaluation data — none are inputs. Payload-scan tests prove no
rendered training input/target, example identity, or fault-family label
appears in any checkpoint byte (structural/exact-value absence under the
producer contract — cryptographic impossibility of encoded leakage is not
claimed).

## 8. Eligibility

`assess_checkpoint_eligibility(execution_dir, plan_dir, format_spec, policy)`
independently VERIFIES both artifacts (fail-closed read), then checks:
final state completed, execution↔plan binding, planned/completed counts match
the plan, retry within policy, format/policy compatibility, and (when writing)
no existing checkpoint for the same logical id. Failed and cancelled
executions are rejected; a corrupted execution directory fails verification
before its state string is ever consulted.

## 9. Manifest, writer, verifier, reader

The manifest embeds format spec, production policy, lineage, and
compatibility whole, plus file entries, counts, total bytes, and generator id
— deterministic metadata only (no timestamps, hostnames, usernames, devices,
process ids, absolute paths, or git state). Writer: `.INCOMPLETE` marker,
atomic canonical writes, recomputed hashes, post-write verification, no
overwrite. Verifier (structured, fail-closed): directory/marker/manifest/
schema/format checks, directory-name↔id match, missing/unexpected files,
symlinks, executables, per-file hash and size, total size, file count, fake
payload magic, and the independent lineage audit (`audit_checkpoint_lineage`
recomputes lineage/compatibility/checkpoint ids and the digest from primary
fields — closed even against `model_construct` bypass). Readers verify first
and return metadata plus payload DESCRIPTORS; `open_checkpoint_payload`
returns raw bytes of a declared file only. There is no `load_model`, no
tokenizer loading, no conversion, no upload.

## 10. Guarantees proven by test

Deterministic ids at every level; deterministic payload bytes (stable and
step-sensitive, property-tested); eligibility accepts completed (including
resumed-completed) and rejects failed/cancelled/corrupt/missing executions and
wrong plans; every lineage binding tamper fails at parse; candidate payload
without the fake magic is unrepresentable; the store corruption matrix fails
closed (byte flip, size change, unexpected/missing file, executable, symlink,
malformed manifest, tampered digest, overwrite, undeclared payload access);
duplicate logical checkpoints blocked; full pipeline under ML-framework import
traps and subprocess/network/inference sabotage; all eight upstream artifact
classes byte-identical before/after; evaluation/benchmark changes leave
candidate and artifact byte-identical; identity ripple across every lineage
field, format, policy, and a REAL execution change (retry); simulation-honesty
tampering (real loadability, safetensors, full-model, adapter, simulated
=false) rejected in every variant; build-twice byte-identical directories;
and the AST import boundary covers the three new modules.

## 11. What does not exist yet

A real checkpoint format, real weights, checkpoint-backed prediction,
warm-start/adapter lineage, checkpoint conversion/merging/upload, and any ML
framework integration in THIS gate. Gate 10E added the real backend
contract and preflight (`execution-preflight.md`, ADR-0026), and Gate 10F
(implemented — see `real-training.md`, ADR-0027) added the first genuine
checkpoint as its own NEW format spec (`verifiednet.real-checkpoint-v1`) —
this fake format is unchanged and still cannot claim real loadability.
