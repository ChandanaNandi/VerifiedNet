# 0025 — Checkpoints are verified artifacts with bound lineage, never trusted candidates

**Status:** Accepted (owner decision, Gate 10D)
**Date:** 2026-07-14

## Context

ADR-0022 through ADR-0024 secured training data, run configuration, and
execution orchestration. The remaining artifact before real ML integration is
the most consequential one: the checkpoint. A checkpoint is the boundary
object between training and prediction — if its provenance is loose, an
unaudited or wrong set of weights can silently enter evaluation, and every
benchmark after that is meaningless. The rules for what a checkpoint IS must
exist, hardened and tested, before any real weights do.

## Decision

1. **Candidate and checkpoint are different things, and the type system says
   so.** A `CheckpointCandidate` is untrusted backend output: it carries raw
   content and deliberately has no hash fields, so candidate-supplied
   integrity claims cannot exist. A verified checkpoint exists only as a
   persisted directory whose self-validating manifest passed
   `verify_checkpoint`. Instantiating a manifest model does not create trust;
   the writer recomputes every hash and size from actual bytes and re-verifies
   the persisted artifact before removing `.INCOMPLETE`.

2. **A checkpoint may be created only from a VERIFIED COMPLETED execution.**
   Eligibility is established by fully verifying the execution and plan
   artifacts — never by a state string. Failed, cancelled, running, corrupt,
   or incomplete executions are rejected structurally. A resumed execution
   that completed is eligible; its lineage binds through the execution
   artifact (which records `resumed_from_execution_id`) — no checkpoint
   parent is invented, because no prior checkpoint was consumed.
   `parent_checkpoint_id` is structurally `None` in this gate; checkpoint
   chaining is a later gate's explicit contract.

3. **Identity is two-layered: logical id and content digest.**
   `checkpoint_id` binds format spec, lineage, declared file roles, simulated
   status, model/tokenizer compatibility, and checkpoint version — never
   paths, never machine-local metadata, never payload bytes.
   `checkpoint_digest` binds the verified content: every configuration block
   plus path-sorted file hashes/sizes/roles. Changing any payload byte,
   lineage value, compatibility field, or format setting changes the digest;
   changing any source identity (execution, plan, spec, corpus, model,
   tokenizer, policy, retry) changes the lineage id and therefore the
   checkpoint id. One logical identity has exactly one artifact on disk.

4. **Lineage binds everything upstream, exactly.** Execution id + digest,
   plan id + digest, request id, spec id, corpus id + digest, model and
   tokenizer spec ids, trainer implementation and capability ids, execution
   policy id, retry number. Lineage consumes only verified identities — never
   evaluation records, benchmark rankings, predictor outputs, labels, or raw
   training examples (proven: payload scans find no training input, target,
   example/group id, or fault-family label; evaluation/benchmark changes leave
   the artifact byte-identical).

5. **File contracts are explicit and hostile-input-proof.** Every file has a
   declared role, serialization id, required status, and a safe canonical
   relative path under `payload/`; absolute paths, `..`, backslashes,
   duplicates of path or role, undeclared files, symlinks, and executable
   payloads are all rejected — at parse time where representable, by the
   verifier otherwise.

6. **Simulation honesty is layered and tamper-tested.** The Gate 10D format
   is Literal-locked: `simulated_checkpoint` kind, fake payload format,
   `weights_declaration="simulated_none"`, optimizer/scheduler/resume state
   excluded, compatibility `simulated_only=True` /
   `loadable_as_real_model=False` with an empty real-backend list, a
   `.fakebin` extension, and a mandatory fake magic prefix in the payload
   bytes. There is no model-loading API anywhere in the package. Rewriting a
   persisted artifact to claim safetensors, full-model or adapter weights, or
   real loadability fails verification in every tested variant.

## Consequences

- Gate 10E's real trainer backend inherits a finished contract: it produces
  CANDIDATES, and nothing it emits becomes trusted except through the same
  verifier — a real checkpoint will be introduced as a NEW explicit format
  spec, not by loosening this one.
- The future checkpoint-backed predictor can bind to `checkpoint_id` +
  `checkpoint_digest` and know exactly which corpus, spec, plan, and execution
  produced its weights.
- Structural/exact-value leakage absence is proven; cryptographic
  impossibility of encoded leakage is NOT claimed — the guarantee is the fake
  producer's input contract (content-addressed identities only), stated
  honestly.

## References

- `../gate10/checkpoint-artifact.md`
- ADR-0024 (execution is event-sourced and simulated first), ADR-0023
  (runs are planned before executed), ADR-0022 (training data is train-only),
  ADR-0010 (models are never ground truth)
