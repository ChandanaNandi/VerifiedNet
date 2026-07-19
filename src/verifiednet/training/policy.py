"""Training eligibility policy + input/target templates (Gate 10A).

These three frozen, versioned, content-addressed contracts define exactly which
prepared examples may enter supervised training and what a training example's
input and target look like:

* ``TrainingDataPolicy`` — eligibility. The Gate 10A policy is LOCKED by Literal
  types to: source partition ``train`` only, accepted-fault examples only,
  accepted-diagnosis labels only, abstention excluded. A policy permitting
  anything else cannot be constructed in this gate.
* ``TrainingInputTemplate`` — the exact model input a future trainer will
  tokenize, rendered as a pure function of the model-visible ``DatasetFeatures``
  only. It is deliberately INDEPENDENT of the Gate 8 inference prompt: the
  training package may not import ``verifiednet.evaluation`` (evaluation
  isolation, ADR-0022), so the two templates carry distinct explicit identities
  rather than a silently shared implementation.
* ``TrainingTargetTemplate`` — the exact supervised target, serialized as
  canonical JSON so equivalent labels are byte-identical.

Every id is a pure content hash — no timestamps, hosts, users, env, or paths.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.canonical import canonical_json_str
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.evidence_features import (
    DatasetFeaturesV2,
    render_evidence_observation_block,
)
from verifiednet.datasets.features import DatasetFeatures
from verifiednet.schemas.base import StrictModel

TRAINING_POLICY_VERSION = 1
INPUT_TEMPLATE_VERSION = 1
TARGET_TEMPLATE_VERSION = 1

#: The fault-family class space presented in the training input (the four Gate 5
#: families). This is the public classification class space — not the answer for
#: any specific example — mirroring the Gate 8 prompt's class list by DESIGN
#: DECISION, not by shared code (see module docstring).
TRAINING_CANDIDATE_FAMILIES: tuple[str, ...] = (
    "bgp_neighbor_removal",
    "bgp_prefix_withdrawal",
    "bgp_remote_as_mismatch",
    "iface_admin_shutdown",
)

_INPUT_INSTRUCTIONS = (
    "You are a deterministic network fault-diagnosis classifier. You are given "
    "only observation metadata about one verified network run. Decide which "
    "fault family the observed fault belongs to, strictly from the candidate "
    "list."
)

_TARGET_SCHEMA_DESCRIPTION = (
    'One canonical JSON object: {"fault_family": <candidate family>, '
    '"prediction_type": "diagnosis"} with keys sorted and no whitespace.'
)

#: Gate 16A — the MIRRORED deployed-inference contract text (ADR-0034).
#: These two constants restate, verbatim, the frozen Gate 8 prompt's public
#: instruction and response-schema sentences so that training-input v2 renders
#: byte-identically to the deployed prompt WITHOUT importing
#: ``verifiednet.evaluation`` (ADR-0022 unchanged). They are a mirrored
#: contract, never shared code; cross-layer byte-equality is enforced by
#: contract tests in ``tests/`` (where importing both layers is legal), so any
#: drift between the mirror and the prompt fails CI loudly. The v2 model
#: validator additionally locks a v2 template to EXACTLY this text — a drifted
#: v2 template is unrepresentable, not merely untested.
CONTRACT_ALIGNED_TEMPLATE_VERSION: Literal[2] = 2
CONTRACT_ALIGNED_TEMPLATE_NAME = "contract_aligned_fault_diagnosis"

_CONTRACT_INSTRUCTIONS = (
    "You are a deterministic network fault-diagnosis classifier. You are given "
    "only observation metadata about one verified network run. Decide whether a "
    "fault occurred and, if so, which fault family it belongs to. You must choose "
    "a fault family strictly from the candidate list, or abstain."
)

_CONTRACT_RESPONSE_SCHEMA = (
    'Respond with ONE JSON object and nothing else: '
    '{"prediction_type": "diagnosis" | "abstention", '
    '"fault_family": <one of the candidate families, required iff diagnosis>, '
    '"confidence": "low" | "medium" | "high"}. '
    'If there is no fault to diagnose, use prediction_type "abstention".'
)


def derive_training_data_policy_id(
    *,
    schema_version: int,
    policy_version: int,
    allowed_partition: str,
    allowed_example_kind: str,
    allowed_label_kind: str,
    task_id: str,
    input_template_id: str,
    target_template_id: str,
    include_abstention: bool,
) -> str:
    payload = {
        "schema_version": schema_version,
        "policy_version": policy_version,
        "allowed_partition": allowed_partition,
        "allowed_example_kind": allowed_example_kind,
        "allowed_label_kind": allowed_label_kind,
        "task_id": task_id,
        "input_template_id": input_template_id,
        "target_template_id": target_template_id,
        "include_abstention": include_abstention,
    }
    return "trainpolicy-" + sha256_canonical(payload)[:16]


def derive_input_template_id(
    *,
    schema_version: int,
    template_version: int,
    name: str,
    instructions: str,
    candidate_families: tuple[str, ...],
    task_id: str,
    feature_policy_id: str,
) -> str:
    payload = {
        "schema_version": schema_version,
        "template_version": template_version,
        "name": name,
        "instructions": instructions,
        "candidate_families": sorted(candidate_families),
        "task_id": task_id,
        "feature_policy_id": feature_policy_id,
    }
    return "traintmpl-" + sha256_canonical(payload)[:16]


def derive_target_template_id(
    *,
    schema_version: int,
    target_version: int,
    task_id: str,
    output_schema: str,
) -> str:
    payload = {
        "schema_version": schema_version,
        "target_version": target_version,
        "task_id": task_id,
        "output_schema": output_schema,
    }
    return "traintgt-" + sha256_canonical(payload)[:16]


class TrainingInputTemplate(StrictModel):
    """The frozen, content-addressed training-input contract.

    ``render`` is pure and deterministic over model-visible features ONLY: the
    exact ordered fields exposed to the model are ``backend``, ``topology_hash``,
    baseline-evidence presence, and onset-evidence presence — plus the fixed
    candidate class list and the required output schema. No identity, label,
    split, digest, or policy id ever enters the rendered text (the
    ``feature_policy_id`` the template SUPPORTS is template metadata, never
    prompt text).
    """

    schema_version: Literal[1] = 1
    #: 1 = Gate 10A serialization (byte-frozen); 2 = Gate 16A contract-aligned
    #: serialization (byte-identical to the deployed Gate 8 prompt rendering).
    template_version: Literal[1, 2] = 1
    name: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    candidate_families: tuple[str, ...] = Field(min_length=1)
    task_id: str = Field(min_length=1)
    feature_policy_id: str = Field(min_length=1)
    input_template_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingInputTemplate:
        if sorted(self.candidate_families) != list(self.candidate_families):
            raise ValueError("candidate_families must be sorted")
        if len(set(self.candidate_families)) != len(self.candidate_families):
            raise ValueError("candidate_families must be unique")
        if self.template_version == CONTRACT_ALIGNED_TEMPLATE_VERSION:
            # v2 text is LOCKED to the mirrored deployed contract — arbitrary
            # prompt-text injection through a v2 template is unrepresentable.
            if self.instructions != _CONTRACT_INSTRUCTIONS:
                raise ValueError(
                    "a v2 template must carry exactly the mirrored deployed "
                    "instruction text")
            if self.name != CONTRACT_ALIGNED_TEMPLATE_NAME:
                raise ValueError(
                    "a v2 template must carry the contract-aligned name")
            if self.candidate_families != TRAINING_CANDIDATE_FAMILIES:
                raise ValueError(
                    "a v2 template must carry exactly the approved candidate "
                    "class space")
        expected = derive_input_template_id(
            schema_version=self.schema_version, template_version=self.template_version,
            name=self.name, instructions=self.instructions,
            candidate_families=self.candidate_families, task_id=self.task_id,
            feature_policy_id=self.feature_policy_id,
        )
        if self.input_template_id != expected:
            raise ValueError("input_template_id does not match the template content")
        return self

    def render(self, features: DatasetFeatures) -> str:
        """Deterministically render the model input from features ONLY.

        v1 renders the byte-frozen Gate 10A serialization; v2 renders the
        deployed Gate 8 prompt serialization (same observation block, the
        mirrored instruction sentence, and the mirrored response-schema
        sentence in place of the v1 ``Output:`` line).
        """
        candidates = ", ".join(self.candidate_families)
        onset = "present" if features.onset_evidence is not None else "absent"
        tail = (f"Output: {_TARGET_SCHEMA_DESCRIPTION}"
                if self.template_version == 1 else _CONTRACT_RESPONSE_SCHEMA)
        return (
            f"{self.instructions}\n\n"
            f"Candidate fault families: {candidates}\n\n"
            "Observation metadata:\n"
            f"- backend: {features.backend}\n"
            f"- topology_hash: {features.topology_hash}\n"
            f"- baseline_evidence: present\n"
            f"- onset_evidence: {onset}\n\n"
            f"{tail}"
        )


class TrainingTargetTemplate(StrictModel):
    """The frozen, content-addressed supervised-target contract.

    ``render`` emits strict canonical JSON (sorted keys, no whitespace) so two
    equivalent labels serialize byte-identically. The target carries ONLY the
    authoritative fault family and the prediction type — never correctness,
    confidence, reasoning, outcome category, ranking, recovery data, identity,
    paths, or digests.
    """

    schema_version: Literal[1] = 1
    target_version: Literal[1] = 1
    task_id: str = Field(min_length=1)
    output_schema: str = Field(min_length=1)
    target_template_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingTargetTemplate:
        expected = derive_target_template_id(
            schema_version=self.schema_version, target_version=self.target_version,
            task_id=self.task_id, output_schema=self.output_schema,
        )
        if self.target_template_id != expected:
            raise ValueError("target_template_id does not match the template content")
        return self

    def render(self, fault_family: str) -> str:
        """Canonical JSON target from the authoritative accepted label."""
        return canonical_json_str(
            {"prediction_type": "diagnosis", "fault_family": fault_family}
        )


class TrainingDataPolicy(StrictModel):
    """The frozen, content-addressed training-eligibility contract (Gate 10A).

    Every eligibility-defining field is a ``Literal`` locking the Gate 10A
    contract: only ``train``-partition, accepted-fault, accepted-diagnosis
    examples may enter supervised training; validation, test, and abstention are
    structurally excluded (a policy permitting them cannot be constructed).
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    allowed_partition: Literal["train"] = "train"
    allowed_example_kind: Literal["accepted_fault"] = "accepted_fault"
    allowed_label_kind: Literal["accepted_fault"] = "accepted_fault"
    include_abstention: Literal[False] = False
    task_id: str = Field(min_length=1)
    input_template_id: str = Field(min_length=1)
    target_template_id: str = Field(min_length=1)
    training_data_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingDataPolicy:
        expected = derive_training_data_policy_id(
            schema_version=self.schema_version, policy_version=self.policy_version,
            allowed_partition=self.allowed_partition,
            allowed_example_kind=self.allowed_example_kind,
            allowed_label_kind=self.allowed_label_kind, task_id=self.task_id,
            input_template_id=self.input_template_id,
            target_template_id=self.target_template_id,
            include_abstention=self.include_abstention,
        )
        if self.training_data_policy_id != expected:
            raise ValueError("training_data_policy_id does not match the policy")
        return self


def diagnosis_input_template(
    *,
    task_id: str,
    feature_policy_id: str,
    name: str = "supervised_fault_family_diagnosis",
    candidate_families: tuple[str, ...] = TRAINING_CANDIDATE_FAMILIES,
) -> TrainingInputTemplate:
    families = tuple(sorted(candidate_families))
    template_id = derive_input_template_id(
        schema_version=1, template_version=INPUT_TEMPLATE_VERSION, name=name,
        instructions=_INPUT_INSTRUCTIONS, candidate_families=families,
        task_id=task_id, feature_policy_id=feature_policy_id,
    )
    return TrainingInputTemplate(
        name=name, instructions=_INPUT_INSTRUCTIONS, candidate_families=families,
        task_id=task_id, feature_policy_id=feature_policy_id,
        input_template_id=template_id,
    )


def diagnosis_target_template(*, task_id: str) -> TrainingTargetTemplate:
    template_id = derive_target_template_id(
        schema_version=1, target_version=TARGET_TEMPLATE_VERSION, task_id=task_id,
        output_schema=_TARGET_SCHEMA_DESCRIPTION,
    )
    return TrainingTargetTemplate(
        task_id=task_id, output_schema=_TARGET_SCHEMA_DESCRIPTION,
        target_template_id=template_id,
    )


def diagnosis_training_policy(
    *,
    task_id: str,
    input_template: TrainingInputTemplate,
    target_template: TrainingTargetTemplate,
) -> TrainingDataPolicy:
    policy_id = derive_training_data_policy_id(
        schema_version=1, policy_version=TRAINING_POLICY_VERSION,
        allowed_partition="train", allowed_example_kind="accepted_fault",
        allowed_label_kind="accepted_fault", task_id=task_id,
        input_template_id=input_template.input_template_id,
        target_template_id=target_template.target_template_id,
        include_abstention=False,
    )
    return TrainingDataPolicy(
        task_id=task_id, input_template_id=input_template.input_template_id,
        target_template_id=target_template.target_template_id,
        training_data_policy_id=policy_id,
    )


def contract_aligned_input_template(
    *,
    task_id: str,
    feature_policy_id: str,
) -> TrainingInputTemplate:
    """The Gate 16A v2 training-input template (contract-aligned, locked).

    Renders byte-identically to the deployed Gate 8 prompt for the same
    features. Exposes NO text parameters: the instruction sentence, the
    response-schema sentence, the name, and the candidate class space are all
    fixed to the mirrored deployed contract and Literal-checked by the model
    validator — arbitrary prompt-text injection is unrepresentable.
    """
    template_id = derive_input_template_id(
        schema_version=1,
        template_version=CONTRACT_ALIGNED_TEMPLATE_VERSION,
        name=CONTRACT_ALIGNED_TEMPLATE_NAME,
        instructions=_CONTRACT_INSTRUCTIONS,
        candidate_families=TRAINING_CANDIDATE_FAMILIES,
        task_id=task_id, feature_policy_id=feature_policy_id,
    )
    return TrainingInputTemplate(
        template_version=CONTRACT_ALIGNED_TEMPLATE_VERSION,
        name=CONTRACT_ALIGNED_TEMPLATE_NAME,
        instructions=_CONTRACT_INSTRUCTIONS,
        candidate_families=TRAINING_CANDIDATE_FAMILIES,
        task_id=task_id, feature_policy_id=feature_policy_id,
        input_template_id=template_id,
    )


def contract_aligned_training_policy(
    *,
    task_id: str,
    input_template: TrainingInputTemplate,
    target_template: TrainingTargetTemplate,
) -> TrainingDataPolicy:
    """The Gate 16A eligibility policy: v2 input + the UNCHANGED v1 target.

    Eligibility itself is byte-identical to Gate 10A (train-partition,
    accepted-fault, accepted-diagnosis only; abstention structurally
    excluded — those fields are Literal-locked on ``TrainingDataPolicy``).
    Only the bound input-template identity differs, so the policy id changes
    while the target-template id must remain the frozen v1 identity.
    """
    if input_template.template_version != CONTRACT_ALIGNED_TEMPLATE_VERSION:
        raise ValueError(
            "the contract-aligned policy requires the v2 input template")
    if target_template.target_version != TARGET_TEMPLATE_VERSION:
        raise ValueError(
            "the contract-aligned policy requires the UNCHANGED v1 target "
            "template")
    return diagnosis_training_policy(
        task_id=task_id, input_template=input_template,
        target_template=target_template,
    )


# ---------------------------------------------------------------------------
# Gate 18A — v2 training input render (byte-identical to the deployed v2 prompt)
# ---------------------------------------------------------------------------

def render_training_input_v2(features: DatasetFeaturesV2) -> str:
    """Render the v2 training input from v2 observable features ONLY.

    Uses the mirrored Gate 16A instruction/response-schema constants (byte-equal
    to the deployed Gate 8 prompt text) and the SHARED v2 observation render, so
    the training input is byte-identical to the deployed inference prompt for the
    same v2 features (Gate 17A boundary preservation). Training never imports the
    evaluation package; the shared render lives in ``datasets``.
    """
    candidates = ", ".join(sorted(TRAINING_CANDIDATE_FAMILIES))
    block = render_evidence_observation_block(features)
    return (
        f"{_CONTRACT_INSTRUCTIONS}\n\n"
        f"Candidate fault families: {candidates}\n\n"
        f"{block}\n\n"
        f"{_CONTRACT_RESPONSE_SCHEMA}"
    )
