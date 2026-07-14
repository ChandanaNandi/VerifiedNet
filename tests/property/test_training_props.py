"""Gate 10A property tests: id stability, canonical targets, leak detection."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    audit_training_example,
    derive_training_example_id,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
)
from verifiednet.training.corpus import (
    FORBIDDEN_INPUT_TOKENS,
    SupervisedTrainingExample,
    SupervisedTrainingInput,
    SupervisedTrainingTarget,
    TrainingLeakageCode,
    TrainingTraceMetadata,
)

pytestmark = pytest.mark.property

_TASK = "task-0000000000000000"
_families = st.sampled_from([
    "bgp_neighbor_removal", "bgp_prefix_withdrawal",
    "bgp_remote_as_mismatch", "iface_admin_shutdown",
])


def _example(input_text: str, target_text: str) -> SupervisedTrainingExample:
    itpl = diagnosis_input_template(task_id=_TASK,
                                    feature_policy_id="feat-0000000000000000")
    ttpl = diagnosis_target_template(task_id=_TASK)
    policy = diagnosis_training_policy(task_id=_TASK, input_template=itpl,
                                       target_template=ttpl)
    trace = TrainingTraceMetadata(
        source_example_id="ex-0123456789abcdef", source_group_id="grp-0123456789abcdef",
        task_id=_TASK, training_data_policy_id=policy.training_data_policy_id,
        input_template_id=itpl.input_template_id,
        target_template_id=ttpl.target_template_id,
        feature_policy_id="feat-0000000000000000",
        label_policy_id="label-0000000000000000", source_schema_version=1)
    eid = derive_training_example_id(
        source_example_id=trace.source_example_id, task_id=trace.task_id,
        training_data_policy_id=trace.training_data_policy_id,
        input_template_id=trace.input_template_id,
        target_template_id=trace.target_template_id,
        rendered_input=input_text, rendered_target=target_text)
    return SupervisedTrainingExample(
        training_example_id=eid, input=SupervisedTrainingInput(text=input_text),
        target=SupervisedTrainingTarget(text=target_text), trace=trace)


@given(family=_families)
@settings(max_examples=50)
def test_target_canonicalization_is_stable(family: str) -> None:
    ttpl = diagnosis_target_template(task_id=_TASK)
    assert ttpl.render(family) == ttpl.render(family)
    assert ttpl.render(family).encode() == ttpl.render(family).encode()


@given(seed=st.integers(0, 20))
@settings(max_examples=21, deadline=None)
def test_policy_and_template_ids_stable(seed: int) -> None:
    fp = f"feat-{seed:016x}"
    a = diagnosis_input_template(task_id=_TASK, feature_policy_id=fp)
    b = diagnosis_input_template(task_id=_TASK, feature_policy_id=fp)
    assert a.input_template_id == b.input_template_id
    # a different feature policy changes the template id
    other = diagnosis_input_template(task_id=_TASK,
                                     feature_policy_id=f"feat-{seed + 1:016x}")
    assert other.input_template_id != a.input_template_id


@given(token=st.sampled_from(sorted(FORBIDDEN_INPUT_TOKENS)),
       prefix=st.text(min_size=0, max_size=20).filter(
           lambda s: not any(t in s for t in FORBIDDEN_INPUT_TOKENS)))
@settings(max_examples=100)
def test_forbidden_token_anywhere_in_input_is_detected(token, prefix) -> None:
    text = f"clean observation text {prefix} {token} more text"
    example = _example(text, '{"fault_family":"x","prediction_type":"diagnosis"}')
    result = audit_training_example(example)
    assert result.passed is False
    assert any(f.code is TrainingLeakageCode.FORBIDDEN_INPUT_KEY
               for f in result.errors)


@given(family=_families)
@settings(max_examples=20)
def test_clean_example_passes_audit(family: str) -> None:
    ttpl = diagnosis_target_template(task_id=_TASK)
    example = _example("clean observation metadata about the run",
                       ttpl.render(family))
    assert audit_training_example(example).passed is True


def test_unauthorized_target_field_is_detected() -> None:
    example = _example(
        "clean input",
        '{"fault_family":"x","prediction_type":"diagnosis","confidence":"high"}')
    result = audit_training_example(example)
    assert result.passed is False
    assert any(f.code is TrainingLeakageCode.UNAUTHORIZED_TARGET_FIELD
               for f in result.errors)
