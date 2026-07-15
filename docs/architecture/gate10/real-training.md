# Gate 10F — First Bounded Real Training Execution

**Status:** IMPLEMENTED (Gate 10F). This document describes the first genuine
weight mutation in VerifiedNet — a controlled proof that the architecture can
safely cross from planning and authorization into real training. It implements
ADR-0027. The run is BOUNDED, optional, deselected from normal CI, and proves
execution and artifact integrity only — **no evaluation, no benchmark, no
quality claim of any kind exists in this gate.**

## 1. Why the first real run is bounded

Real training is the first operation that cannot be verified by replay: exact
losses, gradients, and weight deltas are not re-derivable. The mitigation is
to make everything AROUND the hot section maximally verified and the hot
section itself maximally small: one approved tiny model, one deterministic
corpus slice, a handful of optimizer steps, one checkpoint, no retries, no
resume, no cancellation. The architecture is the deliverable; the trained
weights are a byproduct.

## 2. Exact conditions required before execution

```
Verified Gate 10A corpus  +  Verified Gate 10B real-backend plan
+  Verified Gate 10E authorization (revalidated at the moment of use)
+  Local content-hashed model + tokenizer artifacts (never downloaded)
+  BoundedTrainingModelPolicy  (bmodel-…: exact identity, param ceiling)
+  BoundedCorpusSlicePolicy    (cslice-…: first-N canonical, ids recorded)
+  TrainingObjectivePolicy     (objpol-…: exact causal-LM objective)
+  RealTrainingExecutionPolicy (rexecpol-…: Literal-locked ceilings)
        ↓ every bound enforced BEFORE model loading
AuthorizedTrainingExecutor.execute(...)   ← authorization is REQUIRED;
        ↓                                   no bypass API exists
real-training-executions/<id>/  +  real-checkpoints/<id>/  (on completion)
```

Authorization revalidation re-verifies the stored artifact and re-checks:
authorized=True, plan id+digest, corpus id+digest, backend, model/tokenizer
content hashes unchanged, determinism category within the explicit acceptance
set, and every model-policy and execution-policy ceiling. Changed evidence
refuses; authorizations are never refreshed in place.

## 3. The approved bounded model and deterministic slice

`BoundedTrainingModelPolicy` pins the ONE permitted model: family, exact
immutable model and tokenizer revisions, architecture class, parameter-count
ceiling, sequence/example/epoch/step/batch ceilings, permitted devices, and
local-cache-only. An arbitrary model cannot be substituted without a new
policy id. `BoundedCorpusSlicePolicy` records the deterministic slice —
first-N in canonical Gate 10A order (never random, never balanced by outcome,
never informed by evaluation or benchmarks) — with the selected training-
example ids captured BEFORE training. The executor re-selects the slice and
refuses on any mismatch; changing the slice changes execution identity and
checkpoint lineage (property-tested).

## 4. Local-only resolution and the no-network guarantee

`LocalModelArtifactResolver` / `LocalTokenizerArtifactResolver` resolve
against local directories only: required files verified, symlinks refused,
deterministic content hashes over actual bytes, parameter count derived
STRUCTURALLY from the safetensors header (never from a model name). Absolute
paths locate artifacts but never become identity. The HF engine additionally
forces `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` and passes
`local_files_only=True`; a missing local file is a structured refusal, never
a download (tested with the network sabotaged).

## 5. The exact training objective

`TrainingObjectivePolicy` (identity-bearing, Literal-locked v1): serialize
`input_text + "\n" + target_text + EOS`; mask input-and-separator label
positions with −100 so loss covers ONLY target and the single trailing EOS;
pad right and mask padded labels; mean loss over unmasked positions; no chat
template. `build_causal_lm_example` is a pure integer function (tested
without any tokenizer); overlength examples fail closed per the Gate 10B
sequence policy. Optimizer/scheduler arguments come from the plan EXACTLY
(learning rate, weight decay, betas, epsilon, clipping, scheduler, warmup);
a plan without a declared clip norm refuses (clipping is required). Every
Gate 10B seed is applied; canonical data order means shuffling is disabled;
applied deterministic settings are recorded as backend-reported evidence.

## 6. Real execution states, events, and consistency discipline

States: planned → validated → starting → running → completed | failed —
resume and cancel states are structurally excluded, `retry_number:
Literal[0]`. Events are hash-chained and sequence-numbered (no timestamps,
no durations, no raw text or tensors) and each carries a CONSISTENCY class:
structurally_verified / recomputable / backend_reported / non_recomputable.
Losses are validated as finite canonical decimals and recorded as
backend-reported testimony — the verifier recomputes bindings, ordering,
monotone step counts, final-state consistency, and digests, and it never
claims to replay losses, gradients, weights, or kernel behavior. The result
model cannot represent claims of replay determinism, model quality,
validation/test accuracy, or benchmark movement. A failed hot section becomes
a FAILED execution artifact with a structured failure class (16 classes) and
no checkpoint; refusals before any loading raise without creating artifacts.

## 7. The first real checkpoint format

`verifiednet.real-checkpoint-v1` — a NEW spec; Gate 10D's fake format is
untouched and still cannot claim real loadability:

```
real-checkpoints/<realckpt-…>/
    manifest.json
    payload/{checkpoint.json, config.json, model.safetensors, tokenizer.json}
```

Full-model safetensors weights, model config, tokenizer snapshot, metadata —
optimizer/scheduler/RNG/resume state Literal-excluded, checkpoint-on-
completion only, exactly one checkpoint per execution. Candidate-versus-
verified is preserved: the engine emits raw bytes; the writer recomputes
hashes, validates the safetensors payload structurally (dependency-free
8-byte-header + JSON parsing — the checkpoint is NEVER loaded into a model to
verify it), binds lineage, and verifies before removing `.INCOMPLETE`.
Lineage (`reallin-…`) binds: real execution id + evidence digest,
authorization id + digest, plan id + digest, spec id, corpus id + digest,
slice id, model/tokenizer artifact ids, backend id, execution policy id, and
the completed step count; `parent_checkpoint_id` remains structurally `None`
(warm starts and resume stay deferred). Publication rule (ADR-0027): a
completed execution MUST reference exactly one checkpoint; a failed execution
can NEVER publish one — both parse-time-locked and store-verified.

## 8. Persistence

```
real-training-executions/<realexec-…>/
    manifest.json  authorization-binding.json  events.jsonl  result.json
```

No raw training data, no model bytes, no timestamps, no host facts; the
checkpoint lives in its own store and is bound by id (payload bytes bind
through the checkpoint's own verified digest, never hashed into the execution
digest directly). Writer/verifier/reader follow the repository discipline;
the verifier detects hash mismatches, ordering violations, step regressions,
success-without-checkpoint, failure-with-checkpoint, exceeded bounds, and
retry/resume evidence.

## 9. Offline stub versus optional real integration

The offline suite exercises the ENTIRE structural pipeline through
`StubTrainingEngine` — deterministic synthetic losses and a structurally
valid safetensors payload derived from the execution identity (build-twice
byte-identical) — under ML import traps and subprocess/network sabotage. The
Gate 10C simulator is untouched and still serves the core suite. Genuine
weight mutation lives in ONE integration test, DOUBLE-gated: deselected by
default AND requiring `VERIFIEDNET_RUN_REAL_TRAINING=1`, the `training-hf`
extras (`torch`, `transformers`, `safetensors` — defined now because this
gate actually consumes them; core imports never require them), and an
approved local model via `VERIFIEDNET_LOCAL_MODEL_DIR`. It performs the
bounded run, writes exactly one checkpoint, and proves real weight mutation
by hashing one tensor's serialized bytes before/after — without exposing
values — while proving the source model artifact unchanged.

The AST import boundary gains exactly one sanctioned lazy-ML site
(`hfexecutor.py`, mirroring the sanctioned subprocess site), with a dedicated
test proving every ML import there is function-level.

## 10. Privacy limitation, stated honestly

Execution events, manifests, and checkpoint TEXTUAL metadata never carry
rendered inputs, targets, fault-family labels, or trace identities (scanned
by test; the slice policy lists selected example ids for audit only). Trained
weights, by nature, encode learned information from the training rows —
these guarantees cover explicit artifact fields, not model memorization.

## 11. What follows

Checkpoint-backed prediction behind the Gate 8 feature-only interface, then
evaluation, then Gate 9 benchmark comparison — each as its own gate. Warm
starts, resume, adapters, distributed execution, and publication remain
deferred and forbidden here.

**Update (Gate 11):** checkpoint-backed prediction is now implemented — see
`../gate11/checkpoint-predictor.md` and ADR-0028. The real-checkpoint format
defined here is byte-unchanged (the manifest's
`predictor_adapter_version="deferred-next-gate-v0"` Literal stays as written;
Gate 11's inference scope is a SEPARATE evaluation-side compatibility model,
so existing checkpoint ids and digests remain valid). Evaluation and Gate 9
benchmark comparison of the trained checkpoint remain the next boundary.

**Update (Gate 12):** evaluation and benchmark comparison are now implemented
(matched, unconfounded, policy-worded — ADR-0029); the checkpoint remained
byte-identical throughout, and its training corpus/plan/authorization/
execution artifacts stayed untouched. See `../gate12/checkpoint-benchmark.md`.
