"""Feature-leakage audit over the SERIALIZED model-visible payload (Part 4).

This audit inspects the actual serialized feature dict — not just the Python
model definition — so a leak injected by a ``model_construct`` bypass or a nested
dict is still caught. It performs two structural checks at ANY nesting depth:

1. FORBIDDEN KEY NAMES — a label, identity, split, or bookkeeping field name
   appearing anywhere in the feature payload (exact name, or any ``dataset_*``).
2. FORBIDDEN VALUES — an evaluator-only scalar value (a label / trace identity
   value) copied verbatim into the feature payload.

It FAILS CLOSED: any ERROR finding forces ``passed=False``.

Guarantee and limitation (documented, not overstated): these checks prove the
ABSENCE of the enumerated structural leaks and of verbatim evaluator-only scalar
values in the model-visible payload. They do NOT and cannot prove the absence of
arbitrary SEMANTIC leakage (e.g. an evidence file whose content implies the
answer) — that is bounded by the feature allowlist and the evidence contract,
not by this audit.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.datasets.features import (
    AbstentionLabels,
    AcceptedLabels,
    DatasetTraceMetadata,
    SeparatedDatasetExample,
)
from verifiednet.datasets.models import LeakageSeverity
from verifiednet.schemas.base import StrictModel

#: Field names that must NEVER appear in a model-visible feature payload.
FORBIDDEN_FEATURE_KEYS: frozenset[str] = frozenset({
    "example_id",
    "group_id",
    "run_id",
    "run_digest",
    "source_index_digest",
    "incident_reference",
    "ledger_reference",
    "transcript_reference",
    "recovery_reference",
    "ground_truth_reference",
    "rejection_code",
    "failed_phase",
    "template_id",
    "scenario_id",
    "fault_family",
    "partition",
    "split",
    "split_policy_id",
    "dataset_digest",
    "dataset_version",
    "dataset_group_id",
    "dataset_split",
    "acceptance_status",
    "stable_identity",
    "expected_outcome",
    "oracle_version",
    "code_commit",
    "example_kind",
    "label_policy_id",
    "kind",
})

_FORBIDDEN_KEY_PREFIX = "dataset_"


class FeatureLeakageCode(StrEnum):
    FORBIDDEN_FEATURE_KEY = "forbidden_feature_key"
    FORBIDDEN_FEATURE_VALUE = "forbidden_feature_value"


class FeatureLeakageError(Exception):
    """A separated example leaked evaluator-only data into its features."""


class FeatureLeakageFinding(StrictModel):
    schema_version: Literal[1] = 1
    code: FeatureLeakageCode
    severity: LeakageSeverity
    json_path: str = ""
    detail: str = ""


class FeatureLeakageResult(StrictModel):
    schema_version: Literal[1] = 1
    passed: bool
    findings: tuple[FeatureLeakageFinding, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _fail_closed(self) -> FeatureLeakageResult:
        has_error = any(f.severity is LeakageSeverity.ERROR for f in self.findings)
        if self.passed and has_error:
            raise ValueError("feature-leakage audit cannot pass with ERROR findings")
        return self

    @property
    def errors(self) -> tuple[FeatureLeakageFinding, ...]:
        return tuple(f for f in self.findings if f.severity is LeakageSeverity.ERROR)


def _walk(node: object, path: str) -> Iterator[tuple[str, str | None, object]]:
    """Yield (json_path, key_or_None, value) for every node in a JSON structure."""
    if isinstance(node, dict):
        for key, val in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, str(key), val
            yield from _walk(val, child)
    elif isinstance(node, (list, tuple)):
        for i, val in enumerate(node):
            child = f"{path}[{i}]"
            yield from _walk(val, child)


def audit_feature_payload(
    payload: dict[str, object], *, forbidden_values: frozenset[str]
) -> list[FeatureLeakageFinding]:
    """Walk a serialized feature dict for forbidden keys and forbidden values."""
    findings: list[FeatureLeakageFinding] = []
    for json_path, key, value in _walk(payload, ""):
        if key is not None and (
            key in FORBIDDEN_FEATURE_KEYS or key.startswith(_FORBIDDEN_KEY_PREFIX)
        ):
            findings.append(FeatureLeakageFinding(
                code=FeatureLeakageCode.FORBIDDEN_FEATURE_KEY,
                severity=LeakageSeverity.ERROR,
                json_path=json_path,
                detail=f"forbidden key {key!r}",
            ))
        if isinstance(value, str) and value in forbidden_values:
            findings.append(FeatureLeakageFinding(
                code=FeatureLeakageCode.FORBIDDEN_FEATURE_VALUE,
                severity=LeakageSeverity.ERROR,
                json_path=json_path,
                detail="evaluator-only value present in features",
            ))
    return findings


def _sensitive_values(
    labels: AcceptedLabels | AbstentionLabels, trace: DatasetTraceMetadata
) -> frozenset[str]:
    """Every evaluator-only scalar that must never appear in a feature payload."""
    values: set[str] = set()

    def _add(node: object) -> None:
        if isinstance(node, str) and node:
            values.add(node)
        elif isinstance(node, dict):
            for v in node.values():
                _add(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _add(v)

    _add(labels.model_dump(mode="json"))
    _add(trace.model_dump(mode="json"))
    # ``backend`` and ``topology_hash`` are legitimately model-visible context and
    # are never placed in labels/trace, so they never enter this set.
    return frozenset(values)


def audit_separated_example(
    separated: SeparatedDatasetExample,
) -> FeatureLeakageResult:
    """Audit one separated example's model-visible features (fail closed)."""
    forbidden = _sensitive_values(separated.labels, separated.trace)
    payload = separated.features.model_dump(mode="json")
    findings = audit_feature_payload(payload, forbidden_values=forbidden)
    has_error = any(f.severity is LeakageSeverity.ERROR for f in findings)
    return FeatureLeakageResult(passed=not has_error, findings=tuple(findings))
