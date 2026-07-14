# Gate 8 — Base SLM Predictor Benchmark

**Status:** IMPLEMENTED (Gate 8). This document describes the first model-backed
predictor in VerifiedNet: a local SLM that plugs into the Gate 7 evaluation
framework through the exact same feature-only boundary as the deterministic rule
baselines. It implements ADR-0020. Gate 8 is about proving a real language model
can participate in the deterministic, auditable pipeline — not about model
quality. No training, fine-tuning, embedding, retrieval, or remote API is
involved.

## 1. Where the model sits

```
… → Prepared Corpus → Model Predictor → Evaluation → Immutable Evaluation Artifacts
```

The SLM predictor is a `Baseline`: `predict(features: DatasetFeatures) ->
DatasetPrediction`. The evaluation engine passes ONLY `example.features`; the
model never sees labels, trace, identity, split, the prepared example, evaluation
records, the source `IncidentRecord`, or run artifacts (proven by a prompt-content
security test).

## 2. Predictor interface and specification

`SlmPredictor` implements the Gate 7 predictor contract unchanged. Its
`PredictorSpec` is frozen and content-addressed:

`predictor_id = "predictor-" + sha256_canonical({schema_version, predictor_name,
predictor_version, backend, model_identifier, prompt_template_id,
decoding_config_id, normalization_policy_id})[:16]`

To keep the evaluation engine, records, metrics, and digests unchanged, the
predictor exposes a Gate-7 `BaselineSpec` whose `baseline_id` and
`rule_configuration` embed the full `PredictorSpec` (including `predictor_id`).
The evaluation manifest therefore persists the predictor specification with no
structural change, and any prediction-affecting change alters the id.

## 3. Prompt templates

Prompts are never ad hoc. `PromptTemplate` is frozen, versioned, and
content-addressed (`prompt_template_id`); `render(features)` is a pure function of
the model-visible features only. The rendered prompt exposes the allowlisted
features (backend, topology hash, baseline present, onset present/absent), the
fixed candidate fault-family CLASS list (public class space, not the answer for a
specific example), and the required JSON response schema.

## 4. Deterministic decoding

`DecodingConfig` requests greedy decoding: `temperature=0`, a fixed `max_tokens`,
optional fixed `stop` tokens, and an optional `seed` when the backend supports
one. Its `config_id` feeds the predictor id.

**Determinism limitation (documented, not overstated):** a real local backend may
not produce bit-identical text across builds. The framework guarantees
deterministic PROMPT construction, deterministic parsing/validation/normalization,
and a deterministic mapping from a given model output to a prediction. It does NOT
claim model-output bit-identity. The offline fake backend is fully deterministic
end to end.

## 5. Response contract, validation, normalization

The model must return one JSON object: `{"prediction_type": "diagnosis" |
"abstention", "fault_family": <candidate, required iff diagnosis>, "confidence":
"low|medium|high"}`. Model reasoning is not requested, not stored, and never
evaluated — only the structured fields are scored.

Strict validation rejects malformed JSON, a non-object, a missing/empty
`fault_family`, an unknown fault family (not in the candidate class set), and an
unsupported `prediction_type`. A rejected output becomes an explicit
`InvalidPrediction` (with a bounded, non-authoritative `raw_excerpt` for auditing)
— never an exception escaping the engine. Fault families are normalized with the
task's versioned `NormalizationPolicy` (strip + casefold; no fuzzy/semantic
matching).

## 6. The invalid outcome (additive)

`InvalidPrediction` and `OutcomeCategory.INVALID_PREDICTION` are the realization of
the invalid-prediction case anticipated by the Gate 7 record spec. An invalid
prediction is ALWAYS scored incorrect (for accepted and abstention examples
alike), is folded into the accepted `incorrect` count, and is excluded from the
accepted confusion matrix. Crucially it is never scored as a correct abstention on
a rejected example, so a model emitting garbage cannot inflate its score. Rule
baselines never emit invalid, so their evaluations are byte-identical to Gate 7.

## 7. Backends

`InferenceBackend` is a minimal `generate(prompt, *, decoding) ->
InferenceResponse`. `FakeInferenceBackend` is deterministic and offline — the only
backend used by the test suite and the default pipeline; it can return well-formed,
malformed, or edge-case responses and can simulate a timeout or an unavailable
backend. `OllamaBackend` is an OPTIONAL local backend (integration-only) that
imports its network client lazily inside `generate`, so importing the package is
network-free; backend failures surface as `BackendUnavailableError` /
`InferenceTimeoutError`, which the predictor maps to an invalid prediction.

## 8. Integration and persistence

The evaluation engine required no changes. A `SlmPredictor` is injected exactly
like a baseline; evaluation records, metrics, manifest, and digest are the Gate 7
shapes. The evaluation manifest now carries the predictor id and full predictor
specification (embedded in the baseline-spec configuration) without weakening
reproducibility.

## 9. Guarantees proven by test

Features-only boundary (the prompt contains no identity/label/split); no network
(the offline pipeline completes with `urllib` sabotaged); malformed / unknown /
timeout / unavailable all become invalid predictions (no exception escapes);
predictor and prompt ids are deterministic and configuration-sensitive; Gate 7
deterministic baselines continue to pass unchanged. Real local inference is
covered by one optional integration test that skips without a local model.

## 10. Limitations and next steps

The allowlisted features intentionally withhold the answer, so the model — like
the rule baselines — cannot diagnose the specific family from features alone; the
reported accuracies demonstrate INTEGRATION, not model capability, over the tiny
v1 corpus. Gate 8 stops here: no multi-model benchmarking, prompt optimization,
fine-tuning, or retrieval augmentation. Those are later gates, and each would plug
into this same feature-only boundary and immutable result format.

**Update (Gate 11):** the checkpoint-backed predictor now exists behind this
exact boundary. Its response parsing was NOT duplicated — the Gate 8 parser
was extracted into shared module-level functions (`parse_backend_response`,
`build_backend_invalid_prediction`), `SlmPredictor` delegates to them
(behavior and all derived ids unchanged), and `VerifiedCheckpointPredictor`
reuses them together with this document's prompt template, normalization, and
prediction union. See `../gate11/checkpoint-predictor.md` and ADR-0028.
