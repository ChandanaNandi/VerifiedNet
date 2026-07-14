# Gate 10E — Real Trainer Backend Contract and Execution Preflight

**Status:** IMPLEMENTED (Gate 10E). This document describes the first real
ML-backend boundary in `verifiednet.training` — the backend contract, the
environment snapshot, immutable artifact resolution, the staged preflight, and
the execution-authorization artifact. It implements ADR-0026. **No real
training or checkpoint occurs in Gate 10E**: no gradients, no optimizer or
scheduler instantiation, no weight mutation, no model/tokenizer loading, no
downloads, no ML framework import anywhere in the package.

## 1. Immutable intent versus runtime evidence (the core distinction)

```
Verified Training Corpus ──→ Verified Training Plan     (IMMUTABLE INTENT)
                                       ↓
                     Real Trainer Backend Selection      (contract)
                                       ↓
                Environment & Capability Preflight       (RUNTIME EVIDENCE)
                                       ↓
             Resolved Immutable Model/Tokenizer Inputs
                                       ↓
        Execution Authorization  — or structured refusal
                                       ↓
              Future Real Training Execution (Gate 10F)
```

Intent (Gate 10B) is content-addressed and portable; evidence (this gate) is
what one machine can do at inspection time. Evidence lives in a separate
snapshot + authorization and NEVER mutates a plan: the same plan on two
machines yields two authorization ids, by design.

## 2. Selected initial real-backend scope

Exactly one mode, chosen as the smallest viable contract:
**Hugging Face Transformers + PyTorch, single process, single device, FULL
fine-tuning.** No PEFT/LoRA/QLoRA (the Gate 10B spec models no adapter
hyperparameters — claiming an adapter mode would be an unmodeled promise), no
distributed, no DeepSpeed, no FSDP, no multi-node, no remote training. A
future adapter mode requires explicit modeling and a NEW backend spec.

## 3. Backend contract

`RealTrainerBackendSpec` (`trainbk-…`, self-validating) declares the
IMPLEMENTATION contract — never the current machine: framework family,
training mode (Literal-locked), required packages + PEP 440 constraints,
supported OSes/devices/precisions/optimizers/schedulers/checkpoint
declarations. Changing any supported behavior changes the id.
`build_hf_backend_capabilities()` provides the Gate 10B planning path for the
real backend (`plan_for_real_backend`, honest `best_effort_deterministic`
claim) — fake-plan verification is untouched, and a fake-trainer plan on the
real backend is structurally refused (`fake_plan_on_real_backend`).

## 4. Environment snapshot

`TrainingEnvironmentSnapshot` (`envsnap-…`): Python implementation/version,
OS family, architecture, name-sorted `RuntimePackageRecord`s, one
`TrainingDeviceCapability` (`devcap-…`), deterministic-mode support, backend
and cache availability. Schema-level secret exclusion: `extra="forbid"` means
username/hostname/home/env-vars/pid/time are UNREPRESENTABLE, not merely
omitted. Identical probes yield identical snapshots; different machines are
expected to differ — snapshots are evidence, not portable intent.

Package checks use `importlib.metadata` (which never imports the package) and
`packaging`'s PEP 440 parser — never lexicographic comparison ("2.10" >
"2.9"), never automatic installs; missing/incompatible packages are
structured findings. The v1 `SystemEnvironmentProbe` is deliberately
CPU-only with undeclared total memory: honest CUDA probing requires torch
itself and arrives with Gate 10F — until then real-machine preflight refuses,
which is correct. `FakeEnvironmentProbe` drives the entire offline suite.

## 5. Immutable model and tokenizer resolution

Separate resolvers (`ModelArtifactResolver` / `TokenizerArtifactResolver` —
tokenizer identity is never assumed from model identity) return
`ResolvedModelArtifact` (`modelart-…`) / `ResolvedTokenizerArtifact`
(`tokart-…`): pinned revision, content hash, source, cache presence, required
files, explicit `declared_parameter_count` (model), special-vocab/padding
agreement (tokenizer), and a verification status where "verified" REQUIRES a
content hash + local artifact (+ policy agreement). Mutable aliases are
rejected at parse time in every model. Resolvers never download; offline
preflight makes no network access. Gate 10E ships fake resolvers; a
local-cache/torch-backed resolver belongs to Gate 10F.

## 6. Preflight stages

Twelve explicit ordered stages, each always reported (skips are ERROR
findings, never hidden):

```
PLAN_VERIFICATION      full artifact verification; ids re-derived
CORPUS_VERIFICATION    corpus artifact verifies; id/digest/task/policy/
                       template/count binding to the plan; pairs loadable;
                       pair loader exposes ONLY input/target text
BACKEND_CONTRACT       real (never fake) implementation id; family/precision/
                       optimizer/scheduler/world-size/data-order supported
PACKAGE_CHECK          PEP 440 compatibility for every required package
DEVICE_CHECK           supported OS + device type; EXACTLY one device
                       (implicit distributed rejected); no silent GPU→CPU
                       fallback logic exists at all
MODEL_RESOLUTION       immutable, verified model artifact
TOKENIZER_RESOLUTION   immutable, verified tokenizer artifact + policy accord
PRECISION_CHECK        declared precision available on the device
MEMORY_ESTIMATE        conservative deterministic estimate vs declared total
DETERMINISM_ASSESSMENT honest category vs the explicit allowed set
CHECKPOINT_COMPATIBILITY  declaration ("none") supported
AUTHORIZATION          aggregate: authorized only with zero ERRORs
```

Findings are immutable (`PreflightFinding`: stage, code, severity, message,
deterministic detail, affected identity, remediation category) and ordered by
stage.

## 7. Memory estimation

`estimate_training_memory_bytes`: weights + gradients at declared precision,
AdamW float32 moments (8 bytes/param), a conservative per-token activation
allowance (8192 B default), x1.25 integer overhead — an APPROXIMATION whose
only job is to refuse obviously impossible plans before model loading.
Parameter count must be explicit (resolved metadata) — never inferred from a
model name. Compared against DECLARED total device memory; live free-memory
readings are deliberately unused (unstable), and undeclared total memory
refuses fail-closed.

## 8. Determinism assessment

Categories: `deterministic_simulated`, `deterministic_supported`,
`best_effort_deterministic`, `nondeterministic`, `unsupported`. CPU with
deterministic algorithms → supported (explanation still notes bit-identical
weights are unproven until an actual run); CUDA → best-effort (kernels
without deterministic implementations); no deterministic mode →
nondeterministic; missing backend → unsupported. The default policy allows
ONLY `deterministic_supported`; accepting best-effort requires the caller to
pass it explicitly (`allowed_determinism`), producing a visible WARNING —
nothing downgrades silently.

## 9. Execution authorization

`TrainingExecutionAuthorization` (`trainauth-…`, deterministic over all
evidence, no timestamps): plan id+digest, corpus id+digest, backend spec id,
snapshot id, embedded resolved artifacts, device capability id, determinism
category, checkpoint format id, the complete ordered findings, and the
authorized boolean. Parse-time validity: all 12 stages present and ordered,
`authorized=True` with any ERROR unrepresentable, authorization requires
verified model+tokenizer resolutions. Authorization is evidence that ONE
environment was suitable at inspection time; it does not change plan identity
and a fresh environment produces a fresh id.

## 10. Persistence

```
training-authorizations/<authorization_id>/
    manifest.json  environment.json  findings.json  authorization.json
```

Immutable writer (`.INCOMPLETE`, canonical JSON, path-sorted hashes, no
overwrite, post-write verification, snapshot↔authorization binding enforced);
self-validating manifest with a non-recursive `authdig-…` digest over all
evidence ids and file hashes; fail-closed verifier that RECOMPUTES validity
(never trusting the stored authorized boolean), cross-checks findings.json
against the embedded findings, re-parses the snapshot (secret fields
unrepresentable), and rejects mutable persisted revisions; fail-closed
reader. No model/tokenizer bytes, no training examples, no checkpoint bytes.

## 11. Optional-dependency strategy

Heavy ML libraries are NOT dependencies of the core package in this gate —
not even optional extras: Gate 10E never imports them, so declaring install
metadata would be an untested promise. Their presence is runtime evidence
(metadata-observed), and their absence is a structured preflight finding,
never an import-time crash. The `verifiednet[training-hf]` extras group is
defined in Gate 10F, the gate that actually imports the packages. The only
new core dependency is `packaging` (PEP 440 version comparison). The AST
import boundary for `verifiednet.training` is fully intact.

## 12. Offline versus integration behavior

The complete offline suite uses `FakeEnvironmentProbe` + fake resolvers: no
torch, no transformers, no CUDA, no model/tokenizer files, no network. One
optional integration test (deselected by default) runs the real CPU-only
system probe, performs PREFLIGHT ONLY, downloads nothing, computes nothing,
and asserts structural behavior — on an unprepared machine, honest refusal is
the expected outcome.

## 13. Guarantees proven by test

Deterministic ids at every level with per-input sensitivity; PEP 440
correctness (property-tested, incl. non-lexicographic ordering); memory
arithmetic exactness and monotonicity (property-tested); package-record order
independence; every-error-forces-refusal across resolver and probe sabotage;
best-effort-requires-acknowledgement; fake-plan refusal on the real backend;
unverified/mismatched plan+corpus refusal with visible skips; the full store
corruption matrix (corrupt snapshot, malformed finding, tampered digest,
flipped authorized boolean, missing files, overwrite); pipeline under ML
import traps + a global dynamic-import trap + subprocess/network/inference
sabotage; source immutability across TEN upstream artifact classes (runs →
simulated checkpoint); evaluation isolation (different benchmark rankings →
byte-identical authorization); environment sensitivity (one capability at a
time → new snapshot/authorization id, same plan id); sensitive-data exclusion
(injected fake credentials never appear in persisted bytes); build-twice
byte-identical directories; and snapshot schemas that cannot represent host
secrets.

## 14. What does not exist yet

Real training execution, gradients, weight mutation, real checkpoints,
checkpoint-backed prediction, benchmark integration of trained models, the
torch-backed environment probe and local-cache resolvers, distributed
training, warm starts, adapter modes. The next boundary is a deliberately
small **Gate 10F: first bounded real training execution** — a tiny approved
model and corpus slice, consuming a verified authorization, before any full
project fine-tune is attempted.
