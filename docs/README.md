# VerifiedNet — Engineering Documentation

This directory is the project's engineering notebook: not just what was built, but
**why**. It is organized by purpose so that any decision, inventory, or rationale can
be found quickly months later.

## Layout

```
docs/
├── README.md                     # this index
├── architecture/                 # how the system is designed, gate by gate
│   ├── final-platform-vision.md  # the long-term destination (8 layers, trust core)
│   ├── gate0/                     # source inventory, licenses, environment assumptions
│   ├── gate1/                     # capability map + code-reuse matrix
│   ├── gate2/                     # Wave A file-level harvest plan
│   ├── gate2_5/                   # architecture validation before implementation
│   ├── gate3/                     # contracts, package boundaries, runtime/security, limitations
│   └── decisions/                # Architecture Decision Records (ADRs)
├── provenance/                   # where reused/adapted code came from + license posture
├── research/                     # deep engineering audits of the source repositories
└── roadmap/                      # planned future gates (4–15)
```

## Reading order (for a newcomer)

1. Top-level `../README.md` — what VerifiedNet is and its current status.
2. `architecture/final-platform-vision.md` — the destination and the layers.
3. `roadmap/future-gates.md` — the gate-by-gate path there.
4. `architecture/decisions/` — the load-bearing choices, each in one short record.
5. `architecture/gate3/contracts.md` + `package_boundaries.md` — the current shape.
6. The gate folders (`gate0` → `gate3`) — the full derivation, in order.
7. `research/` — the source-repo audits that seeded every reuse decision.
8. `provenance/wave_a_provenance.md` — the audit trail for every adapted symbol.

## Conventions

- **Decision records** live in `architecture/decisions/NNNN-title.md`, numbered and
  immutable once accepted (supersede rather than edit). Format: Status / Context /
  Decision / Consequences / References.
- **Knowledge, not transcripts.** Discussions are distilled into concise engineering
  documents (a design note, an ADR, a research page) — raw chat logs are not stored.
- **Design notes** (`docs/design/`), **brainstorming** (`docs/brainstorming/`), and
  **meeting notes** (`docs/meeting_notes/`) are part of this convention; those folders
  are added when there is real content to put in them, rather than kept empty.
- Gate documents are historical: they record the state at that gate and are not
  rewritten later. Corrections are captured in a subsequent gate or an ADR.

## Status

Gates 0–3 complete (offline architecture and contracts). Gate 4 complete: the first live
verified incidents (two-router FRR; accepted + precondition-rejected), canonical per-run
artifacts, a run index, and a thin composition root — see
`architecture/gate4/gate4-completion-report.md`. Gate 5 is in progress: the
evidence-based fault-family plan (Gate 5.0) is in
`architecture/gate5/fault-family-plan.md`; Gate 5 is complete: a verified fault-family
library (BGP remote-AS mismatch, neighbor removal, interface shutdown, prefix
withdrawal), a bounded scenario catalog with reverse-orientation proof, and
cross-family isolation — see `architecture/gate5/gate5-completion-report.md`.
Gate 6 (verified dataset engine) is implemented through Gate 6.2: the engine
design (Gate 6.0) is in `architecture/gate6/` (dataset-engine-plan,
leakage-analysis, dataset-schema, splitting-strategy, gate6-roadmap) with
ADR-0018. Gate 6.1 (read-only models, discovery, integrity-gated projection),
Gate 6.2 Part 2 (rejected-as-abstention projection, deterministic integer-bucket
splitting, fail-closed leakage audit), Gate 6.2 Part 3 (the immutable exported
dataset — corpus manifest, self-validating `dataset_digest`,
writer/reader/verifier, reproducibility), and Gate 6.2 Part 4 (explicit
feature/label/trace separation with versioned policies, a feature-leakage audit,
and the persisted "prepared" corpus with a model-facing features-only loader) now
exist in `verifiednet.datasets` — a read-only, model-free projection that never
mutates a verified run; see
`architecture/gate6/rejected-examples-and-leakage-safe-splits.md`,
`architecture/gate6/exported-dataset-and-reproducibility.md`, and
`architecture/gate6/feature-label-separation.md`.
Gate 7 (deterministic evaluation framework) is implemented in
`verifiednet.evaluation` with ADR-0019: a versioned evaluation-task contract,
deterministic model-free rule baselines (a fixed-prior floor + an
evidence-rule baseline) that receive ONLY model-visible features, abstention-aware
scoring with separate accepted/abstention metrics, and an immutable,
content-addressed evaluation result (manifest + records + metrics + confusion)
with a self-validating `evaluation_digest`, a recompute-from-records verifier, and
reproducibility/immutability/no-execution proofs. No model, LLM, embedding, or
training is involved. See `architecture/gate7/evaluation-framework.md`.
Gate 8 (base SLM predictor benchmark) is implemented with ADR-0020: the first
model-backed predictor (`verifiednet.evaluation.SlmPredictor`) plugs into the Gate 7
evaluation framework through the SAME feature-only boundary as the rule baselines —
it receives only `DatasetFeatures`, renders a versioned prompt, calls a pluggable
inference backend, and strictly parses/validates/normalizes structured output into a
prediction (malformed output becomes an explicit invalid prediction, never an
exception). Offline CI stays completely model-free via a deterministic fake backend;
a real local Ollama backend is exercised only by an optional integration test. The
evaluation engine, records, metrics, and digests are unchanged. See
`architecture/gate8/slm-predictor.md`.
Gate 9 (multi-predictor benchmark framework) is implemented with ADR-0021: a
deterministic predictor registry, an order-independent content-addressed
`BenchmarkSpec`, a pure `run_benchmark` that evaluates every predictor under
identical conditions through the unchanged Gate 7 engine, deterministic
comparison metrics and a fully tie-broken ranking, and immutable benchmark
artifacts (`benchmarks/<id>/manifest+comparison+ranking`) with a self-validating
`benchmark_digest` and a verifier that recomputes ranking-from-comparison. The
benchmark compares predictors and never changes evaluation; predictors still
receive only features. See `architecture/gate9/benchmark-framework.md`.
Gate 10A (training readiness) is implemented with ADR-0022: a deterministic,
immutable supervised training-corpus layer (`verifiednet.training`) derived from
the prepared corpus. Eligibility is Literal-locked to train-partition
accepted-diagnosis examples (validation/test/abstention structurally excluded);
model input is an allowlist rendering of model-visible features; the target is
canonical JSON from the authoritative label; audit metadata stays separate; the
trainer-facing loader returns only input/target pairs; partition isolation is
proven by test; and the training package may not import evaluation or any
model-training library (AST-enforced). **No model training occurs in Gate 10A.**
See `architecture/gate10/training-corpus.md`.
Gate 10B (reproducible training specification and trainer abstraction) is
implemented with ADR-0023: every weight-affecting input is explicit and
content-addressed in a `TrainingSpec` (immutable model/tokenizer revisions,
canonical decimal hyperparameters, full seed policy, validated batch shape,
train-corpus binding by id and digest); a `Trainer` protocol whose
authoritative operation is `plan` (there is no `train()`); fail-closed
capability negotiation; a `TrainingPlan` with exact integer batch/step
arithmetic and honest determinism claims; a `FakeTrainer` proving the
machinery offline; and immutable, verified `training-plans/<id>/` artifacts.
**No fine-tuning occurs in Gate 10B** — no ML framework is imported
(AST-enforced and import-trapped). See `architecture/gate10/training-plan.md`.
Gate 10C (deterministic training execution framework) is implemented with
ADR-0024: execution is a closed state machine
(planned→validated→starting→running→completed, with failed/cancelled branches
and failed→resumed→running), recorded as an ordered, hash-chained,
timestamp-free event log that the model validator verifies by REPLAY (the
deterministic simulator's log is a pure function of its header, so any
dropped/duplicated/reordered/edited event fails at parse time); execution ids
derive from plan + capability + retry policy + retry number (a retry is a new
execution, one authoritative outcome per identity); resume continues exactly
where a failure stopped (property-proven for every failure point); and
executions persist as immutable verified `training-executions/<id>/`
artifacts. **Execution is simulation-only in Gate 10C** — Literal-locked
`simulated=True`, fake engine only, no ML framework imported. See
`architecture/gate10/training-execution.md`.
Gate 10D (immutable checkpoint artifact and lineage contract) is implemented
with ADR-0025: untrusted `CheckpointCandidate` (content, no hashes) versus
verified persisted checkpoint (self-validating manifest, recomputed hashes);
two-layer identity (logical `checkpoint_id` over format+lineage+roles, content
`checkpoint_digest` over verified bytes); lineage binding execution/plan/spec/
corpus/model/tokenizer/capability/policy/retry with parent checkpoints
structurally forbidden; eligibility only from a VERIFIED completed execution;
a deterministic fake producer (magic-prefixed `.fakebin`, metadata-only
config/tokenizer JSON); layered simulation honesty (Literal-locked kind,
not-real-loadable compatibility, no model-loading API); and fail-closed
verification with an independent lineage audit. **No real model checkpoint
exists in Gate 10D.** See `architecture/gate10/checkpoint-artifact.md`.
Gate 10E (real trainer-backend contract and execution preflight) is
implemented with ADR-0026: immutable training intent (Gate 10B) is strictly
separated from runtime environment evidence — a content-addressed
`RealTrainerBackendSpec` (single-device HF full fine-tuning, the only modeled
mode), a secret-free `TrainingEnvironmentSnapshot` (PEP 440 package records
via importlib.metadata, device capability, deterministic-mode support),
separate immutable model/tokenizer resolution (pinned revisions + content
hashes; mutable aliases unrepresentable), a 12-stage structured preflight
(plan/corpus/backend/packages/device/resolution/precision/memory/determinism/
checkpoint/authorization; skips visible, never hidden), honest determinism
categories with explicit best-effort acknowledgement, and an immutable
verified `training-authorizations/<id>/` artifact whose validity is
recomputed, never trusted. **No real training, model loading, or checkpoint
occurs in Gate 10E**; heavy ML libraries are not dependencies, and their
absence is a structured finding. See
`architecture/gate10/execution-preflight.md`.
Gate 10F (first bounded real training execution) is implemented with
ADR-0027: real weight mutation is reachable only through a verified,
revalidated authorization plus four content-addressed bounded policies
(approved model, deterministic first-N corpus slice, exact causal-LM
objective with label masking, Literal-locked runtime ceilings); real
executions are STRUCTURALLY verified (bindings, ordering, monotone counts,
digests) with explicit consistency classes and can never claim replay
determinism or model quality; the first genuine checkpoint format
(`verifiednet.real-checkpoint-v1`, full-model safetensors validated by
dependency-free structural parsing) publishes only from a verified COMPLETED
execution with complete lineage and no parent; heavy ML lives in the optional
`training-hf` extras behind one sanctioned lazy-import module, the offline
suite runs a deterministic stub end-to-end, and genuine weight mutation is a
double-gated optional integration test. **No evaluation, benchmarking, or
quality claim of the trained checkpoint exists.** See
`architecture/gate10/real-training.md`.
Layers beyond are **planned, not
implemented** — no checkpoint-backed prediction, trained-model evaluation or
benchmark integration, prompt optimization, RAG,
GraphRAG, agent, memory, or persistent workflow exists yet. The deterministic
trust core (labs →
faults → evidence → verification → oracle → incidents → recovery → artifacts → index) is
fixed and is never replaced by a model. See `architecture/gate3/limitations.md`.
