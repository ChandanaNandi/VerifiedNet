# 0028 — Model weights enter prediction only through a verified immutable checkpoint, behind the feature-only boundary

**Status:** Accepted (owner decision, Gate 11)
**Date:** 2026-07-14

## Context

Gate 10F produced the first genuine checkpoint; Gate 8 established that models
participate only behind the feature-only predictor boundary (ADR-0020). The
missing rule was how trained weights may reach prediction at all. Without one,
"just load this directory" paths appear: an HF cache alias, an unverified
export, a checkpoint whose bytes drifted since verification. Separately, the
package graph had no legal route between the training layer (which owns the
verified-checkpoint format) and the evaluation layer (which owns the predictor
boundary) — ADR-0022 forbids training→evaluation, and the one-way guard
forbade evaluation→training.

## Decision

1. **Model weights enter prediction ONLY through a verified immutable real
   checkpoint.** A checkpoint-backed predictor is constructible only from a
   `VerifiedCheckpointBundle`, which is constructible only from a fail-closed
   eligibility assessment of the ON-DISK artifact (full Gate 10F structural
   verification + genuine-format, honesty-flag, architecture-scope, and
   completed-execution checks). There is no API accepting a raw path, an HF
   identifier, or a caller-supplied manifest. Eligibility is re-verified at
   the moment of first load; a mutated checkpoint is refused before any
   weight byte is interpreted.

2. **Checkpoint-backed predictors stay behind the feature-only boundary.**
   The predictor implements the unchanged Gate 7 contract
   (`predict(features: DatasetFeatures) -> DatasetPrediction`) and reuses the
   Gate 8 prompt template, parser, normalization, prediction union, and
   prediction-id algorithm — one pipeline, not a parallel one. Its identity
   (`ckptpred-…`) is a pure content hash over checkpoint identity + digest +
   format + compatibility + specs + prompt + decoding + normalization +
   backend family + precision + device policy — never path/host/time/label.

3. **Evaluation may consume verified training artifacts; the reverse stays
   forbidden.** The dependency arrow follows the artifact flow: training
   PRODUCES verified checkpoints, evaluation CONSUMES them. The AST guard
   allows exactly one evaluation module (`checkpointpred.py`) to import
   exactly one training module (`realckptstore` — the verified-checkpoint
   store), and nothing else; training importing evaluation remains banned
   (ADR-0022 unchanged). ML libraries are banned in evaluation exactly as in
   training, with one sanctioned lazy-import inference site
   (`hfinference.py`), proven function-level.

4. **Inference is read-only and honestly scoped.** Eval mode, gradients
   disabled, `inference_mode`, strictly greedy decoding, local-files-only,
   offline forced, no remote code, no quantization/adapters, CPU + float32
   explicitly recorded with no silent device fallback. No training API is
   referenced in the inference path (statically guarded). Producing a
   prediction creates no evaluation, benchmark, metric, or quality claim —
   those remain separate, later gates.

## Consequences

- A trained model can never reach prediction unverified, and a verified
  checkpoint can never be silently substituted or mutated — any byte change
  breaks eligibility, and any prediction-affecting configuration change
  changes the predictor id.
- The deterministic truth chain stays model-free: weights influence only the
  prediction side of the existing boundary, and backend failures are explicit
  `InvalidPrediction`s, never abstentions or escaping exceptions.
- Offline CI never loads a checkpoint or imports an ML library; real
  inference is a double-gated optional integration test.
- Future predictor backends (other architectures, devices, or engines) must
  arrive as new content-addressed policies/specs, not as edits to this one.

## References

- `docs/architecture/gate11/checkpoint-predictor.md`
- ADR-0020 (feature-only predictor boundary), ADR-0022 (training/evaluation
  isolation), ADR-0025 (checkpoints are verified artifacts), ADR-0027
  (structural verification of real executions and checkpoint publication).
