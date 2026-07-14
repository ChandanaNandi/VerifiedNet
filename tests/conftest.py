"""Shared deterministic test fixtures. No wall clocks, no randomness, no services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.schemas import (
    ScenarioDefinition,
    ScenarioTimeouts,
    TopologySpec,
)

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class FakeClock:
    """Deterministic, manually-advanced clock."""

    def __init__(self, start: datetime = EPOCH) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self._now.timestamp()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def run_ctx(fake_clock: FakeClock) -> RunContext:
    return RunContext("run-test-0001", clock=fake_clock)


def make_two_router_topology() -> TopologySpec:
    # Delegates to the canonical factory (single source of the approved values).
    return two_router_frr_topology()


def make_scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        family="bgp",
        template_id="bgp_remote_as_mismatch",
        version=1,
        parameters={"wrong_asn": 65999, "target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0,
            onset_s=30.0,
            recovery_s=60.0,
            command_s=10.0,
            poll_interval_s=0.5,
        ),
    )


@pytest.fixture
def two_router_topology() -> TopologySpec:
    return make_two_router_topology()


@pytest.fixture
def scenario() -> ScenarioDefinition:
    return make_scenario()


ClockFn = Callable[[], datetime]


# --------------------------------------------------------------------------
# Synthetic run inputs for artifact tests (Gate 4 Step 5). Deterministic,
# offline: fixed clock, no lab, no Docker. Reused across artifact test tiers.
# --------------------------------------------------------------------------

import json as _json  # noqa: E402
from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True)
class RunInputs:
    run_manifest: object
    environment_manifest: object
    incident: object
    transcript_entries: tuple
    ledger_records: tuple


def _evidence_bundle(rc: RunContext, phase: object, target: str, normalized: dict) -> object:
    from verifiednet.common.hashing import sha256_bytes
    from verifiednet.schemas import EvidenceBundle, EvidenceRecord, EvidenceSource

    payload = _json.dumps(normalized, sort_keys=True)
    seq = rc.next_seq()
    record = EvidenceRecord(
        evidence_id=rc.content_id("ev", {"phase": str(phase), "target": target, "seq": seq}),
        phase=phase,
        source=EvidenceSource(collector="fake.collector", target=target, trusted=True),
        raw_sha256=sha256_bytes(payload.encode("utf-8")),
        raw_payload=payload,
        normalized=normalized,
        captured_at=EPOCH,
        run_seq=seq,
    )
    return EvidenceBundle(
        bundle_id=rc.content_id("bundle", {"phase": str(phase), "t": target}),
        phase=phase,
        records=(record,),
    ).seal()


def _env_manifest() -> object:
    from verifiednet.schemas import EnvironmentManifest

    return EnvironmentManifest(
        os_name="Darwin", kernel="25.5.0", arch="arm64", python_version="3.12.12",
        container_runtime="docker", container_runtime_version="29.1.3",
        image_reference="frrouting/frr:v8.4.1@sha256:" + "c" * 64,
        image_manifest_digest="sha256:" + "c" * 64, frr_version="8.4.1_git", captured_at=EPOCH,
    )


def build_accepted_inputs(run_id: str = "run-test-acc1") -> RunInputs:
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.faults.ledger import Ledger, LifecyclePhase
    from verifiednet.incidents.builder import build_accepted_record
    from verifiednet.incidents.oracle import build_ground_truth
    from verifiednet.runtime.invocation import CommandInvocation
    from verifiednet.runtime.transcript import TranscriptEntry
    from verifiednet.schemas import (
        Phase,
        ProvenanceInfo,
        RestorationMetadata,
        RunManifest,
        Verdict,
        VerificationResult,
    )
    from verifiednet.schemas.fault import FaultInjection

    rc = RunContext(run_id, clock=lambda: EPOCH)
    topo = make_two_router_topology()
    scen = make_scenario()
    up = {"bgp.peer.172.30.0.2.state": "Established"}
    down = {"bgp.peer.172.30.0.2.state": "Idle"}
    baseline = _evidence_bundle(rc, Phase.BASELINE, "router_a", up)
    onset = _evidence_bundle(rc, Phase.ONSET, "router_a", down)
    recovery = _evidence_bundle(rc, Phase.RECOVERY, "router_a", up)
    fault = FaultInjection(
        scenario_id=scen.scenario_id, template_id=scen.template_id, target_node="router_a",
        target_session="a-b", method="vtysh-remote-as", parameter_name="remote_as",
        before_value="65002", after_value="65999", transcript_refs=(2,),
        injected_at_seq=rc.next_seq(), injected_at=EPOCH,
    )
    vr = VerificationResult(
        check_id="bgp_not_established:router_a:x:onset", verdict=Verdict.PASS, phase="onset",
        evidence_ids=("ev-transient",), observed=("Idle",), evaluated_at_seq=rc.next_seq(),
        evaluated_at=EPOCH,
    )
    gt = build_ground_truth(
        fault=fault, verdicts=(vr,), accepted_evidence_ids=onset.evidence_ids,
        root_cause_label="bgp_remote_as_mismatch",
    )
    prov = ProvenanceInfo(generator="g", generator_version="0.1.0", code_commit="deadbeef")
    incident = build_accepted_record(
        run_ctx=rc, scenario=scen, topology=topo, fault=fault, ground_truth=gt,
        baseline=baseline, onset=onset, recovery=recovery, precondition_results=(vr,),
        onset_results=(vr,), recovery_results=(vr,),
        restoration=RestorationMetadata(method="m", forced_reset_used=True,
            forced_reset_command="clear bgp 172.30.0.2", transcript_refs=(3, 4), completed=True),
        provenance=prov,
        completed_phases=("precondition", "inject", "onset", "restore", "recovery"),
        cleanup_status="clean",
    )
    inv = CommandInvocation(
        command_id="cmd-000000000000abcd", target="router_a",
        logical_argv=("vtysh", "-c", "configure terminal"),
        transport_argv=(
            "docker", "compose", "exec", "-T", "router_a", "vtysh", "-c", "configure terminal",
        ),
    )
    transcript = (
        TranscriptEntry(seq=1, mode="read", stage="completed", target="router_a",
            argv=("vtysh", "-c", "show version"), status="ok", started_at=EPOCH),
        TranscriptEntry(seq=2, mode="mutation", stage="pending", target="router_a",
            argv=inv.transport_argv, status="pending", started_at=EPOCH, invocation=inv),
        TranscriptEntry(seq=2, mode="mutation", stage="completed", target="router_a",
            argv=inv.transport_argv, status="ok", started_at=EPOCH, invocation=inv),
    )
    led = Ledger(rc)
    for ph in (LifecyclePhase.PRECHECKED, LifecyclePhase.INJECTING, LifecyclePhase.INJECTED,
               LifecyclePhase.ONSET_VERIFIED, LifecyclePhase.RESTORING, LifecyclePhase.RESTORED,
               LifecyclePhase.RECOVERY_VERIFIED):
        led.append(ph, "")
    rm = RunManifest(
        run_id=run_id, git_rev="deadbeef", lock_hash="b" * 64, scenario_id=scen.scenario_id,
        template_id=scen.template_id, topology_hash=sha256_canonical(topo), started_at=EPOCH,
        acceptance_status="accepted",
    )
    return RunInputs(rm, _env_manifest(), incident, transcript, led.records)


def build_rejected_inputs(run_id: str = "run-test-rej1") -> RunInputs:
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.incidents.builder import build_rejected_record
    from verifiednet.schemas import (
        Phase,
        ProvenanceInfo,
        RejectionCode,
        RunManifest,
        Verdict,
        VerificationResult,
    )

    rc = RunContext(run_id, clock=lambda: EPOCH)
    topo = make_two_router_topology()
    scen = make_scenario()
    baseline = _evidence_bundle(
        rc, Phase.PRECONDITION, "router_a", {"route.203.0.113.99/32.present": "false"}
    )
    ev_id = baseline.records[0].evidence_id
    vr = VerificationResult(
        check_id="route_present:router_a:route.203.0.113.99/32.present:precondition",
        verdict=Verdict.FAIL, phase="precondition", evidence_ids=(ev_id,), observed=("false",),
        evaluated_at_seq=rc.next_seq(), evaluated_at=EPOCH,
    )
    prov = ProvenanceInfo(generator="g", generator_version="0.1.0", code_commit="deadbeef")
    incident = build_rejected_record(
        run_ctx=rc, scenario=scen, topology=topo, baseline=baseline,
        rejection_code=RejectionCode.PRECONDITION_FAILED,
        details="required route 203.0.113.99/32 was absent on router_a",
        failed_phase="precondition", precondition_results=(vr,), provenance=prov,
        completed_phases=(), cleanup_status="clean",
    )
    rm = RunManifest(
        run_id=run_id, git_rev="deadbeef", lock_hash="b" * 64, scenario_id=scen.scenario_id,
        template_id=scen.template_id, topology_hash=sha256_canonical(topo), started_at=EPOCH,
        acceptance_status="rejected",
    )
    return RunInputs(rm, _env_manifest(), incident, (), ())


@pytest.fixture
def accepted_run_inputs() -> RunInputs:
    return build_accepted_inputs()


@pytest.fixture
def rejected_run_inputs() -> RunInputs:
    return build_rejected_inputs()


@pytest.fixture
def make_accepted_inputs() -> Callable[[str], RunInputs]:
    return build_accepted_inputs


@pytest.fixture
def make_rejected_inputs() -> Callable[[str], RunInputs]:
    return build_rejected_inputs


@pytest.fixture
def make_live_manifests() -> Callable[..., tuple]:
    """Build (RunManifest, EnvironmentManifest) from a live backend + run context."""
    import re
    from pathlib import Path as _Path

    from verifiednet.common.hashing import sha256_canonical, sha256_file
    from verifiednet.runtime.process import default_runner
    from verifiednet.schemas import EnvironmentManifest, RunManifest

    def _make(backend: object, run_ctx: RunContext, scenario: object, *, status: str) -> tuple:
        topo = backend.topology()  # type: ignore[attr-defined]
        meta = backend.capture_environment_metadata()  # type: ignore[attr-defined]
        vr = backend.execute_readonly(  # type: ignore[attr-defined]
            topo.nodes[0].name, ["vtysh", "-c", "show version"], 10.0
        )
        match = re.search(r"FRRouting (\S+)", vr.stdout)
        rev = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip()
        commit = rev or "unknown"
        lock = _Path("uv.lock")
        lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64
        env = EnvironmentManifest(
            os_name=meta["os_name"], kernel=meta["kernel"], arch=meta["arch"],
            python_version=meta["python_version"], container_runtime=meta["container_runtime"],
            container_runtime_version=meta.get("container_runtime_version", ""),
            image_reference=meta["image_reference"],
            image_manifest_digest=meta.get("image_manifest_digest"),
            platform_resolved_digest=meta.get("platform_resolved_repo_digest"),
            frr_version=match.group(1) if match else None, captured_at=run_ctx.now(),
        )
        rm = RunManifest(
            run_id=run_ctx.run_id, git_rev=commit, lock_hash=lock_hash,
            scenario_id=scenario.scenario_id, template_id=scenario.template_id,  # type: ignore[attr-defined]
            topology_hash=sha256_canonical(topo),
            image_digests={"frr": topo.images.frr}, started_at=run_ctx.now(),
            acceptance_status=status,  # type: ignore[arg-type]
        )
        return rm, env

    return _make


@pytest.fixture
def write_inputs() -> Callable[..., object]:
    """Return a helper that writes a RunInputs to a directory and returns WrittenRun."""
    from verifiednet.artifacts import write_run_artifacts

    def _write(inputs: RunInputs, out_root: object) -> object:
        return write_run_artifacts(
            out_root=out_root,  # type: ignore[arg-type]
            run_manifest=inputs.run_manifest,  # type: ignore[arg-type]
            environment_manifest=inputs.environment_manifest,  # type: ignore[arg-type]
            incident=inputs.incident,  # type: ignore[arg-type]
            transcript_entries=inputs.transcript_entries,
            ledger_records=inputs.ledger_records,
        )

    return _write


# --------------------------------------------------------------------------
# Gate 6.2: rejected precondition run with a DISTINCT stable identity.
#
# The rejected run must NOT share a leakage group with any accepted catalog
# case, so it uses a distinct template + scenario_id + an IMPOSSIBLE target
# prefix (203.0.113.99/32) whose precondition (prefix present before
# withdrawal) fails. It carries a sealed baseline, no fault, no ground truth,
# no restoration, an empty ledger, and ZERO mutation transcript entries.
# --------------------------------------------------------------------------

REJECT_IMPOSSIBLE_PREFIX = "203.0.113.99/32"


def make_rejected_prefix_scenario() -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id="bgp-prefix-withdrawal-reject-2r-0001",
        family="bgp",
        template_id="bgp_prefix_withdrawal",
        version=1,
        parameters={
            "target_prefix": REJECT_IMPOSSIBLE_PREFIX,
            "target_node": "router_a",
            "target_session": "a-b",
        },
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0,
            command_s=10.0, poll_interval_s=0.5,
        ),
    )


def build_rejected_prefix_inputs(run_id: str = "run-rej-prefix") -> RunInputs:
    """A precondition-rejected run for an impossible target prefix (distinct id)."""
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.incidents.builder import build_rejected_record
    from verifiednet.schemas import (
        Phase,
        ProvenanceInfo,
        RejectionCode,
        RunManifest,
        Verdict,
        VerificationResult,
    )

    rc = RunContext(run_id, clock=lambda: EPOCH)
    topo = make_two_router_topology()
    scen = make_rejected_prefix_scenario()
    baseline = _evidence_bundle(
        rc, Phase.PRECONDITION, "router_a",
        {f"route.{REJECT_IMPOSSIBLE_PREFIX}.present": "false"},
    )
    ev_id = baseline.records[0].evidence_id
    vr = VerificationResult(
        check_id=f"route_present:router_a:route.{REJECT_IMPOSSIBLE_PREFIX}.present:precondition",
        verdict=Verdict.FAIL, phase="precondition", evidence_ids=(ev_id,),
        observed=("false",), evaluated_at_seq=rc.next_seq(), evaluated_at=EPOCH,
    )
    prov = ProvenanceInfo(generator="g", generator_version="0.1.0", code_commit="deadbeef")
    incident = build_rejected_record(
        run_ctx=rc, scenario=scen, topology=topo, baseline=baseline,
        rejection_code=RejectionCode.PRECONDITION_FAILED,
        details=f"required prefix {REJECT_IMPOSSIBLE_PREFIX} was absent on router_a",
        failed_phase="precondition", precondition_results=(vr,), provenance=prov,
        completed_phases=(), cleanup_status="clean",
    )
    rm = RunManifest(
        run_id=run_id, git_rev="deadbeef", lock_hash="b" * 64, scenario_id=scen.scenario_id,
        template_id=scen.template_id, topology_hash=sha256_canonical(topo), started_at=EPOCH,
        acceptance_status="rejected",
    )
    return RunInputs(rm, _env_manifest(), incident, (), ())


def write_and_index_run(inputs: RunInputs, out_root: object) -> object:
    """Write a RunInputs to *out_root* and add it to the run index."""
    from verifiednet.artifacts import add_run_to_index, write_run_artifacts

    written = write_run_artifacts(
        out_root=out_root,  # type: ignore[arg-type]
        run_manifest=inputs.run_manifest,  # type: ignore[arg-type]
        environment_manifest=inputs.environment_manifest,  # type: ignore[arg-type]
        incident=inputs.incident,  # type: ignore[arg-type]
        transcript_entries=inputs.transcript_entries,
        ledger_records=inputs.ledger_records,
    )
    add_run_to_index(out_root, written.run_id)  # type: ignore[arg-type]
    return written


@pytest.fixture
def make_rejected_prefix_inputs() -> Callable[[str], RunInputs]:
    return build_rejected_prefix_inputs


@pytest.fixture
def write_indexed_run() -> Callable[..., object]:
    return write_and_index_run


# --------------------------------------------------------------------------
# Gate 6.2 Part 3: build a mixed (accepted + rejected) assigned corpus offline,
# ready to export. Returns (assigned_examples, policy, source_index_digest).
# --------------------------------------------------------------------------


@pytest.fixture
def export_corpus() -> Callable[..., object]:
    """Return a helper building an assigned mixed corpus in a fresh library.

    ``helper(tmp_path, accepted=[(case_id, run_id), ...], rejected=[run_id, ...],
    policy=None)`` writes the runs, projects + assigns them, and returns a tuple
    ``(assigned, policy, source_index_digest, out_root)``.
    """
    from verifiednet.datasets import (
        SplitPolicy,
        assign_splits,
        discover_verified_runs,
        project_verified_run,
    )
    from verifiednet.orchestrator.catalog import case_by_id

    def _helper(tmp_path, *, accepted, rejected=(), policy=None):
        out_root = tmp_path / "runs"

        class _Clk:
            def __init__(self) -> None:
                self.t = 0.0

            def monotonic(self) -> float:
                return self.t

            def sleep(self, s: float) -> None:
                self.t += s

        for case_id, run_id in accepted:
            run_catalog_case_offline(case_by_id(case_id), out_root, tmp_path,
                                     run_id=run_id, sim=CatalogLabSim())
        for run_id in rejected:
            write_and_index_run(build_rejected_prefix_inputs(run_id), out_root)

        examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
        pol = policy or SplitPolicy(salt="gate6", train_buckets=8000,
                                    validation_buckets=1000, test_buckets=1000)
        assigned = assign_splits(examples=examples, policy=pol)
        digest = next(iter(discover_verified_runs(out_root))).source_index_digest
        return assigned, pol, digest, out_root

    return _helper


@pytest.fixture
def separated_pipeline() -> Callable[..., object]:
    """Build runs -> Part 3 export -> load -> separate. Returns a rich context.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.loaded`` (LoadedDataset), ``.separated`` (tuple), ``.dataset`` (built
    ExportedDataset), ``.dataset_dir`` (written Part 3 dir), ``.run_root``,
    ``.source_index_digest``, ``.feature_policy``, ``.label_policy``.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.datasets import (
        SplitPolicy,
        assign_splits,
        build_dataset,
        discover_verified_runs,
        project_verified_run,
        read_dataset,
        write_dataset,
    )
    from verifiednet.datasets.features import FeaturePolicy, LabelPolicy
    from verifiednet.datasets.separation import separate_dataset
    from verifiednet.orchestrator.catalog import case_by_id

    @_dc(frozen=True)
    class _Ctx:
        loaded: object
        separated: object
        dataset: object
        dataset_dir: object
        run_root: object
        source_index_digest: str
        feature_policy: object
        label_policy: object

    def _helper(tmp_path, *, accepted, rejected=(), policy=None):
        out_root = tmp_path / "runs"
        for case_id, run_id in accepted:
            run_catalog_case_offline(case_by_id(case_id), out_root, tmp_path,
                                     run_id=run_id, sim=CatalogLabSim())
        for run_id in rejected:
            write_and_index_run(build_rejected_prefix_inputs(run_id), out_root)

        examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
        pol = policy or SplitPolicy(salt="gate6", train_buckets=8000,
                                    validation_buckets=1000, test_buckets=1000)
        assigned = assign_splits(examples=examples, policy=pol)
        idx = next(iter(discover_verified_runs(out_root))).source_index_digest
        ds = build_dataset(assigned, policy=pol, dataset_version="v1",
                           source_index_digest=idx)
        dataset_dir = tmp_path / "dataset"
        write_dataset(ds, dataset_dir)
        loaded = read_dataset(dataset_dir)
        fp, lp = FeaturePolicy(), LabelPolicy()
        sep = separate_dataset(loaded.examples, feature_policy=fp, label_policy=lp,
                               dataset_version=loaded.manifest.dataset_version,
                               source_index_digest=idx)
        return _Ctx(loaded=loaded, separated=sep, dataset=ds, dataset_dir=dataset_dir,
                    run_root=out_root, source_index_digest=idx,
                    feature_policy=fp, label_policy=lp)

    return _helper


@pytest.fixture
def eval_pipeline(separated_pipeline) -> Callable[..., object]:
    """Build the full chain up to a written+loaded prepared corpus for Gate 7.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.loaded`` (LoadedPrepared), ``.prepared_dir``, ``.run_root``,
    ``.dataset_dir``, and ``.source_dataset_digest``.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.datasets import build_prepared, load_prepared, write_prepared

    @_dc(frozen=True)
    class _E:
        loaded: object
        prepared_dir: object
        run_root: object
        dataset_dir: object
        source_dataset_digest: str

    def _helper(tmp_path, *, accepted, rejected=(), policy=None):
        ctx = separated_pipeline(tmp_path, accepted=accepted, rejected=rejected,
                                 policy=policy)
        prep = build_prepared(
            ctx.separated, feature_policy=ctx.feature_policy,
            label_policy=ctx.label_policy, dataset_version="v1",
            source_index_digest=ctx.source_index_digest,
            source_dataset_digest=ctx.dataset.manifest.dataset_digest)
        prepared_dir = tmp_path / "prepared"
        write_prepared(prep, prepared_dir)
        loaded = load_prepared(prepared_dir)
        return _E(loaded=loaded, prepared_dir=prepared_dir, run_root=ctx.run_root,
                  dataset_dir=ctx.dataset_dir,
                  source_dataset_digest=ctx.dataset.manifest.dataset_digest)

    return _helper


@pytest.fixture
def plan_pipeline(eval_pipeline) -> Callable[..., object]:
    """Gate 10B chain: written training corpus + default spec/trainer context.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.manifest`` (TrainingCorpusManifest), ``.descriptor``, ``.spec`` (a valid
    default TrainingSpec bound to the corpus), ``.trainer`` (FakeTrainer),
    ``.make_spec(**overrides)``, ``.corpus_root``, and the upstream roots.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.evaluation import diagnosis_task
    from verifiednet.training import (
        FAKE_TRAINER_IMPLEMENTATION_ID,
        BatchConfig,
        EpochBudget,
        FakeTrainer,
        OptimizationConfig,
        SchedulerConfig,
        SeedPolicy,
        SequenceLengthPolicy,
        TokenizerSpec,
        TrainableModelSpec,
        build_training_corpus,
        build_training_spec,
        derive_model_spec_id,
        derive_tokenizer_spec_id,
        descriptor_from_manifest,
        diagnosis_input_template,
        diagnosis_target_template,
        diagnosis_training_policy,
        load_training_corpus,
        write_training_corpus,
    )

    def _model() -> TrainableModelSpec:
        return TrainableModelSpec(
            provider="fake", model_identifier="fake/tiny-slm",
            model_revision="a" * 40, model_class="FakeCausalLM",
            model_spec_id=derive_model_spec_id(
                provider="fake", model_identifier="fake/tiny-slm",
                model_revision="a" * 40, model_class="FakeCausalLM",
                load_precision="float32"))

    def _tokenizer() -> TokenizerSpec:
        return TokenizerSpec(
            tokenizer_identifier="fake/tiny-slm", tokenizer_revision="a" * 40,
            tokenizer_class="FakeTokenizer",
            tokenizer_spec_id=derive_tokenizer_spec_id(
                tokenizer_identifier="fake/tiny-slm", tokenizer_revision="a" * 40,
                tokenizer_class="FakeTokenizer",
                special_vocab_policy="model_defaults", padding_policy="right",
                truncation_policy="fail_closed"))

    @_dc(frozen=True)
    class _P:
        manifest: object
        descriptor: object
        spec: object
        trainer: object
        make_spec: object
        corpus_root: object
        run_root: object
        dataset_dir: object
        prepared_dir: object

    def _helper(tmp_path, *, accepted, rejected=()):
        ctx = eval_pipeline(tmp_path, accepted=accepted, rejected=rejected)
        task_id = diagnosis_task().task_id
        fp = ctx.loaded.manifest.feature_policy_id
        itpl = diagnosis_input_template(task_id=task_id, feature_policy_id=fp)
        ttpl = diagnosis_target_template(task_id=task_id)
        policy = diagnosis_training_policy(task_id=task_id, input_template=itpl,
                                           target_template=ttpl)
        corpus = build_training_corpus(ctx.loaded, training_data_policy=policy,
                                       input_template=itpl, target_template=ttpl)
        written = write_training_corpus(corpus, tmp_path / "training-corpora")
        manifest = load_training_corpus(written.root).manifest

        def make_spec(**overrides):
            fields = dict(
                training_corpus_id=manifest.training_corpus_id,
                training_corpus_digest=manifest.training_corpus_digest,
                task_id=task_id, model=_model(), tokenizer=_tokenizer(),
                trainer_implementation_id=FAKE_TRAINER_IMPLEMENTATION_ID,
                sequence_policy=SequenceLengthPolicy(
                    max_input_tokens=512, max_target_tokens=64, max_total_tokens=576),
                batch=BatchConfig(per_device_batch_size=2,
                                  gradient_accumulation_steps=2,
                                  effective_batch_size=4),
                optimization=OptimizationConfig(optimizer_name="adamw",
                                                learning_rate="1e-4"),
                scheduler=SchedulerConfig(scheduler_name="linear_warmup",
                                          warmup_steps=1),
                budget=EpochBudget(epochs=3),
                seed_policy=SeedPolicy(data_order_seed=1, model_init_seed=2,
                                       dropout_seed=3, backend_seed=4))
            fields.update(overrides)
            return build_training_spec(**fields)

        return _P(manifest=manifest, descriptor=descriptor_from_manifest(manifest),
                  spec=make_spec(), trainer=FakeTrainer(), make_spec=make_spec,
                  corpus_root=written.root, run_root=ctx.run_root,
                  dataset_dir=ctx.dataset_dir, prepared_dir=ctx.prepared_dir)

    return _helper


@pytest.fixture
def execution_pipeline(plan_pipeline) -> Callable[..., object]:
    """Gate 10C chain: verified plan + fake execution engine + retry policy.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.plan`` (a default TrainingPlan), ``.engine`` (FakeExecutionEngine),
    ``.policy`` (max_retries=2, allow_resume=True by default), ``.make_plan``
    (rebuild the plan from spec overrides), and ``.planctx`` (the underlying
    plan_pipeline context). Default plan shape: 3 examples / batch 2 =
    2 batches/epoch; accumulation 2 = 1 step/epoch; 3 epochs = 3 steps.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.training import FakeExecutionEngine, build_execution_policy

    @_dc(frozen=True)
    class _X:
        plan: object
        engine: object
        policy: object
        make_plan: object
        planctx: object

    def _helper(tmp_path, *, accepted, rejected=(), max_retries=2,
                allow_resume=True):
        ctx = plan_pipeline(tmp_path, accepted=accepted, rejected=rejected)

        def make_plan(**spec_overrides):
            return ctx.trainer.plan(spec=ctx.make_spec(**spec_overrides),
                                    corpus=ctx.descriptor)

        return _X(plan=make_plan(), engine=FakeExecutionEngine(),
                  policy=build_execution_policy(
                      max_retries=max_retries, allow_resume=allow_resume),
                  make_plan=make_plan, planctx=ctx)

    return _helper


@pytest.fixture
def checkpoint_pipeline(execution_pipeline) -> Callable[..., object]:
    """Gate 10D chain: persisted plan + persisted completed execution +
    checkpoint format spec / production policy / fake producer.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.plan_dir``, ``.exec_dir`` (a verified COMPLETED execution),
    ``.format_spec``, ``.production_policy``, ``.producer``,
    ``.run_execution(**script)`` (persist another execution of the same plan
    under a fresh subdirectory; returns its directory), and ``.execctx``.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.training import (
        FakeCheckpointProducer,
        build_default_checkpoint_production_policy,
        build_fake_checkpoint_format_spec,
        write_training_execution,
        write_training_plan,
    )

    @_dc(frozen=True)
    class _C:
        plan_dir: object
        exec_dir: object
        format_spec: object
        production_policy: object
        producer: object
        run_execution: object
        execctx: object

    def _helper(tmp_path, *, accepted, rejected=()):
        ctx = execution_pipeline(tmp_path, accepted=accepted, rejected=rejected)
        written_plan = write_training_plan(ctx.plan, tmp_path / "training-plans")
        completed = ctx.engine.execute(ctx.plan, policy=ctx.policy)
        written_exec = write_training_execution(
            completed, tmp_path / "training-executions")
        counter = {"n": 0}

        def run_execution(**script):
            counter["n"] += 1
            ex = ctx.engine.execute(ctx.plan, policy=ctx.policy, **script)
            w = write_training_execution(
                ex, tmp_path / f"training-executions-{counter['n']}")
            return w.root

        return _C(plan_dir=written_plan.root, exec_dir=written_exec.root,
                  format_spec=build_fake_checkpoint_format_spec(),
                  production_policy=build_default_checkpoint_production_policy(),
                  producer=FakeCheckpointProducer(),
                  run_execution=run_execution, execctx=ctx)

    return _helper


@pytest.fixture
def preflight_pipeline(plan_pipeline) -> Callable[..., object]:
    """Gate 10E chain: a REAL-backend-bound plan + fake probe/resolvers.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.plan_dir`` (a persisted plan whose trainer implementation is the HF
    full-finetune backend), ``.corpus_root``, ``.backend`` (adapter over a
    deterministic FakeEnvironmentProbe), ``.model_resolver`` /
    ``.tokenizer_resolver`` (fake, cached), ``.make_backend(probe)``,
    ``.make_hf_spec(**overrides)``, ``.hf_spec``, and ``.planctx``.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.training import (
        HF_FULL_FINETUNE_BACKEND_ID,
        FakeEnvironmentProbe,
        FakeModelArtifactResolver,
        FakeTokenizerArtifactResolver,
        HuggingFaceFullFinetuneBackend,
        TokenizerSpec,
        TrainableModelSpec,
        derive_model_spec_id,
        derive_tokenizer_spec_id,
        plan_for_real_backend,
        write_training_plan,
    )

    def _hf_model() -> TrainableModelSpec:
        fields = dict(provider="huggingface",
                      model_identifier="verifiednet-test/tiny-slm",
                      model_revision="b" * 40,
                      model_class="AutoModelForCausalLM")
        return TrainableModelSpec(
            **fields,
            model_spec_id=derive_model_spec_id(load_precision="float32",
                                               **fields))

    def _hf_tokenizer() -> TokenizerSpec:
        fields = dict(tokenizer_identifier="verifiednet-test/tiny-slm",
                      tokenizer_revision="b" * 40,
                      tokenizer_class="AutoTokenizer")
        return TokenizerSpec(
            **fields,
            tokenizer_spec_id=derive_tokenizer_spec_id(
                special_vocab_policy="model_defaults", padding_policy="right",
                truncation_policy="fail_closed", **fields))

    @_dc(frozen=True)
    class _F:
        plan_dir: object
        corpus_root: object
        backend: object
        model_resolver: object
        tokenizer_resolver: object
        make_backend: object
        make_hf_spec: object
        hf_spec: object
        planctx: object

    def _helper(tmp_path, *, accepted, rejected=()):
        ctx = plan_pipeline(tmp_path, accepted=accepted, rejected=rejected)

        def make_hf_spec(**overrides):
            fields = dict(model=_hf_model(), tokenizer=_hf_tokenizer(),
                          trainer_implementation_id=HF_FULL_FINETUNE_BACKEND_ID)
            fields.update(overrides)
            return ctx.make_spec(**fields)

        hf_spec = make_hf_spec()
        plan = plan_for_real_backend(spec=hf_spec, corpus=ctx.descriptor)
        written_plan = write_training_plan(plan, tmp_path / "hf-plans")

        def make_backend(probe=None):
            return HuggingFaceFullFinetuneBackend(
                probe if probe is not None else FakeEnvironmentProbe())

        return _F(plan_dir=written_plan.root, corpus_root=ctx.corpus_root,
                  backend=make_backend(),
                  model_resolver=FakeModelArtifactResolver(),
                  tokenizer_resolver=FakeTokenizerArtifactResolver(),
                  make_backend=make_backend, make_hf_spec=make_hf_spec,
                  hf_spec=hf_spec, planctx=ctx)

    return _helper


# --------------------------------------------------------------------------
# Gate 5.2: deterministic neighbor-removal lab sim + builder, shared by the
# unit and failure tiers (tests/ is not a package; shared helpers live here).
# --------------------------------------------------------------------------

NEIGHBOR_PEER_IP = "172.30.0.2"
NEIGHBOR_CORRECT_AS = 65002


class NeighborLabSim:
    """Deterministic lab: the neighbor object on router_a is the mutable state.

    ``fail_command`` injects per-command failures; ``ignore_activate`` models a
    restore that recreates the neighbor but never activates it (session returns,
    routes do not).
    """

    def __init__(self, *, fail_command=None, ignore_activate: bool = False) -> None:
        self.neighbor_present = True
        self.activated = True
        self.fail_command = fail_command
        self.ignore_activate = ignore_activate
        self.mutation_targets: list[str] = []

    @property
    def session_up(self) -> bool:
        return self.neighbor_present

    @property
    def routes_exchanged(self) -> bool:
        return self.neighbor_present and self.activated

    def _bgp_summary(self, service: str) -> str:
        if service == "router_a":
            if not self.neighbor_present:
                # Live-verified FRR 8.4.1 behavior: with the LAST ipv4-unicast
                # neighbor removed, the whole ipv4Unicast object is omitted.
                return _json.dumps({})
            peers = {NEIGHBOR_PEER_IP: {
                "state": "Established" if self.session_up else "Idle",
                "remoteAs": NEIGHBOR_CORRECT_AS,
            }}
            local = 65001
        else:
            peers = {"172.30.0.1": {
                "state": "Established" if self.session_up else "Idle",
                "remoteAs": 65001,
            }}
            local = 65002
        return _json.dumps({"ipv4Unicast": {"as": local, "peers": peers}})

    @staticmethod
    def _interfaces() -> str:
        return _json.dumps({
            "eth1": {"administrativeStatus": "up", "operationalStatus": "up"},
            "lo": {"administrativeStatus": "up", "operationalStatus": "up"},
        })

    def _routes(self, service: str) -> str:
        table: dict[str, list[dict[str, object]]] = {}
        if service == "router_a":
            table["10.255.0.1/32"] = [{"protocol": "connected"}]
            if self.routes_exchanged:
                table["10.255.0.2/32"] = [{"protocol": "bgp"}]
        else:
            table["10.255.0.2/32"] = [{"protocol": "connected"}]
            if self.routes_exchanged:
                table["10.255.0.1/32"] = [{"protocol": "bgp"}]
        return _json.dumps(table)

    def _running_config(self, service: str) -> str:
        # Canonical serialization: a pure function of the logical state, so a
        # restored config is byte-identical — the property live FRR provides.
        if service != "router_a":
            return (
                "frr version 8.4.1_git\nhostname router_b\nrouter bgp 65002\n"
                " neighbor 172.30.0.1 remote-as 65001\n"
            )
        lines = ["frr version 8.4.1_git", "hostname router_a", "router bgp 65001"]
        if self.neighbor_present:
            lines.append(f" neighbor {NEIGHBOR_PEER_IP} remote-as {NEIGHBOR_CORRECT_AS}")
        lines.append(" address-family ipv4 unicast")
        lines.append("  network 10.255.0.1/32")
        if self.neighbor_present and self.activated:
            lines.append(f"  neighbor {NEIGHBOR_PEER_IP} activate")
        lines.append(" exit-address-family")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _cmds(logical: list[str]) -> list[str]:
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def __call__(self, argv, timeout_s, max_output_bytes):
        from verifiednet.runtime.process import RawResult

        a = list(argv)
        exec_idx = a.index("exec")
        service = a[exec_idx + 2]
        logical = a[exec_idx + 3:]
        cmds = self._cmds(logical)
        if self.fail_command is not None and self.fail_command(cmds):
            return RawResult(1, "", "vtysh: command failed", False, False, False)
        if logical[0] == "ping":
            return RawResult(0, "1 received", "", False, False, False)
        first = cmds[0] if cmds else ""
        if first.startswith("show"):
            if first == "show ip bgp summary json":
                return RawResult(0, self._bgp_summary(service), "", False, False, False)
            if first == "show interface json":
                return RawResult(0, self._interfaces(), "", False, False, False)
            if first == "show ip route json":
                return RawResult(0, self._routes(service), "", False, False, False)
            if first == "show running-config":
                return RawResult(0, self._running_config(service), "", False, False, False)
            raise AssertionError(f"unexpected show: {first!r}")
        # mutation path
        self.mutation_targets.append(service)
        for cmd in cmds:
            if cmd.startswith("no neighbor"):
                self.neighbor_present = False
                self.activated = False
            elif cmd.startswith("neighbor") and "remote-as" in cmd:
                self.neighbor_present = True
            elif cmd.startswith("neighbor") and cmd.endswith("activate"):
                if not self.ignore_activate:
                    self.activated = True
        return RawResult(0, "", "", False, False, False)


def build_neighbor_removal_scenario(sim: NeighborLabSim, run_ctx: RunContext, tmp_path):
    """Wire the REAL scenario + executor + provider around *sim*; returns
    (scenario, ledger, provider, backend)."""
    from verifiednet.faults.bgp_neighbor_removal import BgpNeighborRemovalScenario
    from verifiednet.faults.ledger import Ledger
    from verifiednet.labs.frr.backend import FrrComposeBackend
    from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
    from verifiednet.labs.frr.topologies import two_router_frr_topology
    from verifiednet.orchestrator.families import _neighbor_removal_phase_plans
    from verifiednet.runtime.policy import bgp_neighbor_removal_mutation_shapes
    from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts
    from verifiednet.verifiers.claims import ClaimVerifier

    scenario_definition = ScenarioDefinition(
        scenario_id="bgp-neighbor-removal-2r-0001",
        family="bgp",
        template_id="bgp_neighbor_removal",
        version=1,
        parameters={"target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0,
            command_s=10.0, poll_interval_s=0.5,
        ),
    )
    topology = two_router_frr_topology()
    backend = FrrComposeBackend(topology, run_ctx, work_dir=tmp_path, runner=sim)
    provider = LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor,
        topology=topology,
        run_ctx=run_ctx,
        target_node="router_a",
        peer_node="router_b",
        phase_plans=_neighbor_removal_phase_plans(topology, "router_a", "router_b"),
    )
    mutation = backend.build_mutation_adapter(
        allowed_targets=("router_a",),
        allowed_shapes=bgp_neighbor_removal_mutation_shapes(),
    )
    ledger = Ledger(run_ctx)

    class _Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def monotonic(self) -> float:
            return self.t

        def sleep(self, s: float) -> None:
            self.t += s

    clock = _Clock()
    scenario = BgpNeighborRemovalScenario(
        topology=topology,
        scenario=scenario_definition,
        mutation=mutation,
        ledger=ledger,
        run_ctx=run_ctx,
        evidence_provider=provider,
        verifier=ClaimVerifier(run_ctx),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return scenario, ledger, provider, backend


@pytest.fixture
def neighbor_sim_cls() -> type[NeighborLabSim]:
    return NeighborLabSim


@pytest.fixture
def build_neighbor_scenario():
    return build_neighbor_removal_scenario


# --------------------------------------------------------------------------
# Gate 5.3: interface-shutdown lab sim + builder (probe-driven behavior).
# --------------------------------------------------------------------------

IFACE_PEER_IP = "172.30.0.2"


class IfaceLabSim:
    """Deterministic lab: eth1 admin state on router_a is the mutable fault.

    Probe-verified behavior: admin down => oper down => target session leaves
    Established, ping fails, peer-loopback route withdrawn on the target. The
    peer keeps Established during onset (hold timer) and is only re-checked at
    recovery. ``fail_command`` injects per-command failures.
    """

    def __init__(self, *, fail_command=None) -> None:
        self.eth1_up = True
        self.fail_command = fail_command
        self.mutation_targets: list[str] = []

    @property
    def session_up(self) -> bool:
        return self.eth1_up

    def _bgp_summary(self, service: str) -> str:
        if service == "router_a":
            peers = {IFACE_PEER_IP: {
                "state": "Established" if self.eth1_up else "Active",
                "remoteAs": 65002,
            }}
            local = 65001
        else:
            # peer holds Established while the target link is down (hold timer)
            peers = {"172.30.0.1": {"state": "Established", "remoteAs": 65001}}
            local = 65002
        return _json.dumps({"ipv4Unicast": {"as": local, "peers": peers}})

    def _interfaces(self, service: str) -> str:
        if service == "router_a" and not self.eth1_up:
            eth1 = {"administrativeStatus": "down", "operationalStatus": "down"}
        else:
            eth1 = {"administrativeStatus": "up", "operationalStatus": "up"}
        return _json.dumps({
            "eth1": eth1,
            "lo": {"administrativeStatus": "up", "operationalStatus": "up"},
        })

    def _routes(self, service: str) -> str:
        table: dict[str, list[dict[str, object]]] = {}
        if service == "router_a":
            table["10.255.0.1/32"] = [{"protocol": "connected"}]
            if self.eth1_up:
                table["10.255.0.2/32"] = [{"protocol": "bgp"}]
        else:
            table["10.255.0.2/32"] = [{"protocol": "connected"}]
            if self.eth1_up:
                table["10.255.0.1/32"] = [{"protocol": "bgp"}]
        return _json.dumps(table)

    def _running_config(self, service: str) -> str:
        if service != "router_a":
            return (
                "frr version 8.4.1_git\nhostname router_b\nrouter bgp 65002\n"
                " neighbor 172.30.0.1 remote-as 65001\n"
            )
        lines = ["frr version 8.4.1_git", "hostname router_a", "interface eth1"]
        lines.append(" ip address 172.30.0.1/30")
        if not self.eth1_up:
            lines.append(" shutdown")
        lines.append("router bgp 65001")
        lines.append(f" neighbor {IFACE_PEER_IP} remote-as 65002")
        return "\n".join(lines) + "\n"

    def _ping(self, service: str) -> bool:
        return self.eth1_up

    @staticmethod
    def _cmds(logical: list[str]) -> list[str]:
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def __call__(self, argv, timeout_s, max_output_bytes):
        from verifiednet.runtime.process import RawResult

        a = list(argv)
        exec_idx = a.index("exec")
        service = a[exec_idx + 2]
        logical = a[exec_idx + 3:]
        cmds = self._cmds(logical)
        if self.fail_command is not None and self.fail_command(cmds):
            return RawResult(1, "", "vtysh: command failed", False, False, False)
        if logical[0] == "ping":
            if self._ping(service):
                return RawResult(0, "1 received", "", False, False, False)
            return RawResult(1, "0 received", "", False, False, False)
        first = cmds[0] if cmds else ""
        if first.startswith("show"):
            if first == "show ip bgp summary json":
                return RawResult(0, self._bgp_summary(service), "", False, False, False)
            if first == "show interface json":
                return RawResult(0, self._interfaces(service), "", False, False, False)
            if first == "show ip route json":
                return RawResult(0, self._routes(service), "", False, False, False)
            if first == "show running-config":
                return RawResult(0, self._running_config(service), "", False, False, False)
            raise AssertionError(f"unexpected show: {first!r}")
        self.mutation_targets.append(service)
        for cmd in cmds:
            if cmd == "shutdown":
                self.eth1_up = False
            elif cmd == "no shutdown":
                self.eth1_up = True
        return RawResult(0, "", "", False, False, False)


def build_iface_shutdown_scenario(sim, run_ctx: RunContext, tmp_path):
    from verifiednet.faults.iface_admin_shutdown import IfaceAdminShutdownScenario
    from verifiednet.faults.ledger import Ledger
    from verifiednet.labs.frr.backend import FrrComposeBackend
    from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
    from verifiednet.labs.frr.topologies import two_router_frr_topology
    from verifiednet.orchestrator.families import _iface_shutdown_phase_plans
    from verifiednet.runtime.policy import iface_admin_shutdown_mutation_shapes
    from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts
    from verifiednet.verifiers.claims import ClaimVerifier

    scenario_definition = ScenarioDefinition(
        scenario_id="iface-admin-shutdown-2r-0001", family="interface",
        template_id="iface_admin_shutdown", version=1,
        parameters={"target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(precondition_s=30.0, onset_s=30.0, recovery_s=60.0,
                                  command_s=10.0, poll_interval_s=0.5),
    )
    topology = two_router_frr_topology()
    backend = FrrComposeBackend(topology, run_ctx, work_dir=tmp_path, runner=sim)
    provider = LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor, topology=topology, run_ctx=run_ctx,
        target_node="router_a", peer_node="router_b",
        phase_plans=_iface_shutdown_phase_plans(topology, "router_a", "router_b"),
    )
    mutation = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=iface_admin_shutdown_mutation_shapes())
    ledger = Ledger(run_ctx)

    class _Clock:
        def __init__(self): self.t = 0.0
        def monotonic(self): return self.t
        def sleep(self, s): self.t += s

    clock = _Clock()
    scenario = IfaceAdminShutdownScenario(
        topology=topology, scenario=scenario_definition, mutation=mutation, ledger=ledger,
        run_ctx=run_ctx, evidence_provider=provider, verifier=ClaimVerifier(run_ctx),
        monotonic=clock.monotonic, sleep=clock.sleep)
    return scenario, ledger, provider, backend


@pytest.fixture
def iface_sim_cls():
    return IfaceLabSim


@pytest.fixture
def build_iface_scenario():
    return build_iface_shutdown_scenario


# --------------------------------------------------------------------------
# Gate 5.4: prefix-withdrawal lab sim + builder. Session stays Established;
# only the target's advertised loopback is withdrawn from the peer.
# --------------------------------------------------------------------------

PREFIX_PEER_IP = "172.30.0.2"
PREFIX_TARGET_LOOPBACK = "10.255.0.1/32"


class PrefixLabSim:
    """Deterministic lab: the target's advertised loopback is the mutable state.

    The BGP session never drops — only the peer's view of 10.255.0.1/32 changes.
    ``fail_command`` injects per-command failures.
    """

    def __init__(self, *, fail_command=None) -> None:
        self.advertised = True
        self.fail_command = fail_command
        self.mutation_targets: list[str] = []

    def _bgp_summary(self, service: str) -> str:
        # Session is Established on BOTH sides at all times.
        if service == "router_a":
            peers = {PREFIX_PEER_IP: {"state": "Established", "remoteAs": 65002}}
            local = 65001
        else:
            peers = {"172.30.0.1": {"state": "Established", "remoteAs": 65001}}
            local = 65002
        return _json.dumps({"ipv4Unicast": {"as": local, "peers": peers}})

    @staticmethod
    def _interfaces() -> str:
        return _json.dumps({
            "eth1": {"administrativeStatus": "up", "operationalStatus": "up"},
            "lo": {"administrativeStatus": "up", "operationalStatus": "up"},
        })

    def _routes(self, service: str) -> str:
        table: dict[str, list[dict[str, object]]] = {}
        if service == "router_a":
            table["10.255.0.1/32"] = [{"protocol": "connected"}]
            table["10.255.0.2/32"] = [{"protocol": "bgp"}]  # peer advert unaffected
        else:
            table["10.255.0.2/32"] = [{"protocol": "connected"}]
            if self.advertised:
                table["10.255.0.1/32"] = [{"protocol": "bgp"}]
        return _json.dumps(table)

    def _running_config(self, service: str) -> str:
        if service != "router_a":
            return (
                "frr version 8.4.1_git\nhostname router_b\nrouter bgp 65002\n"
                " address-family ipv4 unicast\n  network 10.255.0.2/32\n"
            )
        lines = ["frr version 8.4.1_git", "hostname router_a", "router bgp 65001",
                 " address-family ipv4 unicast"]
        if self.advertised:
            lines.append("  network 10.255.0.1/32")
        lines.append(f"  neighbor {PREFIX_PEER_IP} activate")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _cmds(logical: list[str]) -> list[str]:
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def __call__(self, argv, timeout_s, max_output_bytes):
        from verifiednet.runtime.process import RawResult

        a = list(argv)
        exec_idx = a.index("exec")
        service = a[exec_idx + 2]
        logical = a[exec_idx + 3:]
        cmds = self._cmds(logical)
        if self.fail_command is not None and self.fail_command(cmds):
            return RawResult(1, "", "vtysh: command failed", False, False, False)
        if logical[0] == "ping":
            return RawResult(0, "1 received", "", False, False, False)  # link always up
        first = cmds[0] if cmds else ""
        if first.startswith("show"):
            if first == "show ip bgp summary json":
                return RawResult(0, self._bgp_summary(service), "", False, False, False)
            if first == "show interface json":
                return RawResult(0, self._interfaces(), "", False, False, False)
            if first == "show ip route json":
                return RawResult(0, self._routes(service), "", False, False, False)
            if first == "show running-config":
                return RawResult(0, self._running_config(service), "", False, False, False)
            raise AssertionError(f"unexpected show: {first!r}")
        self.mutation_targets.append(service)
        for cmd in cmds:
            if cmd.startswith("no network"):
                self.advertised = False
            elif cmd.startswith("network"):
                self.advertised = True
        return RawResult(0, "", "", False, False, False)


def build_prefix_withdrawal_scenario(sim, run_ctx: RunContext, tmp_path):
    from verifiednet.faults.bgp_prefix_withdrawal import BgpPrefixWithdrawalScenario
    from verifiednet.faults.ledger import Ledger
    from verifiednet.labs.frr.backend import FrrComposeBackend
    from verifiednet.labs.frr.scenario_evidence import LiveScenarioEvidenceProvider
    from verifiednet.labs.frr.topologies import two_router_frr_topology
    from verifiednet.orchestrator.families import _prefix_withdrawal_phase_plans
    from verifiednet.runtime.policy import bgp_prefix_withdrawal_mutation_shapes
    from verifiednet.schemas import ScenarioDefinition, ScenarioTimeouts
    from verifiednet.verifiers.claims import ClaimVerifier

    scenario_definition = ScenarioDefinition(
        scenario_id="bgp-prefix-withdrawal-2r-0001", family="bgp",
        template_id="bgp_prefix_withdrawal", version=1,
        parameters={"target_node": "router_a", "target_session": "a-b"},
        timeouts=ScenarioTimeouts(precondition_s=30.0, onset_s=30.0, recovery_s=60.0,
                                  command_s=10.0, poll_interval_s=0.5),
    )
    topology = two_router_frr_topology()
    backend = FrrComposeBackend(topology, run_ctx, work_dir=tmp_path, runner=sim)
    provider = LiveScenarioEvidenceProvider(
        executor=backend.readonly_executor, topology=topology, run_ctx=run_ctx,
        target_node="router_a", peer_node="router_b",
        phase_plans=_prefix_withdrawal_phase_plans(topology, "router_a", "router_b"),
    )
    mutation = backend.build_mutation_adapter(
        allowed_targets=("router_a",), allowed_shapes=bgp_prefix_withdrawal_mutation_shapes())
    ledger = Ledger(run_ctx)

    class _Clock:
        def __init__(self): self.t = 0.0
        def monotonic(self): return self.t
        def sleep(self, s): self.t += s

    clock = _Clock()
    scenario = BgpPrefixWithdrawalScenario(
        topology=topology, scenario=scenario_definition, mutation=mutation, ledger=ledger,
        run_ctx=run_ctx, evidence_provider=provider, verifier=ClaimVerifier(run_ctx),
        monotonic=clock.monotonic, sleep=clock.sleep)
    return scenario, ledger, provider, backend


@pytest.fixture
def prefix_sim_cls():
    return PrefixLabSim


@pytest.fixture
def build_prefix_scenario():
    return build_prefix_withdrawal_scenario


# --------------------------------------------------------------------------
# Gate 5.5/5.6: symmetric catalog lab sim (both routers, all four families),
# shared by the catalog + cross-family test modules (tests/ is not a package).
# --------------------------------------------------------------------------

import json as _json2  # noqa: E402

CATALOG_GIT_REV = "deadbeefcafe"
CATALOG_LOCK_HASH = "b" * 64

_CATALOG_NODES = {
    "router_a": {"asn": 65001, "loopback": "10.255.0.1/32", "peer_ip": "172.30.0.2",
                 "correct_remote_as": 65002, "peer": "router_b", "peer_loopback": "10.255.0.2/32"},
    "router_b": {"asn": 65002, "loopback": "10.255.0.2/32", "peer_ip": "172.30.0.1",
                 "correct_remote_as": 65001, "peer": "router_a", "peer_loopback": "10.255.0.1/32"},
}


class _CatNodeState:
    def __init__(self, name: str) -> None:
        facts = _CATALOG_NODES[name]
        self.name = name
        self.remote_as = int(facts["correct_remote_as"])
        self.has_neighbor = True
        self.eth1_up = True
        self.advertised = True


class CatalogLabSim:
    """Symmetric Docker+FRR simulator: both routers carry independent state."""

    def __init__(self) -> None:
        self.a = _CatNodeState("router_a")
        self.b = _CatNodeState("router_b")
        self._up = False
        self.mutation_targets: list[str] = []

    def _state(self, node: str):
        return self.a if node == "router_a" else self.b

    @property
    def _session_up(self) -> bool:
        return (
            self.a.eth1_up and self.b.eth1_up
            and self.a.has_neighbor and self.b.has_neighbor
            and self.a.remote_as == _CATALOG_NODES["router_a"]["correct_remote_as"]
            and self.b.remote_as == _CATALOG_NODES["router_b"]["correct_remote_as"]
        )

    def _ps(self) -> str:
        if not self._up:
            return ""
        return "cid_a\trouter_a\trunning\ncid_b\trouter_b\trunning"

    def _bgp_summary(self, node: str) -> str:
        st = self._state(node)
        facts = _CATALOG_NODES[node]
        if not st.has_neighbor:
            return _json2.dumps({})
        state = "Established" if self._session_up else "Active"
        peers = {facts["peer_ip"]: {"state": state, "remoteAs": st.remote_as}}
        return _json2.dumps({"ipv4Unicast": {"as": facts["asn"], "peers": peers}})

    def _interfaces(self, node: str) -> str:
        st = self._state(node)
        eth1 = ({"administrativeStatus": "down", "operationalStatus": "down"}
                if not st.eth1_up
                else {"administrativeStatus": "up", "operationalStatus": "up"})
        return _json2.dumps({"eth1": eth1,
                            "lo": {"administrativeStatus": "up", "operationalStatus": "up"}})

    def _routes(self, node: str) -> str:
        facts = _CATALOG_NODES[node]
        peer = self._state(facts["peer"])
        table = {facts["loopback"]: [{"protocol": "connected"}]}
        if self._session_up and peer.advertised:
            table[facts["peer_loopback"]] = [{"protocol": "bgp"}]
        return _json2.dumps(table)

    def _running_config(self, node: str) -> str:
        st = self._state(node)
        facts = _CATALOG_NODES[node]
        lines = [f"hostname {node}", "interface eth1", f" ip address {facts['peer_ip']}/30-face"]
        if not st.eth1_up:
            lines.append(" shutdown")
        lines.append(f"router bgp {facts['asn']}")
        if st.has_neighbor:
            lines.append(f" neighbor {facts['peer_ip']} remote-as {st.remote_as}")
        lines.append(" address-family ipv4 unicast")
        if st.advertised:
            lines.append(f"  network {facts['loopback']}")
        if st.has_neighbor:
            lines.append(f"  neighbor {facts['peer_ip']} activate")
        lines.append(" exit-address-family")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _cmds(logical):
        return [logical[i + 1] for i in range(len(logical)) if logical[i] == "-c"]

    def _exec(self, node: str, logical):
        from verifiednet.runtime.process import RawResult

        if logical and logical[0] == "ping":
            st = self._state(node)
            peer = self._state(_CATALOG_NODES[node]["peer"])
            ok = st.eth1_up and peer.eth1_up
            return RawResult(0 if ok else 1, "1 received" if ok else "0 received", "",
                             False, False, False)
        cmds = self._cmds(logical)
        first = cmds[0] if cmds else ""
        if first.startswith("show"):
            if first == "show ip bgp summary json":
                return RawResult(0, self._bgp_summary(node), "", False, False, False)
            if first == "show interface json":
                return RawResult(0, self._interfaces(node), "", False, False, False)
            if first == "show ip route json":
                return RawResult(0, self._routes(node), "", False, False, False)
            if first == "show running-config":
                return RawResult(0, self._running_config(node), "", False, False, False)
            if first == "show version":
                return RawResult(0, "FRRouting 8.4.1_git (r) on Linux", "", False, False, False)
            raise AssertionError(f"unhandled show: {first!r}")
        self.mutation_targets.append(node)
        st = self._state(node)
        for c in cmds:
            if c.startswith("no neighbor"):
                st.has_neighbor = False
            elif c.startswith("neighbor") and "remote-as" in c:
                st.has_neighbor = True
                st.remote_as = int(c.split()[-1])
            elif c == "shutdown":
                st.eth1_up = False
            elif c == "no shutdown":
                st.eth1_up = True
            elif c.startswith("no network"):
                st.advertised = False
            elif c.startswith("network"):
                st.advertised = True
        return RawResult(0, "", "", False, False, False)

    def __call__(self, argv, timeout_s, max_output_bytes):
        from verifiednet.runtime.process import RawResult

        a = list(argv)
        if a[:2] == ["docker", "ps"]:
            return RawResult(0, self._ps(), "", False, False, False)
        if a[:3] == ["docker", "network", "ls"]:
            return RawResult(0, "", "", False, False, False)
        if a[:2] == ["docker", "version"]:
            return RawResult(0, "29.1.3", "", False, False, False)
        if a[:3] == ["docker", "compose", "version"]:
            return RawResult(0, "v2.29.0", "", False, False, False)
        if a[:3] == ["docker", "image", "inspect"]:
            return RawResult(0, "frrouting/frr@sha256:" + "c" * 64, "", False, False, False)
        if a[:2] == ["docker", "compose"] and "up" in a:
            self._up = True
            return RawResult(0, "", "", False, False, False)
        if a[:2] == ["docker", "compose"] and "down" in a:
            self._up = False
            return RawResult(0, "", "", False, False, False)
        if "exec" in a:
            idx = a.index("exec")
            return self._exec(a[idx + 2], a[idx + 3:])
        raise AssertionError(f"unhandled command: {a!r}")


def _catalog_epoch():
    return datetime(2025, 1, 1, tzinfo=UTC)


def run_catalog_case_offline(case, out_root, tmp_path, run_id=None, sim=None):
    """Run one ScenarioCase through run_accepted_case with a fresh CatalogLabSim."""
    from verifiednet.orchestrator import run_accepted_case

    rid = run_id or f"run-{case.case_id}"

    class _Clk:
        def __init__(self): self.t = 0.0
        def monotonic(self): return self.t
        def sleep(self, s): self.t += s

    clk = _Clk()
    return run_accepted_case(
        case=case, out_root=out_root, work_dir=tmp_path / rid,
        run_ctx=RunContext(rid, clock=_catalog_epoch),
        topology=two_router_frr_topology(),
        git_rev=CATALOG_GIT_REV, lock_hash=CATALOG_LOCK_HASH,
        runner=sim or CatalogLabSim(), monotonic=clk.monotonic, sleep=clk.sleep,
        convergence_timeout_s=5.0,
    )


@pytest.fixture
def catalog_sim_cls():
    return CatalogLabSim


@pytest.fixture
def run_catalog_case():
    return run_catalog_case_offline
