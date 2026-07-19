"""Explicit, versioned prompt templates for model-backed predictors (Gate 8).

Prompts are NEVER constructed ad hoc. A ``PromptTemplate`` is frozen, versioned,
and content-addressed (``prompt_template_id``); its rendering is a pure,
deterministic function of the model-visible ``DatasetFeatures`` only. The rendered
prompt exposes exactly the allowlisted features (topology hash, backend, and
whether onset evidence is present) plus the fixed candidate-family class list and
the required JSON response schema — never a label, identity, split, or trace.

The candidate-family list is the classification CLASS SPACE (public), not the
answer for any specific example; presenting it is not leakage.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.evidence_features import (
    DatasetFeaturesV2,
    render_evidence_observation_block,
)
from verifiednet.datasets.features import DatasetFeatures
from verifiednet.schemas.base import StrictModel

PROMPT_TEMPLATE_VERSION = 1

#: The fault-family class space presented to the model (the four Gate 5 families).
DEFAULT_CANDIDATE_FAMILIES: tuple[str, ...] = (
    "bgp_neighbor_removal",
    "bgp_prefix_withdrawal",
    "bgp_remote_as_mismatch",
    "iface_admin_shutdown",
)

_RESPONSE_SCHEMA = (
    'Respond with ONE JSON object and nothing else: '
    '{"prediction_type": "diagnosis" | "abstention", '
    '"fault_family": <one of the candidate families, required iff diagnosis>, '
    '"confidence": "low" | "medium" | "high"}. '
    'If there is no fault to diagnose, use prediction_type "abstention".'
)

_INSTRUCTIONS = (
    "You are a deterministic network fault-diagnosis classifier. You are given "
    "only observation metadata about one verified network run. Decide whether a "
    "fault occurred and, if so, which fault family it belongs to. You must choose "
    "a fault family strictly from the candidate list, or abstain."
)


def derive_prompt_template_id(
    *,
    schema_version: int,
    template_version: int,
    name: str,
    instructions: str,
    candidate_families: tuple[str, ...],
    response_schema: str,
) -> str:
    payload = {
        "schema_version": schema_version,
        "template_version": template_version,
        "name": name,
        "instructions": instructions,
        "candidate_families": sorted(candidate_families),
        "response_schema": response_schema,
    }
    return "prompt-" + sha256_canonical(payload)[:16]


class PromptTemplate(StrictModel):
    """A frozen, versioned, content-addressed prompt template."""

    schema_version: Literal[1] = 1
    template_version: Literal[1] = 1
    name: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    candidate_families: tuple[str, ...] = Field(min_length=1)
    response_schema: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> PromptTemplate:
        if sorted(self.candidate_families) != list(self.candidate_families):
            raise ValueError("candidate_families must be sorted")
        if len(set(self.candidate_families)) != len(self.candidate_families):
            raise ValueError("candidate_families must be unique")
        expected = derive_prompt_template_id(
            schema_version=self.schema_version, template_version=self.template_version,
            name=self.name, instructions=self.instructions,
            candidate_families=self.candidate_families, response_schema=self.response_schema,
        )
        if self.prompt_template_id != expected:
            raise ValueError("prompt_template_id does not match the template content")
        return self

    def render(self, features: DatasetFeatures) -> str:
        """Deterministically render the prompt from model-visible features ONLY."""
        candidates = ", ".join(self.candidate_families)
        onset = "present" if features.onset_evidence is not None else "absent"
        return (
            f"{self.instructions}\n\n"
            f"Candidate fault families: {candidates}\n\n"
            "Observation metadata:\n"
            f"- backend: {features.backend}\n"
            f"- topology_hash: {features.topology_hash}\n"
            f"- baseline_evidence: present\n"
            f"- onset_evidence: {onset}\n\n"
            f"{self.response_schema}"
        )


def diagnosis_prompt_template(
    *,
    name: str = "single_fault_family_diagnosis",
    candidate_families: tuple[str, ...] = DEFAULT_CANDIDATE_FAMILIES,
) -> PromptTemplate:
    """Build the canonical Gate 8 diagnosis prompt template with its derived id."""
    families = tuple(sorted(candidate_families))
    template_id = derive_prompt_template_id(
        schema_version=1, template_version=PROMPT_TEMPLATE_VERSION, name=name,
        instructions=_INSTRUCTIONS, candidate_families=families,
        response_schema=_RESPONSE_SCHEMA,
    )
    return PromptTemplate(
        name=name, instructions=_INSTRUCTIONS, candidate_families=families,
        response_schema=_RESPONSE_SCHEMA, prompt_template_id=template_id,
    )


# ---------------------------------------------------------------------------
# Gate 18A — v2 prompt: frozen instructions/candidates/schema, v2 observation
# block. Only the OBSERVATION METADATA block changes; the shared render in
# ``datasets.evidence_features`` is the single source of truth so the deployed
# inference prompt and the training input are byte-identical (Gate 17A boundary).
# ---------------------------------------------------------------------------

PROMPT_OBSERVATION_VERSION_V2 = 2


def derive_prompt_v2_template_id(
    *,
    feature_policy_v2_id: str,
    name: str = "single_fault_family_diagnosis",
    instructions: str = _INSTRUCTIONS,
    candidate_families: tuple[str, ...] = DEFAULT_CANDIDATE_FAMILIES,
    response_schema: str = _RESPONSE_SCHEMA,
) -> str:
    """Content-addressed id for the v2 prompt (frozen text + v2 observation)."""
    payload = {
        "schema_version": 1,
        "template_version": PROMPT_TEMPLATE_VERSION,
        "observation_version": PROMPT_OBSERVATION_VERSION_V2,
        "name": name,
        "instructions": instructions,
        "candidate_families": sorted(candidate_families),
        "response_schema": response_schema,
        "feature_policy_v2_id": feature_policy_v2_id,
    }
    return "prompt-" + sha256_canonical(payload)[:16]


def render_diagnosis_prompt_v2(
    features: DatasetFeaturesV2,
    *,
    instructions: str = _INSTRUCTIONS,
    candidate_families: tuple[str, ...] = DEFAULT_CANDIDATE_FAMILIES,
    response_schema: str = _RESPONSE_SCHEMA,
) -> str:
    """Render the deployed v2 diagnosis prompt from v2 observable features ONLY.

    Frozen Gate 8 instructions, candidate list, and response schema; only the
    observation block (the shared, single-source v2 render) differs from v1.
    """
    candidates = ", ".join(sorted(candidate_families))
    block = render_evidence_observation_block(features)
    return (
        f"{instructions}\n\n"
        f"Candidate fault families: {candidates}\n\n"
        f"{block}\n\n"
        f"{response_schema}"
    )
