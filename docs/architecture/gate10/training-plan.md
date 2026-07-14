# Gate 10B — Reproducible Training Specification and Trainer Abstraction

**Status:** IMPLEMENTED (Gate 10B). This document describes the planning half of
`verifiednet.training` — how a future training run is described, validated,
negotiated, and persisted as an immutable plan. It implements ADR-0023.
**No fine-tuning occurs in Gate 10B**: no torch/transformers/PEFT, no
optimizers, no gradients, no checkpoints, no model or tokenizer downloads —
the AST boundary guard and import traps enforce this. The output is a verified
training PLAN, not a trained model.

## 1. Why planning is its own gate

```
Training Corpus (Gate 10A)
        ↓  descriptor only (id, digest, count — never example text)
TrainingSpec ──→ capability negotiation ──→ TrainingRequest ──→ TrainingPlan
                                                                   ↓
                                              training-plans/<id>/ (immutable)
                                                                   ↓
                                        Future Execution Runner (later gate)
```

Weights are a function of every input to the run: model revision, tokenizer
behavior, data order, batch shape, hyperparameters, seeds. If any of those is
implicit — a mutable `latest` revision, a float that formats differently across
platforms, a default the framework picks silently — the run is unreproducible
before it starts. Gate 10B pins all of it, content-addressed, while the
machinery is still cheap to verify: everything runs offline with no ML
dependency.

## 2. TrainingSpec: every weight-affecting input, pinned

`TrainingSpec` (`trainspec-…`, self-validating) composes:

- **`TrainableModelSpec`** (`model-…`) — provider, model identifier (absolute
  paths rejected: local filesystem layout never enters identity),
  **immutable revision** (mutable aliases `latest`/`main`/`master`/`head`
  rejected at parse time, case-insensitive), model class, load precision
  (`float32`/`float16`/`bfloat16`), `trust_remote_code: Literal[False]`.
- **`TokenizerSpec`** (`tok-…`) — identifier, immutable revision, class, and
  explicit special-vocabulary, padding, and truncation policies. The tokenizer
  is pinned independently of the model: a silently different tokenizer produces
  different token streams and therefore different weights.
- **`SequenceLengthPolicy`** — max input/target/total tokens; overlength
  handling defaults to fail-closed rejection, never silent truncation.
- **`BatchConfig`** — per-device batch size, gradient-accumulation steps,
  `declared_world_size: Literal[1]` (distributed shapes are a later gate), and
  an `effective_batch_size` that must equal the product (validated).
- **`OptimizationConfig`** — optimizer name plus learning rate, weight decay,
  betas, epsilon, max grad norm as **canonical decimal strings** (§3).
- **`SchedulerConfig`** — scheduler name; `warmup_steps` XOR `warmup_ratio`
  (contradictory warmup rejected; `constant` takes neither).
- **`TrainingBudget`** — a discriminated union: `EpochBudget(epochs)` or
  `StepBudget(max_optimizer_steps)`. A payload carrying both shapes cannot
  parse.
- **`SeedPolicy`** — four explicit seeds: data order, model init, dropout,
  backend. `DataOrderPolicy` is `Literal["canonical"]`: corpus order as
  persisted, shuffling (if ever added) becomes a new versioned policy.
- **Corpus binding** — the Gate 10A `training_corpus_id` AND
  `training_corpus_digest`, plus `task_id`.
- `checkpoint_policy: Literal["none"]` — no checkpoint may even be requested in
  this gate.

## 3. Canonical decimal strings

`canonical_decimal` normalizes any decimal representation via
`Decimal.normalize()` and fixed-point formatting: `"1e-4"`, `"0.0001"`, and
`"1.00e-4"` all become `"0.0001"`; NaN/infinity/non-numbers are rejected.
Hyperparameters are therefore strings with platform-independent identity —
float formatting can never fork a `training_spec_id`. The property suite proves
equivalent mantissa/exponent pairs canonicalize identically and that the
function is idempotent.

## 4. Trainer contract: capabilities, negotiation, plan

- **`TrainerCapabilities`** (`traincap-…`) — implementation id plus sorted,
  deduplicated tuples of supported model families, precisions, optimizers,
  schedulers, and checkpoint policies, and honest flags (cpu/gpu, adapter/full
  fine-tuning, distributed). The id is order-independent (proven by property
  test).
- **`DeterminismClaim`** — `deterministic` / `best_effort_deterministic` /
  `nondeterministic`. The plan RECORDS what the trainer claims instead of
  asserting reproducibility that real GPU kernels may not provide.
- **`build_training_request`** — fail-closed negotiation: the spec's trainer
  implementation id, model family, precision, optimizer, scheduler, and
  checkpoint policy must each be explicitly supported, or `TrainerPlanError`.
  The request also re-validates the spec↔corpus binding (id, digest, task);
  a mismatched binding is a `ValidationError`, not a warning.
- **`TrainingCorpusDescriptor`** — what planning knows about the corpus: id,
  digest, example count, `source_partition: Literal["train"]`. Never example
  text; the descriptor is derived from the Gate 10A manifest.
- **`Trainer` protocol** — `capabilities` plus `plan(spec, corpus)`. **There is
  no `train()`** (proven by contract test, along with the signature never
  accepting prepared/evaluation/benchmark arguments).

## 5. TrainingPlan: exact integer arithmetic

`plan()` derives every execution-shaping count with explicit ceil-division:

```
batches_per_epoch        = ceil(example_count / per_device_batch_size)
optimizer_steps_per_epoch = ceil(batches_per_epoch / grad_accum_steps)
optimizer_steps          = epochs × steps_per_epoch        (EpochBudget)
                         | max_optimizer_steps              (StepBudget)
```

Partial final batches count; partial accumulation windows flush. A
`StepBudget` plan derives no epoch count (`expected_epochs is None`) rather
than inventing one. `TrainingPlan` (`trainplan-…`) re-validates every derived
count and its own id at parse time — a tampered `optimizer_steps` fails to
parse. The plan also records the request, determinism claim, and effective
batch size.

## 6. FakeTrainer: proving the machinery offline

`FakeTrainer` (`fake-trainer-v1`) declares ONLY capabilities it genuinely
simulates (the `fake` model family, `float32`, `adamw`,
`constant`/`linear_warmup`, checkpoint policy `none`) and claims
`deterministic` honestly — it is pure arithmetic. `simulate(plan)` returns a
`SimulatedTrainingResult` whose synthetic loss is derived deterministically
from the plan id and whose flags are Literal-locked: `simulated=True`,
`produced_checkpoint=False`. A fake outcome structurally cannot claim a real
checkpoint. This gives every downstream consumer (plan store, verifier,
future orchestration) a real object to be tested against without any ML
framework existing.

## 7. Immutable plan store

```
training-plans/<training_plan_id>/
    manifest.json    request.json    plan.json    [simulated-result.json]
```

Same discipline as every VerifiedNet artifact: atomic write under
`.INCOMPLETE`, post-write verification before finalizing, overwrite refusal,
path-sorted file hashes, and a self-validating `plan_digest` (`plandig-…`).
The manifest records whether a simulated result is present, and the writer
refuses a simulated result whose plan id does not match the plan being
written. `verify_training_plan` reconstructs the request and plan (model
validators re-derive all ids and counts), re-checks manifest consistency and
file hashes, and fails closed with structured checks; `read_training_plan`
verifies first, then returns the typed artifact.

## 8. Guarantees proven by test

Deterministic ids at every level (spec/model/tokenizer/capability/request/
plan) with hyperparameter, seed, and budget sensitivity; canonical-decimal
normalization and idempotence (property-tested); exact batch/step arithmetic
(unit- and property-tested); fail-closed capability negotiation (unsupported
family/precision/optimizer/implementation each refused); corpus-binding
mismatches refused at request construction; **corpus-digest ripple** (a digest
change propagates through spec, request, plan, and on-disk digest — a real
content change, one fewer accepted run, proves the same end-to-end);
**evaluation isolation** (different benchmark results and rankings leave the
spec and plan byte-identical; an evaluation-side abstention change alters
provenance pins only, never any planned quantity); **source immutability**
(runs, dataset export, prepared corpus, evaluations, benchmarks, and the
training corpus are byte-identical before/after the full pipeline);
**no real training** (import traps on torch/transformers/tokenizers/
safetensors/peft/accelerate/bitsandbytes/deepspeed while the entire pipeline
runs; the plan directory contains only the declared JSON files); **no
execution/no network** (subprocess, process runner, `urllib`, and the Ollama
backend sabotaged); build-twice reproducibility (byte-identical plan
directories); tamper rejection (corrupted plan file, tampered manifest,
missing file, tampered ids/counts all fail closed); and the AST import
boundary (the training package — including the Gate 10B modules — imports no
evaluation, live-execution, or model-training modules).

## 9. Limitations and the next gate

The plan is a contract, not a promise of bitwise-identical weights: real GPU
training may be `best_effort_deterministic` at best, which is exactly why the
determinism claim is recorded rather than assumed. `FakeTrainer` proves
orchestration, not learning. World size is locked to 1; adapters, quantized
loading, distributed shapes, and checkpoints are all later gates. Gate 10C
(implemented — see `training-execution.md`, ADR-0024) consumes a verified plan
and adds execution ORCHESTRATION, still simulation-only: states, events,
resume, retry, and replay verification. Checkpoint contracts (Gate 10D) and
the first real trainer backend (Gate 10E) follow behind it — every
configuration decision has already been made, hashed, and persisted here.
