# 0023 — Training runs are planned, content-addressed, and verified before any execution exists

**Status:** Accepted (owner decision, Gate 10B)
**Date:** 2026-07-14

## Context

ADR-0022 fixed WHAT may become training data. The next danger is HOW a training
run happens: the moment fine-tuning code exists, an unguarded pipeline can train
against an ambiguous configuration ("latest" model revision, a float learning
rate that hashes differently across machines, an implicit tokenizer default),
making the resulting weights unreproducible and unauditable. Every input that
can affect trained weights must be pinned before a single gradient is ever
computed — and the planning machinery must be provable OFFLINE, without any ML
framework, so its correctness never depends on GPU availability or network
access. Gate 10B builds that contract; it still trains nothing.

## Decision

1. **Every weight-affecting input is explicit and content-addressed.** A
   `TrainingSpec` (`trainspec-…`) pins the model identity (provider, identifier,
   IMMUTABLE revision — mutable aliases like `latest`/`main` are rejected at
   parse time, absolute paths never enter identity, `trust_remote_code` is
   Literal-locked `False`), the tokenizer identity (with explicit padding,
   truncation, and special-vocabulary policies), the bound training corpus
   (Gate 10A id AND digest), sequence-length policy (overlength fails closed),
   batch shape (validated effective batch size, declared world size locked to
   1), optimization hyperparameters, scheduler, budget, and a full seed policy
   (data order, model init, dropout, backend). Nothing weight-affecting may be
   implicit, defaulted silently, or supplied at execution time.

2. **Numeric hyperparameters are canonical decimal strings.** Learning rate,
   weight decay, betas, epsilon, and clip norms are stored as normalized decimal
   strings (`"1e-4"`, `"0.0001"`, and `"1.00e-4"` all canonicalize to
   `"0.0001"`), never floats — equivalent values hash identically on every
   platform, and representation ambiguity can never fork a training identity.

3. **Planning is separated from execution.** The `Trainer` protocol's
   authoritative operation is `plan(spec, corpus) -> TrainingPlan` — there is no
   `train()` in this gate. A plan derives every execution-shaping quantity
   (batches per epoch, optimizer steps, effective batch size) by exact integer
   arithmetic with explicit ceil-division remainder behavior, and re-validates
   those counts at parse time. Capability negotiation fails closed: a trainer
   that does not explicitly support the requested model family, precision,
   optimizer, scheduler, and checkpoint policy refuses the request.

4. **Determinism claims are honest and structural.** A trainer declares
   `deterministic`, `best_effort_deterministic`, or `nondeterministic` — the
   plan records the claim rather than asserting reproducibility that real GPU
   kernels may not provide. The only trainer in this gate, `FakeTrainer`,
   declares exactly the capabilities it genuinely simulates; its simulated
   result is Literal-locked `simulated=True, produced_checkpoint=False`, so a
   fake outcome structurally cannot masquerade as a real checkpoint.

5. **Plans are immutable, verified artifacts.** A training plan persists under
   `training-plans/<training_plan_id>/` with an atomic writer, a self-validating
   manifest digest, overwrite refusal, and a fail-closed verifier/reader that
   re-derives every identity and count. Corpus binding is transitive: changing
   the training-corpus digest ripples through spec, request, plan, and on-disk
   digest — while changes to evaluation results or benchmark rankings leave the
   spec and plan byte-identical (proven by test, extending ADR-0022's
   isolation).

6. **Gate 10B trains nothing and can prove it.** No
   torch/transformers/PEFT/optimizer/checkpoint code exists or is imported
   (AST-enforced and trapped at the import boundary during the full pipeline);
   planning, simulation, write, verify, and read all succeed with subprocess,
   process runner, network, and inference backends sabotaged.

## Consequences

- A future execution gate receives a complete, verified, content-addressed
  description of the run — it adds ONLY execution, never configuration
  decisions, so any weight difference between two runs is traceable to an
  explicit spec difference or an honest nondeterminism claim.
- Reproducibility disputes reduce to comparing `training_spec_id`s.
- The cost is verbosity: every knob must be written down. That is the point.

## References

- `../gate10/training-plan.md`
- ADR-0022 (training data is train-only and evaluation-isolated), ADR-0020
  (models behind the feature-only boundary), ADR-0019 (deterministic
  model-free evaluation)
