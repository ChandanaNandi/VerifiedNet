"""Real trainer-backend contract + runtime environment evidence (Gate 10E).

Gate 10E introduces the FIRST boundary for a real ML training backend — as a
contract and a preflight, never as execution. The gate's central distinction:

* IMMUTABLE TRAINING INTENT (Gate 10B): corpus binding, model/tokenizer specs,
  hyperparameters, precision, seeds, budget, trainer implementation identity.
  Content-addressed, portable, and never mutated by anything in this module.
* RUNTIME ENVIRONMENT EVIDENCE (this gate): what one specific machine can do
  at inspection time — OS, Python, package versions, device capability,
  precision modes, deterministic-mode support, cache availability. Evidence
  is EXPECTED to vary across machines; it lives in a separate snapshot and a
  separate authorization artifact, and it never changes a plan identity.

Selected initial real-backend scope (deliberately the smallest viable one):

    Hugging Face Transformers + PyTorch
    single process, single device, FULL fine-tuning
    no PEFT/LoRA, no distributed, no DeepSpeed, no FSDP, no multi-node,
    no remote training

LoRA/QLoRA are NOT claimed: the Gate 10B ``TrainingSpec`` models no adapter
hyperparameters, so an adapter mode would be an unmodeled promise. A future
gate that wants LoRA must model it explicitly and issue a new backend spec.

NOTHING in this module imports an ML framework. Package presence is observed
through ``importlib.metadata`` (which never imports the package), and the v1
``SystemEnvironmentProbe`` is deliberately CPU-only: honest CUDA probing needs
torch itself and therefore belongs to the gate that actually loads torch
(Gate 10F). The AST import boundary for ``verifiednet.training`` stays fully
intact.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import sys
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel
from verifiednet.training.spec import TrainingSpec
from verifiednet.training.trainer import (
    DeterminismClaim,
    TrainerCapabilities,
    TrainingCorpusDescriptor,
    TrainingPlan,
    build_capabilities,
    build_training_request,
    compute_plan_counts,
    derive_training_plan_id,
)

HF_FULL_FINETUNE_BACKEND_ID = "hf-transformers-full-finetune-v1"


class BackendContractError(VerifiedNetError):
    """A backend contract, snapshot, or capability operation failed."""


class DeterminismCategory(StrEnum):
    """Honest determinism levels a backend/environment pair may claim."""

    DETERMINISTIC_SIMULATED = "deterministic_simulated"
    DETERMINISTIC_SUPPORTED = "deterministic_supported"
    BEST_EFFORT_DETERMINISTIC = "best_effort_deterministic"
    NONDETERMINISTIC = "nondeterministic"
    UNSUPPORTED = "unsupported"


# ---------------------------------------------------------------------------
# Backend specification (the implementation contract, not the machine)
# ---------------------------------------------------------------------------


class RealTrainerBackendSpec(StrictModel):
    """Frozen, versioned contract of what a real backend implementation
    supports. The id represents the CONTRACT — changing any supported behavior
    changes the id; nothing here describes the current machine."""

    schema_version: Literal[1] = 1
    backend_contract_version: Literal[1] = 1
    backend_name: str = Field(min_length=1)
    backend_implementation_version: str = Field(min_length=1)
    framework_family: Literal["hf-transformers-pytorch"] = (
        "hf-transformers-pytorch")
    training_mode: Literal["full_finetune_single_device"] = (
        "full_finetune_single_device")
    required_packages: tuple[str, ...] = Field(min_length=1)
    required_package_constraints: tuple[str, ...] = Field(min_length=1)
    supported_operating_systems: tuple[str, ...] = Field(min_length=1)
    supported_device_types: tuple[str, ...] = Field(min_length=1)
    supported_precisions: tuple[str, ...] = Field(min_length=1)
    supported_optimizers: tuple[str, ...] = Field(min_length=1)
    supported_schedulers: tuple[str, ...] = Field(min_length=1)
    supported_checkpoint_declarations: tuple[str, ...] = Field(min_length=1)
    backend_spec_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealTrainerBackendSpec:
        for name in ("required_packages", "supported_operating_systems",
                     "supported_device_types", "supported_precisions",
                     "supported_optimizers", "supported_schedulers",
                     "supported_checkpoint_declarations"):
            values = list(getattr(self, name))
            if values != sorted(values) or len(values) != len(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        if len(self.required_package_constraints) != len(self.required_packages):
            raise ValueError(
                "one version constraint is required per required package")
        for constraint in self.required_package_constraints:
            try:
                SpecifierSet(constraint)
            except InvalidSpecifier as exc:
                raise ValueError(f"invalid version constraint: {constraint!r}") from exc
        if self.backend_spec_id != derive_backend_spec_id(self):
            raise ValueError("backend_spec_id does not match the contract")
        return self


def derive_backend_spec_id(spec: RealTrainerBackendSpec) -> str:
    payload = spec.model_dump(mode="json")
    payload.pop("backend_spec_id", None)
    return "trainbk-" + sha256_canonical(payload)[:16]


def build_hf_full_finetune_backend_spec() -> RealTrainerBackendSpec:
    """The single real-backend contract defined in Gate 10E."""
    fields: dict[str, object] = {
        "backend_name": HF_FULL_FINETUNE_BACKEND_ID,
        "backend_implementation_version": "0.1.0",
        "required_packages": ("torch", "transformers"),
        "required_package_constraints": (">=2.2,<3", ">=4.40,<5"),
        "supported_operating_systems": ("linux", "macos"),
        "supported_device_types": ("cpu", "cuda"),
        "supported_precisions": ("bfloat16", "float32"),
        "supported_optimizers": ("adamw",),
        "supported_schedulers": ("constant", "linear_warmup"),
        "supported_checkpoint_declarations": ("none",),
    }
    probe = RealTrainerBackendSpec.model_construct(**fields)  # type: ignore[arg-type]
    return RealTrainerBackendSpec(
        **fields,  # type: ignore[arg-type]
        backend_spec_id=derive_backend_spec_id(probe))


def build_hf_backend_capabilities() -> TrainerCapabilities:
    """Gate 10B capabilities for the real backend's PLANNING path.

    This lets a plan bind ``trainer_implementation_id`` to the real backend
    without touching fake-plan verification: a fake-trainer plan and a
    real-backend plan carry different implementation ids, and preflight
    refuses the fake one structurally.
    """
    return build_capabilities(
        trainer_implementation_id=HF_FULL_FINETUNE_BACKEND_ID,
        supported_model_families=("huggingface",),
        supported_precisions=("bfloat16", "float32"),
        supported_optimizers=("adamw",),
        supported_schedulers=("constant", "linear_warmup"),
        supported_checkpoint_policies=("none",),
        supports_deterministic="conditional",
        supports_cpu=True, supports_gpu=True,
        supports_adapter_training=False, supports_full_finetuning=True,
        supports_distributed=False,
    )


def plan_for_real_backend(
    *, spec: TrainingSpec, corpus: TrainingCorpusDescriptor,
) -> TrainingPlan:
    """Deterministic Gate 10B planning against the real backend's capabilities.

    Identical arithmetic and identity rules as the fake trainer's planning —
    the ONLY differences are the capability contract and an honest
    ``best_effort_deterministic`` claim (a real backend on real kernels may
    not reproduce bit-identical weights; overclaiming is forbidden).
    """
    capabilities = build_hf_backend_capabilities()
    request = build_training_request(
        spec=spec, corpus=corpus, capabilities=capabilities)
    batches, epochs, steps = compute_plan_counts(request)
    output_namespace = f"training-runs/{request.request_id}"
    probe = TrainingPlan.model_construct(
        request=request, expected_example_count=corpus.example_count,
        expected_epochs=epochs, batches_per_epoch=batches,
        optimizer_steps=steps,
        effective_batch_size=spec.batch.effective_batch_size,
        data_order="canonical", input_source="training_corpus_pairs",
        output_namespace=output_namespace,
        determinism_claim=DeterminismClaim.BEST_EFFORT_DETERMINISTIC,
        warnings=())
    return TrainingPlan(
        request=request, expected_example_count=corpus.example_count,
        expected_epochs=epochs, batches_per_epoch=batches,
        optimizer_steps=steps,
        effective_batch_size=spec.batch.effective_batch_size,
        output_namespace=output_namespace,
        determinism_claim=DeterminismClaim.BEST_EFFORT_DETERMINISTIC,
        training_plan_id=derive_training_plan_id(probe))


# ---------------------------------------------------------------------------
# Runtime evidence: package records, device capability, environment snapshot
# ---------------------------------------------------------------------------


class RuntimePackageRecord(StrictModel):
    """One required library, checked with a standards-compliant parser."""

    package_name: str = Field(min_length=1)
    required_constraint: str = Field(min_length=1)
    detected_version: str | None = None
    importable: bool
    status: Literal["compatible", "incompatible", "missing", "unparseable"]
    package_record_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RuntimePackageRecord:
        if self.package_record_id != derive_package_record_id(self):
            raise ValueError("package_record_id does not match the record")
        return self


def derive_package_record_id(record: RuntimePackageRecord) -> str:
    payload = record.model_dump(mode="json")
    payload.pop("package_record_id", None)
    return "pkgrec-" + sha256_canonical(payload)[:16]


def check_package(
    *, package_name: str, required_constraint: str,
    detected_version: str | None, importable: bool,
) -> RuntimePackageRecord:
    """PEP 440 version compatibility — never lexicographic comparison."""
    status: Literal["compatible", "incompatible", "missing", "unparseable"]
    if detected_version is None:
        status = "missing"
    else:
        try:
            version = Version(detected_version)
            status = ("compatible"
                      if version in SpecifierSet(required_constraint)
                      else "incompatible")
        except (InvalidVersion, InvalidSpecifier):
            status = "unparseable"
    probe = RuntimePackageRecord.model_construct(
        package_name=package_name, required_constraint=required_constraint,
        detected_version=detected_version, importable=importable,
        status=status)
    return RuntimePackageRecord(
        package_name=package_name, required_constraint=required_constraint,
        detected_version=detected_version, importable=importable,
        status=status, package_record_id=derive_package_record_id(probe))


class TrainingDeviceCapability(StrictModel):
    """One concrete device the backend could run on (runtime evidence)."""

    schema_version: Literal[1] = 1
    device_type: Literal["cpu", "cuda", "metal"]
    declared_device_count: int = Field(ge=0)
    selected_device_index: int = Field(ge=0)
    supported_precisions: tuple[str, ...] = Field(min_length=1)
    total_memory_bytes: int = Field(ge=0)
    deterministic_operations_supported: bool
    device_capability_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingDeviceCapability:
        values = list(self.supported_precisions)
        if values != sorted(values) or len(values) != len(set(values)):
            raise ValueError("supported_precisions must be sorted and unique")
        if (self.declared_device_count > 0
                and self.selected_device_index >= self.declared_device_count):
            raise ValueError("selected_device_index out of range")
        if self.device_capability_id != derive_device_capability_id(self):
            raise ValueError("device_capability_id does not match the device")
        return self


def derive_device_capability_id(device: TrainingDeviceCapability) -> str:
    payload = device.model_dump(mode="json")
    payload.pop("device_capability_id", None)
    return "devcap-" + sha256_canonical(payload)[:16]


def build_device_capability(
    *, device_type: Literal["cpu", "cuda", "metal"], declared_device_count: int,
    selected_device_index: int, supported_precisions: tuple[str, ...],
    total_memory_bytes: int, deterministic_operations_supported: bool,
) -> TrainingDeviceCapability:
    probe = TrainingDeviceCapability.model_construct(
        device_type=device_type, declared_device_count=declared_device_count,
        selected_device_index=selected_device_index,
        supported_precisions=tuple(sorted(supported_precisions)),
        total_memory_bytes=total_memory_bytes,
        deterministic_operations_supported=deterministic_operations_supported)
    return TrainingDeviceCapability(
        device_type=device_type, declared_device_count=declared_device_count,
        selected_device_index=selected_device_index,
        supported_precisions=tuple(sorted(supported_precisions)),
        total_memory_bytes=total_memory_bytes,
        deterministic_operations_supported=deterministic_operations_supported,
        device_capability_id=derive_device_capability_id(probe))


class TrainingEnvironmentSnapshot(StrictModel):
    """Runtime evidence for ONE machine at inspection time — not intent.

    Deliberately contains NO username, hostname, home directory, absolute
    path, environment variables, process id, or wall-clock time (schema-level:
    ``extra="forbid"`` means such fields are unrepresentable). Identical
    probes yield identical snapshots; different machines are EXPECTED to yield
    different snapshot ids — that is the point of runtime evidence.
    """

    schema_version: Literal[1] = 1
    python_implementation: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    os_family: Literal["linux", "macos", "windows", "other"]
    machine_architecture: str = Field(min_length=1)
    package_records: tuple[RuntimePackageRecord, ...] = Field(min_length=1)
    device: TrainingDeviceCapability
    deterministic_algorithms_supported: bool
    backend_available: bool
    model_cache_available: bool
    tokenizer_cache_available: bool
    environment_snapshot_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingEnvironmentSnapshot:
        names = [r.package_name for r in self.package_records]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("package_records must be name-sorted and unique")
        if self.environment_snapshot_id != derive_environment_snapshot_id(self):
            raise ValueError(
                "environment_snapshot_id does not match the snapshot")
        return self


def derive_environment_snapshot_id(snapshot: TrainingEnvironmentSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    payload.pop("environment_snapshot_id", None)
    return "envsnap-" + sha256_canonical(payload)[:16]


# ---------------------------------------------------------------------------
# Environment probes
# ---------------------------------------------------------------------------


@runtime_checkable
class EnvironmentProbe(Protocol):
    """Narrow, side-effect-free observation source for snapshot construction."""

    def python_implementation(self) -> str: ...
    def python_version(self) -> str: ...
    def os_family(self) -> str: ...
    def machine_architecture(self) -> str: ...
    def detect_package(self, package_name: str) -> tuple[str | None, bool]: ...
    def device_capability(self) -> TrainingDeviceCapability: ...
    def deterministic_algorithms_supported(self) -> bool: ...
    def model_cache_available(self) -> bool: ...
    def tokenizer_cache_available(self) -> bool: ...


def snapshot_from_probe(
    probe: EnvironmentProbe, backend_spec: RealTrainerBackendSpec,
) -> TrainingEnvironmentSnapshot:
    """Build the snapshot from a probe. Observation only — no plan mutation."""
    records = tuple(sorted((
        check_package(
            package_name=name, required_constraint=constraint,
            detected_version=probe.detect_package(name)[0],
            importable=probe.detect_package(name)[1])
        for name, constraint in zip(backend_spec.required_packages,
                                    backend_spec.required_package_constraints,
                                    strict=True)),
        key=lambda r: r.package_name))
    os_family = probe.os_family()
    if os_family not in ("linux", "macos", "windows"):
        os_family = "other"
    device = probe.device_capability()
    fields: dict[str, object] = {
        "python_implementation": probe.python_implementation(),
        "python_version": probe.python_version(),
        "os_family": os_family,
        "machine_architecture": probe.machine_architecture(),
        "package_records": records,
        "device": device,
        "deterministic_algorithms_supported":
            probe.deterministic_algorithms_supported(),
        "backend_available": all(r.status == "compatible" for r in records),
        "model_cache_available": probe.model_cache_available(),
        "tokenizer_cache_available": probe.tokenizer_cache_available(),
    }
    probe_model = TrainingEnvironmentSnapshot.model_construct(**fields)  # type: ignore[arg-type]
    return TrainingEnvironmentSnapshot(
        **fields,  # type: ignore[arg-type]
        environment_snapshot_id=derive_environment_snapshot_id(probe_model))


class SystemEnvironmentProbe:
    """The v1 REAL probe: platform + importlib.metadata only. CPU-only.

    Package versions come from ``importlib.metadata`` (which never imports
    the package) and importability from ``importlib.util.find_spec``. Honest
    CUDA/Metal probing requires torch itself, so the v1 system probe reports
    the CPU device only — GPU observation arrives with the gate that actually
    loads torch (Gate 10F). No environment variables are read, ever.
    """

    def python_implementation(self) -> str:
        return platform.python_implementation()

    def python_version(self) -> str:
        return platform.python_version()

    def os_family(self) -> str:
        system = sys.platform
        if system.startswith("linux"):
            return "linux"
        if system == "darwin":
            return "macos"
        if system in ("win32", "cygwin"):
            return "windows"
        return "other"

    def machine_architecture(self) -> str:
        return platform.machine() or "unknown"

    def detect_package(self, package_name: str) -> tuple[str | None, bool]:
        try:
            version = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            return None, False
        importable = importlib.util.find_spec(package_name) is not None
        return version, importable

    def device_capability(self) -> TrainingDeviceCapability:
        # CPU-only in v1 (documented above): a conservative, honest floor.
        return build_device_capability(
            device_type="cpu", declared_device_count=1,
            selected_device_index=0, supported_precisions=("float32",),
            total_memory_bytes=0,  # not reliably observable without psutil
            deterministic_operations_supported=True)

    def deterministic_algorithms_supported(self) -> bool:
        return True  # CPU arithmetic; the torch-backed probe refines this later

    def model_cache_available(self) -> bool:
        return False  # no cache inspection in v1 — resolution must prove it

    def tokenizer_cache_available(self) -> bool:
        return False


class FakeEnvironmentProbe:
    """Deterministic, fully configurable probe for the offline suite."""

    def __init__(
        self,
        *,
        packages: dict[str, tuple[str | None, bool]] | None = None,
        device: TrainingDeviceCapability | None = None,
        os_family: str = "linux",
        machine_architecture: str = "x86_64",
        python_version: str = "3.12.0",
        deterministic_supported: bool = True,
        model_cache: bool = True,
        tokenizer_cache: bool = True,
    ) -> None:
        self._packages = packages if packages is not None else {
            "torch": ("2.4.0", True), "transformers": ("4.44.0", True)}
        self._device = device if device is not None else build_device_capability(
            device_type="cpu", declared_device_count=1,
            selected_device_index=0,
            supported_precisions=("bfloat16", "float32"),
            total_memory_bytes=16 * 1024**3,
            deterministic_operations_supported=True)
        self._os_family = os_family
        self._arch = machine_architecture
        self._python_version = python_version
        self._deterministic = deterministic_supported
        self._model_cache = model_cache
        self._tokenizer_cache = tokenizer_cache

    def python_implementation(self) -> str:
        return "CPython"

    def python_version(self) -> str:
        return self._python_version

    def os_family(self) -> str:
        return self._os_family

    def machine_architecture(self) -> str:
        return self._arch

    def detect_package(self, package_name: str) -> tuple[str | None, bool]:
        return self._packages.get(package_name, (None, False))

    def device_capability(self) -> TrainingDeviceCapability:
        return self._device

    def deterministic_algorithms_supported(self) -> bool:
        return self._deterministic

    def model_cache_available(self) -> bool:
        return self._model_cache

    def tokenizer_cache_available(self) -> bool:
        return self._tokenizer_cache
