"""Gate 18B — v2 evidence-representation evaluation (matched inference).

The Gate 18B experiment changes the model-visible evidence representation, so the
base and treatment SLM arms must be evaluated under the SAME v2 features and v2
prompt. This module supplies exactly that, reusing every frozen Gate 7/9 scoring,
record, metric, confusion, comparison, and ranking primitive UNCHANGED — only the
per-example model-visible input (the v2 prompt rendered from v2 observable
features) differs. It adds no new scoring, normalization, ranking, or parser
logic.
"""

from __future__ import annotations

from verifiednet.common.canonical import canonical_json_str
from verifiednet.datasets.evidence_features import (
    DatasetFeaturesV2,
    audit_features_v2,
)
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.evaluation.baseline import BaselineSpec, derive_baseline_id
from verifiednet.evaluation.benchmark import (
    BENCHMARK_VERSION,
    BenchmarkResult,
    BenchmarkSpec,
    compute_comparison_row,
    compute_ranking,
    derive_benchmark_id,
)
from verifiednet.evaluation.contract import EvaluationTask, NormalizationPolicy
from verifiednet.evaluation.engine import (
    EvaluationError,
    EvaluationRun,
    compute_aggregate_metrics,
    compute_confusion,
    compute_partition_summaries,
    derive_evaluation_id,
)
from verifiednet.evaluation.inference import (
    BackendUnavailableError,
    DecodingConfig,
    InferenceBackend,
    InferenceError,
    InferenceTimeoutError,
)
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
    verify_prediction_id,
)
from verifiednet.evaluation.prompt import (
    render_diagnosis_prompt_v2,
)
from verifiednet.evaluation.scoring import build_record
from verifiednet.evaluation.slm import (
    build_backend_invalid_prediction,
    parse_backend_response,
)

V2_SLM_PREDICTOR_VERSION = 1


class V2SlmPredictor:
    """A v2-prompt SLM predictor: renders the deployed v2 prompt from v2
    observable features and parses the backend response with the FROZEN Gate 8
    parser. Used identically for the base and treatment arms — the underlying
    inference backend (base snapshot vs verified checkpoint) is the only
    difference, so the two arms are byte-matched on features, prompt, tokenizer,
    decoding, parser, and scoring.
    """

    def __init__(
        self,
        *,
        task: EvaluationTask,
        backend: InferenceBackend,
        v2_prompt_template_id: str,
        model_identity: str,
        predictor_name: str,
        decoding: DecodingConfig | None = None,
        normalization: NormalizationPolicy | None = None,
        candidate_families: tuple[str, ...],
    ) -> None:
        self._task_id = task.task_id
        self._backend = backend
        self._decoding = decoding or DecodingConfig()
        if self._decoding.temperature != 0.0:
            raise EvaluationError("v2 SLM prediction requires greedy decoding")
        self._norm = normalization or NormalizationPolicy()
        self._candidates = frozenset(
            self._norm.normalize(f) for f in candidate_families)
        cfg = {
            "v2_prompt_template_id": v2_prompt_template_id,
            "model_identity": model_identity,
            "decoding": canonical_json_str(self._decoding),
            "normalization_policy_id": self._norm.policy_id,
        }
        self._spec = BaselineSpec(
            baseline_name=predictor_name,
            rule_set_version=V2_SLM_PREDICTOR_VERSION,
            task_id=task.task_id, rule_configuration=cfg,
            baseline_id=derive_baseline_id(
                schema_version=1, baseline_name=predictor_name,
                baseline_version=1, rule_set_version=V2_SLM_PREDICTOR_VERSION,
                task_id=task.task_id, rule_configuration=cfg))

    @property
    def spec(self) -> BaselineSpec:
        return self._spec

    def predict(
        self, features: DatasetFeaturesV2
    ) -> DiagnosisPrediction | AbstentionPrediction | InvalidPrediction:
        prompt = render_diagnosis_prompt_v2(features)
        payload = features.model_dump(mode="json")
        try:
            response = self._backend.generate(prompt, decoding=self._decoding)
        except BackendUnavailableError as exc:
            return self._invalid(payload, "backend_unavailable", str(exc))
        except InferenceTimeoutError as exc:
            return self._invalid(payload, "inference_timeout", str(exc))
        except InferenceError as exc:
            return self._invalid(payload, "backend_error", str(exc))
        return parse_backend_response(
            response.text, baseline_id=self._spec.baseline_id,
            task_id=self._task_id, features_payload=payload,
            normalization=self._norm, normalized_candidates=self._candidates)

    def _invalid(
        self, payload: dict[str, object], reason: str, raw: str
    ) -> InvalidPrediction:
        return build_backend_invalid_prediction(
            baseline_id=self._spec.baseline_id, task_id=self._task_id,
            features_payload=payload, reason_code=reason, raw_excerpt=raw)


def evaluate_prepared_corpus_v2(
    prepared: LoadedPrepared,
    predictor: V2SlmPredictor,
    task: EvaluationTask,
    *,
    v2_features: dict[str, DatasetFeaturesV2],
    feature_policy_v2_id: str,
) -> EvaluationRun:
    """Evaluate a v2 SLM predictor over pre-resolved v2 features per example.

    Mirrors the frozen ``evaluate_prepared_corpus`` EXACTLY except: the
    model-visible input is the v2 features (audited by the v2 firewall) and the
    run is bound to the v2 feature policy. All scoring/records/metrics/confusion/
    evaluation-id logic is the frozen Gate 7 machinery, reused unchanged.
    """
    spec = predictor.spec
    if spec.task_id != task.task_id:
        raise EvaluationError("predictor was built for a different task")
    manifest = prepared.manifest
    permitted = set(task.permitted_partitions)

    records = []
    for example in prepared.examples:
        if example.trace.partition not in permitted:
            raise EvaluationError(
                f"partition {example.trace.partition.value} not permitted")
        features = v2_features.get(example.trace.example_id)
        if features is None:
            raise EvaluationError(
                f"no v2 features resolved for {example.trace.example_id}")
        leak = audit_features_v2(features)
        if not leak.passed:
            raise EvaluationError("v2 feature-leakage audit failed; refusing")
        if features.feature_policy_id != feature_policy_v2_id:
            raise EvaluationError("inconsistent v2 feature policy across corpus")
        prediction = predictor.predict(features)
        payload = features.model_dump(mode="json")
        if not verify_prediction_id(
            prediction, baseline_id=spec.baseline_id, task_id=task.task_id,
            feature_policy_id=feature_policy_v2_id, feature_payload=payload,
        ):
            raise EvaluationError("predictor produced a non-deterministic id")
        records.append(build_record(
            task_id=task.task_id, baseline_id=spec.baseline_id,
            feature_policy_id=feature_policy_v2_id,
            label_policy_id=manifest.label_policy_id,
            labels=example.labels, trace=example.trace, prediction=prediction,
            normalization=task.normalization))

    ordered = tuple(sorted(records, key=lambda r: r.example_id))
    metrics = compute_aggregate_metrics(ordered)
    confusion = compute_confusion(ordered)
    summaries = compute_partition_summaries(ordered)
    evaluation_id = derive_evaluation_id(
        task_id=task.task_id, baseline_id=spec.baseline_id,
        prepared_digest=manifest.prepared_digest,
        scoring_policy_version=task.scoring_policy_version,
        prediction_ids=tuple(r.prediction_id for r in ordered), metrics=metrics)
    return EvaluationRun(
        task=task, baseline_spec=spec, prepared_digest=manifest.prepared_digest,
        dataset_digest=manifest.source_dataset_digest,
        feature_policy_id=feature_policy_v2_id,
        label_policy_id=manifest.label_policy_id, evaluation_id=evaluation_id,
        records=ordered, metrics=metrics, confusion=confusion,
        partition_summaries=summaries)


def benchmark_from_runs(
    runs: tuple[EvaluationRun, ...],
    *,
    task: EvaluationTask,
    prepared_digest: str,
    benchmark_name: str = "multi_predictor_diagnosis",
) -> BenchmarkResult:
    """Build the Gate 9 benchmark from pre-computed evaluation runs.

    Reuses the frozen ``compute_comparison_row`` / ``compute_ranking`` exactly;
    only the run-production step differs from ``run_benchmark`` (some runs are v2
    SLM arms, some are the frozen deterministic baselines). Ranking criteria are
    unchanged.
    """
    if not runs:
        raise EvaluationError("a benchmark needs at least one run")
    identifiers = [r.baseline_spec.baseline_id for r in runs]
    if len(identifiers) != len(set(identifiers)):
        raise EvaluationError("duplicate predictor identifier in the benchmark")
    comparison = tuple(sorted(
        (compute_comparison_row(r) for r in runs),
        key=lambda row: row.predictor_identifier))
    ranking = compute_ranking(comparison)
    spec = BenchmarkSpec(
        benchmark_name=benchmark_name, task_id=task.task_id,
        prepared_digest=prepared_digest,
        predictor_identifiers=tuple(sorted(identifiers)),
        normalization_policy_id=task.normalization.policy_id,
        scoring_policy_version=task.scoring_policy_version,
        benchmark_id=derive_benchmark_id(
            benchmark_version=BENCHMARK_VERSION, benchmark_name=benchmark_name,
            task_id=task.task_id, prepared_digest=prepared_digest,
            predictor_identifiers=tuple(identifiers),
            normalization_policy_id=task.normalization.policy_id,
            scoring_policy_version=task.scoring_policy_version))
    return BenchmarkResult(
        spec=spec, evaluation_runs=tuple(runs), comparison=comparison,
        ranking=ranking)
