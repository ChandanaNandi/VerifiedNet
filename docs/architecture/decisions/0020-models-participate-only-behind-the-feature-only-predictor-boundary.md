# 0020 — A model participates only behind the feature-only predictor boundary

**Status:** Accepted (owner decision, Gate 8)
**Date:** 2026-07-13

## Context

Gate 8 introduces the FIRST model-backed predictor (a local SLM) into VerifiedNet.
Every prior gate is deterministic and model-free; ADR-0019 fixed the evaluation
boundary (baselines receive only features; the evaluator alone holds labels) and
kept evaluation deterministic and offline. Admitting a language model risks
eroding exactly those guarantees — a model could be handed labels, could make the
pipeline non-reproducible, could turn malformed output into an exception that
corrupts a run, or could quietly become a second source of truth. This ADR fixes
how a model is allowed to participate so those guarantees survive.

## Decision

1. **A model participates ONLY as a predictor behind the Gate 7 feature-only
   boundary.** A model-backed predictor implements the same
   `predict(features: DatasetFeatures) -> DatasetPrediction` contract as a rule
   baseline. It receives ONLY model-visible features; it never receives labels,
   trace metadata, identity, split, the prepared example, evaluation records, the
   source `IncidentRecord`, or run artifacts. The prompt is rendered from features
   only (the candidate-family CLASS list is public class space, not the answer).

2. **The evaluation framework is unchanged.** The engine, per-example records,
   metrics, confusion, manifest, digest algorithm, verifier, and reader are the
   same as Gate 7. A predictor is injected, not a new pipeline. The predictor
   specification is persisted inside the existing evaluation manifest (embedded in
   the baseline-spec configuration), so a run remains fully reproducible and
   auditable with no structural change.

3. **Prompts and predictors are explicit, versioned, and content-addressed.** A
   prompt template has a deterministic `prompt_template_id`; a predictor has a
   deterministic `predictor_id` derived from name, version, backend, model
   identifier, prompt template, decoding configuration, and normalization policy.
   Any change that can affect predictions changes the id.

4. **Structured output is strictly parsed, validated, and normalized; malformed
   output is an explicit INVALID prediction, never an exception.** Invalid output
   is always scored incorrect and is never counted as a (correct) abstention on a
   rejected example, so a model that emits garbage cannot inflate its score. Model
   reasoning is not stored and is never evaluated; only structured prediction
   fields are scored.

5. **Determinism is honest.** The framework requests greedy decoding and
   guarantees deterministic prompt construction, parsing, validation,
   normalization, and the mapping from a given model output to a prediction. It
   does NOT claim bit-identical model text across builds; that limitation is
   documented rather than overstated. The offline test suite is fully
   deterministic via a fake backend.

6. **Real inference is optional and offline-by-default.** Offline CI is completely
   model-free: only a deterministic fake backend runs. A real local backend
   (Ollama) is exercised solely by optional integration tests that skip when no
   local model is available, and its network client is imported lazily so the
   package stays network-free on import. No remote API, embedding, vector store,
   retrieval, or training is introduced.

## Consequences

- A real language model is proven to participate in the deterministic, auditable
  pipeline without weakening reproducibility, traceability, the feature-only
  boundary, or evaluation integrity.
- Future model work (multi-model benchmarking, prompt iteration, fine-tuning,
  retrieval) plugs into the same boundary and the same immutable result format;
  none of it may bypass the evaluator or reach labels.
- The invalid-prediction outcome anticipated by the Gate 7 record spec is now
  realized; baseline evaluations are byte-identical to Gate 7 (baselines never
  emit invalid), so existing results and digests are unaffected.

## References

- `../gate8/slm-predictor.md`
- ADR-0018 (datasets are derived, leakage-safe, model-free), ADR-0019 (evaluation
  is deterministic and model-free; baselines receive only features)
