# 0027 — Real executions are structurally verified, never replay-reconstructed; checkpoint publication requires verified authorization AND verified completed execution

**Status:** Accepted (owner decision, Gate 10F)
**Date:** 2026-07-14

## Context

ADR-0024's replay verification worked because the simulator was deterministic:
the whole event log was a pure function of its header. A REAL backend breaks
that premise — exact losses, gradients, weight deltas, and kernel behavior
cannot be re-derived from any header. Pretending otherwise would either forbid
real training forever or corrupt the verification vocabulary with claims that
cannot be checked. The first genuine weight mutation needs its own honest
consistency discipline, and the first genuine checkpoint needs a publication
rule strong enough that unaudited weights can never slip toward evaluation.

## Decision

1. **Real-execution evidence carries explicit consistency classes.**
   `structurally_verified` (bindings, ids, event ordering, monotone step
   counts, final-state consistency — recomputed by the verifier),
   `recomputable` (slice membership, planned step arithmetic — re-derivable
   from verified inputs), `backend_reported` (finite canonical-decimal losses,
   applied deterministic settings — validated for form, never re-derived), and
   `non_recomputable` (kernel behavior, durations — not persisted at all).
   The result model Literal-locks `claims_replay_determinism=False` and
   `claims_model_quality=False`: bit-identical repeatability and scientific
   quality are structurally unclaimable. Gate 10C's replay guarantee remains
   intact for simulated executions and is deliberately NOT extended.

2. **Real execution is reachable only through a verified authorization,
   revalidated at the moment of use.** The executor's only public method
   requires the authorization artifact; immediately before model loading it
   re-verifies the artifact and re-checks every binding (plan id+digest,
   corpus id+digest, backend, model/tokenizer content hashes, determinism
   acceptance, every bounded-policy ceiling). Changed evidence refuses —
   an authorization is never mutated or refreshed in place. Bounds are
   enforced BEFORE model loading; the engine cannot run without them.

3. **The first real run is bounded by content-addressed policy, not by
   discretion.** One approved model (exact immutable revisions, parameter
   ceiling, local-cache only), one deterministic corpus slice (first-N in
   canonical Gate 10A order, ids recorded before training, never informed by
   evaluation), one exact training objective (serialization, separator,
   label masking, padding, loss reduction — all identity-bearing), and
   Literal-locked runtime ceilings (steps ≤ 64, epochs ≤ 8, examples ≤ 64,
   batch ≤ 8). Retries and resume are structurally unsupported
   (`retry_number: Literal[0]`).

4. **Checkpoint publication requires BOTH a verified authorization and a
   verified COMPLETED execution.** A completed execution must reference
   exactly one produced checkpoint; a failed execution can never publish one
   (both directions parse-time-locked and store-verified). The real format
   (`verifiednet.real-checkpoint-v1`: full-model safetensors + config +
   tokenizer snapshot + metadata; optimizer/scheduler/RNG/resume state
   excluded; checkpoint-on-completion only) is a NEW spec — the Gate 10D fake
   format is untouched and still cannot claim real loadability. Candidate vs
   verified is preserved: backends emit raw bytes, the writer recomputes every
   hash and validates the safetensors payload STRUCTURALLY (dependency-free
   header parsing — a checkpoint is never loaded into a model to verify it).
   Lineage binds execution, authorization, plan, spec, corpus, slice,
   artifacts, backend, policy, and step count; parents remain forbidden.

5. **Heavy ML stays optional, lazy, and sanctioned.** The `training-hf`
   extras group exists now because Gate 10F actually consumes it; core
   imports never require it. Exactly one module may reference
   torch/transformers (the executor), the AST guard allowlists exactly that
   file, and a dedicated test proves every such import is function-level.
   The offline suite exercises the ENTIRE structural pipeline through a
   deterministic stub engine; genuine weight mutation lives in one
   double-gated integration test (explicit env flag + extras + approved
   local model), strictly local-files-only — a cache miss refuses, never
   downloads.

6. **Privacy claims are about artifact fields, not weights.** Execution
   events and manifests never carry rendered inputs, targets, labels, or
   trace identities (scanned by test); the slice policy lists selected
   example ids for audit only. Trained weights naturally encode learned
   information — model memorization is documented as out of scope for field
   scanning, not denied.

## Consequences

- The next gate (checkpoint-backed prediction) receives a checkpoint whose
  entire causal history — corpus rows through authorization through completed
  execution — is verifiable, and nothing about its quality has been claimed.
- Reproducibility disputes for real runs reduce to comparing intent
  (plan/policies) and structural evidence; loss curves are testimony, not
  proof, and are labeled as such.
- The cost: two verification vocabularies (replayed vs structural) now
  coexist. That is the honest price of real kernels; blending them would
  falsify one or hobble the other.

## References

- `../gate10/real-training.md`
- ADR-0026 (intent vs environmental authorization), ADR-0025 (checkpoints are
  verified artifacts), ADR-0024 (simulated execution is replay-verified),
  ADR-0022 (training data is train-only)
