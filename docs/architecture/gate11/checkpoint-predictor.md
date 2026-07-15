# Gate 11 — Verified Checkpoint-Backed Predictor

**Status:** IMPLEMENTED (Gate 11). The first predictor whose behavior comes
from real trained weights. It implements ADR-0028 on top of ADR-0020 (models
only behind the feature-only boundary), ADR-0025/0027 (checkpoints are
verified artifacts), and reuses the entire Gate 8 pipeline unchanged. **No
evaluation run, benchmark, metric, ranking, training, or weight change exists
in this gate** — Gate 11 proves only that a verified checkpoint can produce a
prediction through the existing boundary.

## 1. Position in the architecture

```
Verified real checkpoint (Gate 10F, verifiednet.real-checkpoint-v1)
        ↓ assess_checkpoint_prediction_eligibility  (fail-closed, disk-only)
VerifiedCheckpointBundle          (verified descriptors + payload paths;
        ↓                          NO model loading at construction)
HfCheckpointInferenceBackend      (lazy ML, local-only, CPU fp32, eval mode,
        ↓                          inference_mode, greedy, offline forced)
VerifiedCheckpointPredictor       (Gate 7/8 boundary:
        ↓                          predict(DatasetFeatures) → DatasetPrediction)
Gate 8 prompt template → Gate 8 parser/normalizer → Gate 8 prediction union
```

The prediction interface, prompt template, response parser, normalization
policy, prediction union (`DiagnosisPrediction | AbstentionPrediction |
InvalidPrediction`), and prediction-id algorithm are the EXISTING Gate 7/8
ones — Gate 11 introduces no new prompt and no parallel parsing path. The
Gate 8 parser was extracted into shared module-level functions
(`parse_backend_response`, `build_backend_invalid_prediction` in
`evaluation/slm.py`); `SlmPredictor` delegates to them, so both predictors are
provably the same pipeline.

## 2. Eligibility: trust comes only from the on-disk artifact

`assess_checkpoint_prediction_eligibility(checkpoint_dir, compatibility)`
runs the full Gate 10F structural verification (manifest self-validation,
recomputed file hashes, safetensors structural parse, no symlinks, no
undeclared files, no optimizer/scheduler/RNG/resume state, no incomplete
marker) and then adds Gate 11 checks: genuine
`verifiednet.real-checkpoint-v1` format only (a Gate 10D SIMULATED checkpoint
cannot even parse as a real manifest), `simulated is False`,
`loadable_as_real_model is True`, `evaluated/benchmarked are False`, the
architecture is inside the narrow inference-compatibility scope, and the
lineage records a completed execution (`completed_optimizer_steps >= 1`).
There is NO parameter for a caller-supplied manifest — it cannot be trusted,
so it cannot be passed. The result is a structured
`CheckpointEligibilityResult` (per-rule `DatasetCheck`s), never a bare bool
hiding the reason.

## 3. The bundle: verified descriptors, no model loading

`load_verified_checkpoint_bundle` fail-closes on ineligibility and returns a
`VerifiedCheckpointBundle`: the manifest, the inference compatibility, the
eligibility evidence, and role-resolved payload paths (weights / config /
tokenizer snapshot). Construction never imports an ML library and never reads
weight bytes into a model. `bundle.fingerprint()` returns fresh sha256 hashes
of every checkpoint file (the immutability-proof primitive), and
`bundle.reverify()` re-assesses eligibility at the moment of use — mirroring
the Gate 10E rule that environmental trust is revalidated when USED: a
checkpoint mutated after bundling is refused before any weight byte is
interpreted.

## 4. Inference scope: narrow, local, read-only

`CheckpointInferenceCompatibility` (`infcompat-…`) Literal-locks the scope:
`hf-transformers-local` backend family, tokenizer from the checkpoint payload
only, `local_files_only`, no remote code, no quantization, no adapters, no
network, single process, single device; the supported-architecture list
defaults to `("Qwen2ForCausalLM",)`. `CheckpointInferenceDevicePolicy`
(`infdev-…`) Literal-locks CPU + float32 + no silent fallback — CPU is the
honest choice because MPS/CUDA inference behavior is not modeled by any
VerifiedNet contract; a device change requires a new policy and therefore a
new predictor id. Load and inference precision are explicitly `float32`
(the Gate 10F.1 checkpoint stores fp32 weights).

`HfCheckpointInferenceBackend` (`evaluation/hfinference.py`) is the second
sanctioned lazy-ML site (mirror of `training/hfexecutor.py`): torch and
transformers import only inside function bodies, HF offline mode is forced
before any Transformers call, the model loads lazily on first `generate` and
is cached (Option B lifecycle), goes to eval mode with every parameter's
gradient disabled, and generates under `torch.inference_mode()` with strictly
greedy decoding (`do_sample=False`, one beam, fixed `max_new_tokens`, no
top-p/top-k). Only the generated completion is decoded — never the echoed
prompt. An overlength prompt (prompt tokens + max new tokens > model context)
is a structured refusal. Framework determinism claims are unchanged from Gate
8: deterministic prompt construction, decoding parameters, and parsing —
model-output bit-identity across platforms is NOT claimed.

## 5. Identity

```
predictor_id = "ckptpred-" + sha256_canonical({schema_version,
    predictor_version, checkpoint_id, checkpoint_digest, checkpoint_format_id,
    compatibility_id, model_spec_id, tokenizer_spec_id, prompt_template_id,
    decoding_config, normalization_policy_id, backend_family,
    inference_precision, device_policy})[:24]
```

Never from a path, host, time, or label. `CheckpointPredictorSpec` is frozen
and self-validating (re-derives its own id; refuses non-`realckpt-`/`realdig-`
references), and the predictor exposes a Gate-7 `BaselineSpec` whose
`rule_configuration` embeds the full spec — evaluation manifests can persist
it with no structural change, ready for a FUTURE Gate 9 comparison that this
gate does not run.

## 6. Failure semantics

Backend failure is never abstention (abstention is a semantic model decision,
not an error path): unavailable → `invalid/backend_unavailable`, timeout →
`invalid/inference_timeout`, any other structured backend refusal (overlength,
load failure, unsupported decoding) → `invalid/backend_error`; unusable output
flows through the unchanged Gate 8 reasons (`malformed_json`,
`not_an_object`, `missing_fault_family`, `unknown_fault_family`,
`unsupported_prediction_type`). Construction-time violations (non-greedy
decoding, ineligible checkpoint) raise `CheckpointPredictionError`.

## 7. Boundary changes (strengthened, not weakened)

The AST guard now allows exactly ONE evaluation file
(`evaluation/checkpointpred.py`) to import exactly ONE training module
(`verifiednet.training.realckptstore`) — evaluation consumes verified
training ARTIFACTS; training still never imports evaluation (ADR-0022
unchanged). ML libraries are now banned in the evaluation package exactly as
in training, with `evaluation/hfinference.py` the one lazy-import exemption,
proven function-level by a dedicated test. Offline CI never imports torch or
transformers and never touches a real checkpoint payload.

## 8. Proof obligations discharged by tests

Feature-only boundary (spied prompts contain no example identity, label,
split, digest, scenario, rejection code — nor any checkpoint/lineage id);
checkpoint immutability (full-file fingerprints identical before/after a
whole evaluation pass; re-verified after); no-training traps (static AST scan
of the backend: no backward/step/zero_grad/train/save/state_dict/optimizer
references; eval + requires_grad_ + inference_mode required present);
no-network (stdlib client sabotaged, pipeline completes; offline env forced);
tamper evidence (any single byte flip in any checkpoint file breaks
eligibility); build-twice determinism (identical specs, ids, and predictions);
and Literal locks on every policy. The optional integration test
(`tests/integration/test_real_checkpoint_inference.py`) is double-gated on the
`integration` marker + `VERIFIEDNET_RUN_REAL_CHECKPOINT_INFERENCE=1` +
`VERIFIEDNET_REAL_CHECKPOINT_DIR` + the `training-hf` extras and proves the
chain on the real Gate 10F.1 checkpoint: verify → load locally → ≥1 real
prediction from real `DatasetFeatures` → bytes unchanged. It performs no
correctness evaluation.

## 9. Explicitly out of scope (the Gate 12 boundary)

No evaluation run of the checkpoint predictor, no benchmark, no metric, no
ranking, no accuracy claim, no comparison against Gate 7 baselines, no
training, no weight change, no RAG/agents/tools, no publication, no
downloads, no other model families. The next boundary is Gate 12: evaluate
the trained checkpoint through the unchanged Gate 7 engine and compare it
through the unchanged Gate 9 benchmark.

**Update (Gate 12):** that boundary is now implemented — the predictor was
evaluated and benchmarked unchanged, against a matched base-model predictor
served by this same inference backend through a `VerifiedInferenceBundle`
protocol (the Gate 12 `VerifiedBaseModelBundle` is the second implementation;
raw directories still cannot reach the backend). See
`../gate12/checkpoint-benchmark.md` and ADR-0029.
