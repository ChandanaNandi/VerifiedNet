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


def make_rejected_prefix_scenario(
    scenario_suffix: str = "2r-0001", target_node: str = "router_a",
) -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id=f"bgp-prefix-withdrawal-reject-{scenario_suffix}",
        family="bgp",
        template_id="bgp_prefix_withdrawal",
        version=1,
        parameters={
            "target_prefix": REJECT_IMPOSSIBLE_PREFIX,
            "target_node": target_node,
            "target_session": "a-b",
        },
        timeouts=ScenarioTimeouts(
            precondition_s=30.0, onset_s=30.0, recovery_s=60.0,
            command_s=10.0, poll_interval_s=0.5,
        ),
    )


def build_rejected_variant_inputs(
    run_id: str, *, scenario_suffix: str = "2r-0001",
    target_node: str = "router_a", topology=None,
) -> RunInputs:
    """A precondition-rejected run with a parameterizable STABLE identity.

    Gate 14: distinct scenario suffixes, target orientations, and topology
    contexts yield distinct abstention leakage groups through the exact same
    honest rejected contract (precondition phase only — the sole rejected
    subtype the Gate 6 projection supports)."""
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
    topo = topology or make_two_router_topology()
    scen = make_rejected_prefix_scenario(scenario_suffix, target_node)
    baseline = _evidence_bundle(
        rc, Phase.PRECONDITION, target_node,
        {f"route.{REJECT_IMPOSSIBLE_PREFIX}.present": "false"},
    )
    ev_id = baseline.records[0].evidence_id
    vr = VerificationResult(
        check_id=(f"route_present:{target_node}:"
                  f"route.{REJECT_IMPOSSIBLE_PREFIX}.present:precondition"),
        verdict=Verdict.FAIL, phase="precondition", evidence_ids=(ev_id,),
        observed=("false",), evaluated_at_seq=rc.next_seq(), evaluated_at=EPOCH,
    )
    prov = ProvenanceInfo(generator="g", generator_version="0.1.0", code_commit="deadbeef")
    incident = build_rejected_record(
        run_ctx=rc, scenario=scen, topology=topo, baseline=baseline,
        rejection_code=RejectionCode.PRECONDITION_FAILED,
        details=(f"required prefix {REJECT_IMPOSSIBLE_PREFIX} was absent on "
                 f"{target_node}"),
        failed_phase="precondition", precondition_results=(vr,), provenance=prov,
        completed_phases=(), cleanup_status="clean",
    )
    rm = RunManifest(
        run_id=run_id, git_rev="deadbeef", lock_hash="b" * 64, scenario_id=scen.scenario_id,
        template_id=scen.template_id, topology_hash=sha256_canonical(topo), started_at=EPOCH,
        acceptance_status="rejected",
    )
    return RunInputs(rm, _env_manifest(), incident, (), ())


def build_rejected_prefix_inputs(run_id: str = "run-rej-prefix") -> RunInputs:
    """A precondition-rejected run for an impossible target prefix (distinct id)."""
    return build_rejected_variant_inputs(run_id)


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

        for entry in accepted:
            if len(entry) == 2:  # legacy: (case_id, run_id) on the v1 topology
                run_catalog_case_offline(case_by_id(entry[0]), out_root,
                                         tmp_path, run_id=entry[1])
            else:  # Gate 14: (ScenarioCase, TopologySpec, run_id)
                case, topo, run_id = entry
                run_catalog_case_offline(case, out_root, tmp_path,
                                         run_id=run_id, topology=topo)
        for rej in rejected:
            if isinstance(rej, str):  # legacy: default rejected identity
                write_and_index_run(build_rejected_prefix_inputs(rej), out_root)
            else:  # Gate 14: (run_id, scenario_suffix, target_node, topology)
                run_id, suffix, target, topo = rej
                write_and_index_run(build_rejected_variant_inputs(
                    run_id, scenario_suffix=suffix, target_node=target,
                    topology=topo), out_root)

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
        for entry in accepted:
            if len(entry) == 2:  # legacy: (case_id, run_id) on the v1 topology
                run_catalog_case_offline(case_by_id(entry[0]), out_root,
                                         tmp_path, run_id=entry[1])
            else:  # Gate 14: (ScenarioCase, TopologySpec, run_id)
                case, topo, run_id = entry
                run_catalog_case_offline(case, out_root, tmp_path,
                                         run_id=run_id, topology=topo)
        for rej in rejected:
            if isinstance(rej, str):  # legacy: default rejected identity
                write_and_index_run(build_rejected_prefix_inputs(rej), out_root)
            else:  # Gate 14: (run_id, scenario_suffix, target_node, topology)
                run_id, suffix, target, topo = rej
                write_and_index_run(build_rejected_variant_inputs(
                    run_id, scenario_suffix=suffix, target_node=target,
                    topology=topo), out_root)

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


@pytest.fixture
def realtrain_pipeline(preflight_pipeline) -> Callable[..., object]:
    """Gate 10F chain: local model fixture + local-resolver authorization +
    bounded policies + stub executor.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` returns an object with
    ``.plan_dir``, ``.corpus_root``, ``.auth_dir``, ``.model_dir``,
    ``.tokenizer_dir``, ``.output_root``, ``.model_policy``, ``.slice_policy``,
    ``.execution_policy``, ``.objective_policy``, ``.executor`` (stub engine),
    ``.execute(**overrides)``, and ``.prectx``. The local model fixture is a
    tiny structurally valid safetensors dir generated by code (never a
    committed binary).
    """
    from dataclasses import dataclass as _dc

    from verifiednet.training import (
        DeterminismCategory,
        LocalModelArtifactResolver,
        LocalTokenizerArtifactResolver,
        build_bounded_model_policy,
        build_causal_lm_objective_policy,
        build_minimal_safetensors,
        build_real_execution_policy,
        build_stub_executor,
        select_corpus_slice,
        write_training_authorization,
    )

    @_dc(frozen=True)
    class _R:
        plan_dir: object
        corpus_root: object
        auth_dir: object
        model_dir: object
        tokenizer_dir: object
        output_root: object
        model_policy: object
        slice_policy: object
        execution_policy: object
        objective_policy: object
        executor: object
        execute: object
        prectx: object

    def _make_local_model(root) -> None:
        root.mkdir(parents=True, exist_ok=True)
        weights = build_minimal_safetensors({
            "wte.weight": ((4, 4), bytes(4 * 4 * 4)),
            "lm_head.weight": ((4, 4), bytes(4 * 4 * 4)),
        })
        (root / "model.safetensors").write_bytes(weights)
        (root / "config.json").write_text(_json.dumps(
            {"architectures": ["AutoModelForCausalLM"], "vocab_size": 4},
            sort_keys=True))
        (root / "tokenizer.json").write_text(_json.dumps(
            {"version": "1.0", "model": {"type": "test-vocab"}},
            sort_keys=True))
        (root / "tokenizer_config.json").write_text(_json.dumps(
            {"tokenizer_class": "AutoTokenizer"}, sort_keys=True))

    def _helper(tmp_path, *, accepted, rejected=()):
        ctx = preflight_pipeline(tmp_path, accepted=accepted, rejected=rejected)
        model_dir = tmp_path / "local-model"
        _make_local_model(model_dir)
        tokenizer_dir = model_dir  # the fixture dir carries all four files

        model_resolver = LocalModelArtifactResolver(model_dir)
        tokenizer_resolver = LocalTokenizerArtifactResolver(tokenizer_dir)
        auth, snapshot = ctx.backend.preflight(
            plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
            model_resolver=model_resolver,
            tokenizer_resolver=tokenizer_resolver)
        assert auth.authorized, [f for f in auth.findings]
        written_auth = write_training_authorization(
            auth, snapshot, tmp_path / "training-authorizations")

        slice_policy, _ = select_corpus_slice(
            ctx.corpus_root, max_example_count=8)
        model_policy = build_bounded_model_policy(
            permitted_model_identifier=ctx.hf_spec.model.model_identifier,
            permitted_model_revision=ctx.hf_spec.model.model_revision,
            permitted_architecture_class=ctx.hf_spec.model.model_class,
            permitted_tokenizer_revision=(
                ctx.hf_spec.tokenizer.tokenizer_revision),
            max_declared_parameter_count=1_000_000,
            max_sequence_length=1024, max_example_count=16, max_epochs=4,
            max_optimizer_steps=16, max_effective_batch_size=4)
        objective_policy = build_causal_lm_objective_policy()
        execution_policy = build_real_execution_policy(
            approved_backend_id=ctx.hf_spec.trainer_implementation_id,
            authorization_id=auth.authorization_id,
            bounded_model_policy_id=model_policy.bounded_model_policy_id,
            corpus_slice_id=slice_policy.corpus_slice_id,
            objective_policy_id=objective_policy.objective_policy_id,
            max_runtime_optimizer_steps=16, max_epochs=4, max_examples=16,
            max_sequence_length=1024, max_effective_batch_size=4,
            determinism_acceptance=(
                DeterminismCategory.DETERMINISTIC_SUPPORTED.value,))
        executor = build_stub_executor()
        output_root = tmp_path / "outputs"

        def execute(**overrides):
            kwargs = dict(
                plan_dir=ctx.plan_dir, corpus_dir=ctx.corpus_root,
                authorization_dir=written_auth.root, model_dir=model_dir,
                tokenizer_dir=tokenizer_dir, output_root=output_root,
                model_policy=model_policy, slice_policy=slice_policy,
                execution_policy=execution_policy,
                objective_policy=objective_policy)
            kwargs.update(overrides)
            return executor.execute(**kwargs)

        return _R(plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
                  auth_dir=written_auth.root, model_dir=model_dir,
                  tokenizer_dir=tokenizer_dir, output_root=output_root,
                  model_policy=model_policy, slice_policy=slice_policy,
                  execution_policy=execution_policy,
                  objective_policy=objective_policy, executor=executor,
                  execute=execute, prectx=ctx)

    return _helper


@pytest.fixture
def ckpt_predictor_pipeline(realtrain_pipeline) -> Callable[..., object]:
    """Gate 11 chain: a stub-produced GENUINE real checkpoint + verified bundle.

    ``helper(tmp_path, accepted=[...], rejected=[...])`` executes the offline
    stub training pipeline to produce a real-format checkpoint, then builds the
    Gate 11 inference compatibility (scoped to the stub architecture), the CPU
    device policy, and a fail-closed verified bundle. Returns an object with
    ``.checkpoint_dir``, ``.compatibility``, ``.device_policy``, ``.bundle``,
    ``.task``, ``.template``, and ``.trainctx``. Entirely offline: no ML
    library, no network, no real model.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.evaluation import (
        build_checkpoint_inference_compatibility,
        build_cpu_inference_device_policy,
        diagnosis_prompt_template,
        diagnosis_task,
        load_verified_checkpoint_bundle,
    )

    @_dc(frozen=True)
    class _C:
        checkpoint_dir: object
        compatibility: object
        device_policy: object
        bundle: object
        task: object
        template: object
        trainctx: object

    def _helper(tmp_path, *, accepted, rejected=()):
        ctx = realtrain_pipeline(tmp_path, accepted=accepted, rejected=rejected)
        written = ctx.execute()
        checkpoint_dir = (
            ctx.output_root / "real-checkpoints" / written.checkpoint_id)
        compatibility = build_checkpoint_inference_compatibility(
            supported_architectures=("AutoModelForCausalLM",))
        device_policy = build_cpu_inference_device_policy()
        bundle = load_verified_checkpoint_bundle(
            checkpoint_dir, compatibility=compatibility)
        return _C(checkpoint_dir=checkpoint_dir, compatibility=compatibility,
                  device_policy=device_policy, bundle=bundle,
                  task=diagnosis_task(), template=diagnosis_prompt_template(),
                  trainctx=ctx)

    return _helper


@pytest.fixture
def matched_pair_pipeline(eval_pipeline, ckpt_predictor_pipeline) -> Callable[..., object]:
    """Gate 12 chain: matched base + trained predictors over ONE prepared corpus.

    ``helper(tmp_path, base_responder=..., trained_responder=...)`` builds the
    prepared evaluation corpus, a stub-produced genuine checkpoint (trained
    side), a tiny verified base-model snapshot dir (base side), constructs both
    matched predictors with fake offline backends, evaluates both through the
    unchanged Gate 7 engine, and assesses fairness. Entirely offline.
    """
    from dataclasses import dataclass as _dc

    from verifiednet.evaluation import (
        FakeInferenceBackend,
        VerifiedBaseModelPredictor,
        VerifiedCheckpointPredictor,
        assess_matched_pair_fairness,
        base_model_predictor_facts,
        checkpoint_predictor_facts,
        evaluate_prepared_corpus,
        load_verified_base_model_bundle,
    )
    from verifiednet.training import build_minimal_safetensors

    @_dc(frozen=True)
    class _M:
        evalctx: object
        ckptctx: object
        base_dir: object
        base_bundle: object
        base: object
        trained: object
        base_run: object
        trained_run: object
        fairness: object
        make_base: object
        make_trained: object

    _ACCEPTED = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]

    def _helper(tmp_path, *, base_responder, trained_responder):
        eval_root = tmp_path / "evalside"
        train_root = tmp_path / "trainside"
        eval_root.mkdir()
        train_root.mkdir()
        evalctx = eval_pipeline(eval_root, accepted=_ACCEPTED,
                                rejected=["run-rej"])
        ckptctx = ckpt_predictor_pipeline(train_root, accepted=_ACCEPTED,
                                          rejected=["run-rej"])
        base_dir = tmp_path / "base-model"
        base_dir.mkdir()
        (base_dir / "model.safetensors").write_bytes(build_minimal_safetensors({
            "wte.weight": ((4, 4), bytes([7]) * 64),
            "lm_head.weight": ((4, 4), bytes([9]) * 64)}))
        (base_dir / "config.json").write_text(_json.dumps(
            {"architectures": ["AutoModelForCausalLM"], "vocab_size": 4},
            sort_keys=True))
        (base_dir / "tokenizer.json").write_text(_json.dumps(
            {"version": "1.0", "model": {"type": "test-vocab"}},
            sort_keys=True))
        base_bundle = load_verified_base_model_bundle(
            base_dir, model_identifier="verifiednet-test/tiny-slm",
            model_revision="a" * 40,
            architecture_class="AutoModelForCausalLM",
            compatibility=ckptctx.compatibility)

        def make_base(responder):
            return VerifiedBaseModelPredictor(
                task=ckptctx.task, bundle=base_bundle,
                backend=FakeInferenceBackend(responder=responder),
                prompt_template=ckptctx.template,
                device_policy=ckptctx.device_policy, backend_family="fake")

        def make_trained(responder):
            return VerifiedCheckpointPredictor(
                task=ckptctx.task, bundle=ckptctx.bundle,
                backend=FakeInferenceBackend(responder=responder),
                prompt_template=ckptctx.template,
                device_policy=ckptctx.device_policy, backend_family="fake")

        base = make_base(base_responder)
        trained = make_trained(trained_responder)
        base_run = evaluate_prepared_corpus(evalctx.loaded, base, ckptctx.task)
        trained_run = evaluate_prepared_corpus(
            evalctx.loaded, trained, ckptctx.task)
        fairness = assess_matched_pair_fairness(
            base=base_model_predictor_facts(base),
            trained=checkpoint_predictor_facts(trained),
            base_run=base_run, trained_run=trained_run)
        return _M(evalctx=evalctx, ckptctx=ckptctx, base_dir=base_dir,
                  base_bundle=base_bundle, base=base, trained=trained,
                  base_run=base_run, trained_run=trained_run,
                  fairness=fairness, make_base=make_base,
                  make_trained=make_trained)

    return _helper


def expansion_run_entries(*, runs_cap: int | None = None):
    """The Gate 14 campaign's run entries: (accepted, rejected) for the chain.

    ``runs_cap`` limits runs-per-identity for FAST offline tests; the full
    campaign uses each candidate's planned run count. Selection is the
    COMPLETE matrix either way — never filtered by (predicted) partition.
    """
    from verifiednet.orchestrator.expansion import (
        GATE14_REJECTED_RUNS_PER_IDENTITY,
        GATE14_REJECTED_TARGETS,
        build_expansion_matrix,
        expansion_topology,
    )

    accepted = []
    for spec in build_expansion_matrix():
        topo = expansion_topology(spec.topology_id)
        runs = spec.planned_runs if runs_cap is None \
            else min(spec.planned_runs, runs_cap)
        for i in range(1, runs + 1):
            accepted.append((spec.case, topo,
                             f"run-{spec.topology_id}-{spec.case.case_id}-{i}"))
    rejected = []
    for topology_id in ("2r-v1", "2r-v2", "2r-v3"):
        topo = expansion_topology(topology_id)
        for target in GATE14_REJECTED_TARGETS:
            node_tag = target.removeprefix("router_")
            runs = GATE14_REJECTED_RUNS_PER_IDENTITY if runs_cap is None \
                else min(GATE14_REJECTED_RUNS_PER_IDENTITY, runs_cap)
            for i in range(1, runs + 1):
                rejected.append((f"run-rej-{topology_id}-{node_tag}-{i}",
                                 f"{topology_id}-{node_tag}", target, topo))
    return accepted, rejected


@pytest.fixture
def expansion_entries() -> Callable[..., object]:
    """The Gate 14 run-entry builder, exposed as a fixture (any conftest scope)."""
    return expansion_run_entries


@pytest.fixture
def expansion_corpus_pipeline(eval_pipeline) -> Callable[..., object]:
    """Gate 14 chain: the full (or capped) expansion campaign -> prepared corpus."""

    def _helper(tmp_path, *, runs_cap: int | None = None):
        accepted, rejected = expansion_run_entries(runs_cap=runs_cap)
        ctx = eval_pipeline(tmp_path, accepted=accepted, rejected=rejected)
        return ctx, accepted, rejected

    return _helper


def gate14b_candidate_pool():
    """The COMPLETE Gate 14B candidate pool as fully-defined identities.

    Returns ``(pool, topologies_by_hash)`` where ``topologies_by_hash`` maps
    each candidate ``topology_hash`` to ``(topology_id, TopologySpec)`` so
    run entries can be emitted for a selection.
    """
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import StableScenarioIdentity
    from verifiednet.evaluation import CandidateScenario
    from verifiednet.orchestrator.expansion import (
        build_v3_candidate_pool,
        expansion_topology,
    )

    pool = []
    topologies_by_hash = {}
    for spec in build_v3_candidate_pool():
        topo = expansion_topology(spec.topology_id)
        topology_hash = sha256_canonical(topo)
        topologies_by_hash[topology_hash] = (spec.topology_id, topo)
        params = dict(spec.case.scenario.parameters)
        pool.append(CandidateScenario(
            case_id=spec.case.case_id, fault_family=spec.fault_family,
            identity=StableScenarioIdentity(
                template_id=spec.case.scenario.template_id,
                scenario_id=spec.case.scenario.scenario_id,
                target_node=str(params.get("target_node", "")),
                target_session=str(params.get("target_session", "")),
                parameters={k: params[k] for k in sorted(params)},
                topology_hash=topology_hash, backend="frr-compose"),
            planned_runs=1))
    return tuple(pool), topologies_by_hash


def gate14b_selection(*, expansion_policy=None):
    """The Gate 14B identity-first selection over the complete pool.

    Returns ``(selection, identity_policy, expansion_policy,
    topologies_by_hash)``. Without an explicit v3 expansion policy a
    placeholder source binding is used — the SELECTED IDENTITIES and run
    counts are independent of the source corpus identity, so run entries from
    a placeholder-bound selection are exactly those of the real one.
    """
    from verifiednet.datasets.models import SplitPolicy
    from verifiednet.evaluation import (
        build_expansion_policy_v3,
        build_identity_coverage_policy,
        plan_identity_first_selection,
    )
    from verifiednet.orchestrator.expansion import (
        GATE14B_REJECTED_TARGETS,
        GATE14B_TOPOLOGY_FACTORIES,
    )

    pool, topologies_by_hash = gate14b_candidate_pool()
    policy = expansion_policy or build_expansion_policy_v3(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    selection = plan_identity_first_selection(
        pool, expansion_policy=policy, identity_policy=identity_policy,
        split_policy=SplitPolicy(salt="gate6", train_buckets=8000,
                                 validation_buckets=1000, test_buckets=1000),
        planned_rejected_identities=(len(GATE14B_TOPOLOGY_FACTORIES)
                                     * len(GATE14B_REJECTED_TARGETS)))
    return selection, identity_policy, policy, topologies_by_hash


def gate14b_run_entries(*, runs_cap: int | None = None, selection=None,
                        topologies_by_hash=None):
    """Gate 14B campaign run entries from the identity-first selection.

    ``runs_cap`` limits runs-per-identity for FAST offline tests; the full
    campaign uses each selected identity's planner-allocated run count. The
    identity SET is the selection's either way — the cap only trims
    reproducibility repeats, never identities.
    """
    from verifiednet.orchestrator.catalog import case_by_id
    from verifiednet.orchestrator.expansion import (
        GATE14B_REJECTED_RUNS_PER_IDENTITY,
        GATE14B_REJECTED_TARGETS,
        GATE14B_TOPOLOGY_FACTORIES,
        expansion_topology,
    )

    if selection is None or topologies_by_hash is None:
        selection, _identity_policy, _policy, topologies_by_hash = \
            gate14b_selection()
    accepted = []
    for entry in selection.entries:
        topology_id, topo = topologies_by_hash[
            entry.candidate.identity.topology_hash]
        runs = entry.candidate.planned_runs if runs_cap is None \
            else min(entry.candidate.planned_runs, runs_cap)
        for i in range(1, runs + 1):
            accepted.append((case_by_id(entry.candidate.case_id), topo,
                             f"run-14b-{topology_id}-"
                             f"{entry.candidate.case_id}-{i}"))
    rejected = []
    for topology_id in sorted(GATE14B_TOPOLOGY_FACTORIES):
        topo = expansion_topology(topology_id)
        for target in GATE14B_REJECTED_TARGETS:
            node_tag = target.removeprefix("router_")
            runs = GATE14B_REJECTED_RUNS_PER_IDENTITY if runs_cap is None \
                else min(GATE14B_REJECTED_RUNS_PER_IDENTITY, runs_cap)
            for i in range(1, runs + 1):
                rejected.append((f"run-14b-rej-{topology_id}-{node_tag}-{i}",
                                 f"{topology_id}-{node_tag}", target, topo))
    return accepted, rejected


@pytest.fixture
def gate14b_pool() -> Callable[..., object]:
    """The Gate 14B candidate-pool builder, exposed as a fixture."""
    return gate14b_candidate_pool


@pytest.fixture
def gate14b_entries() -> Callable[..., object]:
    """The Gate 14B run-entry builder, exposed as a fixture."""
    return gate14b_run_entries


@pytest.fixture
def gate14b_selection_builder() -> Callable[..., object]:
    """The Gate 14B selection builder, exposed as a fixture."""
    return gate14b_selection


@pytest.fixture
def gate14b_corpus_pipeline(eval_pipeline) -> Callable[..., object]:
    """Gate 14B chain: the full (or capped) v3 campaign -> prepared corpus."""

    def _helper(tmp_path, *, runs_cap: int | None = None):
        accepted, rejected = gate14b_run_entries(runs_cap=runs_cap)
        ctx = eval_pipeline(tmp_path, accepted=accepted, rejected=rejected)
        return ctx, accepted, rejected

    return _helper


#: Gate 15 offline fixture entries: two train identities, one held-out test
#: identity (2 runs), one held-out validation identity, one abstention.
def gate15_fixture_entries():
    from verifiednet.orchestrator.catalog import case_by_id
    from verifiednet.orchestrator.expansion import expansion_topology

    accepted = [
        ("ras-ref", "run-train-1"),   # v1 topology -> train
        ("nr-ref", "run-train-2"),    # v1 topology -> train
        (case_by_id("ras-ref"), expansion_topology("2r-v2"), "run-test-1"),
        (case_by_id("ras-ref"), expansion_topology("2r-v2"), "run-test-2"),
        (case_by_id("ras-rev"), expansion_topology("2r-v4"), "run-val-1"),
    ]
    return accepted, ["run-rej"]


def gate16_capped_corpora(prepared, *, max_example_count):
    """Build the v1 and v2 train-only corpora from ONE prepared corpus and
    apply the same first-N canonical cap. Returns (v1_corpus, v2_corpus).

    The ONLY intended difference between the two is the input-template
    version (Gate 16A v1 vs contract-aligned v2); eligibility, target
    template, cap, and source selection are identical — this helper is the
    shared substrate for the Gate 16B same-source and binding proofs.
    """
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.experiment import cap_training_corpus
    from verifiednet.training import (
        build_training_corpus,
        contract_aligned_input_template,
        contract_aligned_training_policy,
        diagnosis_input_template,
        diagnosis_target_template,
        diagnosis_training_policy,
    )

    task_id = diagnosis_task().task_id
    feature_policy_id = prepared.manifest.feature_policy_id
    target = diagnosis_target_template(task_id=task_id)

    v1_template = diagnosis_input_template(
        task_id=task_id, feature_policy_id=feature_policy_id)
    v1 = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=diagnosis_training_policy(
            task_id=task_id, input_template=v1_template,
            target_template=target),
        input_template=v1_template, target_template=target),
        max_example_count=max_example_count)

    v2_template = contract_aligned_input_template(
        task_id=task_id, feature_policy_id=feature_policy_id)
    v2 = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=contract_aligned_training_policy(
            task_id=task_id, input_template=v2_template,
            target_template=target),
        input_template=v2_template, target_template=target),
        max_example_count=max_example_count)
    return v1, v2


@pytest.fixture
def gate16_corpora() -> Callable[..., object]:
    """The Gate 16B v1/v2 capped-corpus builder, exposed as a fixture."""
    return gate16_capped_corpora


@pytest.fixture
def experiment_pipeline(realtrain_pipeline) -> Callable[..., object]:
    """Gate 15 offline chain: stub-trained checkpoint + four deterministic
    rule-baseline evaluations (two standing in for the matched base/trained
    model predictors) + benchmark + reliability + a preregistered, finalized
    controlled-experiment store. Entirely offline: no ML library, no network.

    ``helper(tmp_path)`` returns an object exposing every intermediate the
    Gate 15 tiers need (spec, bindings, result, store root, prepared corpus,
    runs, policies, and the underlying training context).
    """
    from dataclasses import dataclass as _dc

    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        DecodingConfig,
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        build_default_interpretation_policy,
        build_structured_output_report,
        compute_parser_statistics,
        diagnosis_prompt_template,
        diagnosis_task,
        evaluate_prepared_corpus,
        run_benchmark,
        write_structured_output_report,
    )
    from verifiednet.experiment import (
        BenchmarkBinding,
        BenchmarkRankingRow,
        CheckpointBinding,
        EvaluationBindings,
        ExperimentRuntimeEnvelope,
        PairedSummary,
        ReliabilitySummary,
        TrainingPhaseBinding,
        build_experiment_result,
        build_experiment_spec,
        build_success_policy,
        compute_family_paired_counts,
        compute_partition_paired_counts,
        extract_primary_metrics,
        preregister_experiment,
        write_experiment_result,
    )
    from verifiednet.training import (
        load_training_corpus,
        read_real_checkpoint,
        read_real_execution,
        read_training_authorization,
        read_training_plan,
    )

    @_dc(frozen=True)
    class _E:
        spec: object
        training: object
        checkpoint: object
        evaluations: object
        benchmark_binding: object
        paired: object
        reliability: object
        result: object
        written: object
        experiments_root: object
        prepared: object
        base_run: object
        trained_run: object
        benchmark: object
        success_policy: object
        trainctx: object

    def _helper(tmp_path):
        from verifiednet.datasets.models import DatasetPartition
        from verifiednet.datasets.verifier import DatasetCheck

        accepted, rejected = gate15_fixture_entries()
        trainctx = realtrain_pipeline(tmp_path, accepted=accepted,
                                      rejected=rejected)
        written_exec = trainctx.execute()
        assert written_exec.final_state.value == "completed"
        loaded_exec = read_real_execution(written_exec.root)
        checkpoint_dir = (trainctx.output_root / "real-checkpoints"
                          / written_exec.checkpoint_id)
        ckpt_manifest = read_real_checkpoint(checkpoint_dir).manifest

        plan = read_training_plan(trainctx.plan_dir)
        corpus_manifest = load_training_corpus(trainctx.corpus_root).manifest
        auth = read_training_authorization(trainctx.auth_dir)
        prepared = load_prepared(trainctx.prectx.planctx.prepared_dir)

        task = diagnosis_task()
        fixed = FixedPriorBaseline(
            task=task, fixed_fault_family="bgp_remote_as_mismatch")
        rule = EvidenceRuleBaseline(
            task=task, default_fault_family="bgp_remote_as_mismatch")
        base_stand_in = FixedPriorBaseline(
            task=task, fixed_fault_family="bgp_neighbor_removal")
        trained_stand_in = EvidenceRuleBaseline(
            task=task, default_fault_family="bgp_neighbor_removal")
        base_run = evaluate_prepared_corpus(prepared, base_stand_in, task)
        trained_run = evaluate_prepared_corpus(
            prepared, trained_stand_in, task)
        benchmark = run_benchmark(
            prepared, task=task,
            predictors=[fixed, rule, base_stand_in, trained_stand_in])
        report = build_structured_output_report(benchmark)
        written_report = write_structured_output_report(
            report, tmp_path / "structured-reports")

        success_policy = build_success_policy(min_eligible_test_examples=2)
        spec = build_experiment_spec(
            experiment_name="gate15-offline-fixture",
            experiment_version=1,
            scientific_question="does the stub chain hold structurally?",
            hypothesis="the offline chain is structurally sound",
            evaluation_corpus_id="evalcorpus-" + "0" * 16,
            evaluation_corpus_digest="ecdig-" + "0" * 24,
            readiness_assessment_id="ready-" + "0" * 16,
            source_prepared_digest=prepared.manifest.prepared_digest,
            training_corpus_policy_id=(
                corpus_manifest.training_data_policy.training_data_policy_id),
            training_corpus_id=corpus_manifest.training_corpus_id,
            training_corpus_digest=corpus_manifest.training_corpus_digest,
            eligible_train_examples=corpus_manifest.example_count,
            training_example_cap=corpus_manifest.example_count,
            cap_rationale="offline fixture: the full corpus fits the "
                          "envelope",
            model_approval_id="modelappr-" + "0" * 16,
            model_artifact_id=(
                auth.authorization.model_artifact.resolved_model_artifact_id),
            tokenizer_artifact_id=(
                auth.authorization.tokenizer_artifact
                .resolved_tokenizer_artifact_id),
            model_identifier="verifiednet-test/tiny-slm",
            model_revision="b" * 40,
            tokenizer_revision="b" * 40,
            training_spec_id=plan.plan.request.spec.training_spec_id,
            training_plan_id=plan.plan.training_plan_id,
            training_plan_digest=plan.manifest.plan_digest,
            bounded_model_policy_id=(
                trainctx.model_policy.bounded_model_policy_id),
            objective_policy_id=(
                trainctx.objective_policy.objective_policy_id),
            runtime_envelope=ExperimentRuntimeEnvelope(
                max_examples=16, max_epochs=4, max_optimizer_steps=16,
                max_sequence_length=1024, max_effective_batch_size=4),
            prompt_template_id=diagnosis_prompt_template().prompt_template_id,
            decoding=DecodingConfig(max_tokens=64),
            normalization_policy_id=task.normalization.policy_id,
            scoring_policy_version=task.scoring_policy_version,
            interpretation_policy_id=(
                build_default_interpretation_policy()
                .interpretation_policy_id),
            success_policy=success_policy)
        experiments_root = tmp_path / "controlled-experiments"
        preregister_experiment(spec, experiments_root)

        losses = loaded_exec.result.observed_losses
        training = TrainingPhaseBinding(
            experiment_id=spec.experiment_id,
            training_corpus_id=corpus_manifest.training_corpus_id,
            training_corpus_digest=corpus_manifest.training_corpus_digest,
            corpus_slice_id=trainctx.slice_policy.corpus_slice_id,
            training_spec_id=plan.plan.request.spec.training_spec_id,
            training_plan_id=plan.plan.training_plan_id,
            training_plan_digest=plan.manifest.plan_digest,
            authorization_id=auth.authorization.authorization_id,
            authorization_digest=auth.manifest.authorization_digest,
            bounded_model_policy_id=(
                trainctx.model_policy.bounded_model_policy_id),
            objective_policy_id=(
                trainctx.objective_policy.objective_policy_id),
            real_execution_policy_id=(
                trainctx.execution_policy.real_execution_policy_id),
            model_approval_id="modelappr-" + "0" * 16,
            execution_id=written_exec.execution_id,
            execution_digest=written_exec.execution_digest,
            completed_optimizer_steps=(
                loaded_exec.result.completed_optimizer_steps),
            completed_epochs=loaded_exec.result.completed_epochs,
            observed_loss_count=len(losses),
            first_observed_loss=losses[0], last_observed_loss=losses[-1])
        lineage = ckpt_manifest.lineage
        checkpoint = CheckpointBinding(
            experiment_id=spec.experiment_id,
            checkpoint_id=ckpt_manifest.checkpoint_id,
            checkpoint_digest=ckpt_manifest.checkpoint_digest,
            lineage_id=lineage.lineage_id,
            real_execution_id=lineage.real_execution_id,
            training_plan_id=lineage.training_plan_id,
            training_corpus_id=lineage.training_corpus_id,
            lineage_checks=(
                DatasetCheck(rule="execution_matches",
                             passed=lineage.real_execution_id
                             == written_exec.execution_id, detail=""),
                DatasetCheck(rule="plan_matches",
                             passed=lineage.training_plan_id
                             == plan.plan.training_plan_id, detail=""),
                DatasetCheck(rule="corpus_matches",
                             passed=lineage.training_corpus_id
                             == corpus_manifest.training_corpus_id,
                             detail="")))
        evaluations = EvaluationBindings(
            experiment_id=spec.experiment_id,
            fixed_prior_evaluation_id=next(
                r.evaluation_id for r in benchmark.evaluation_runs
                if r.baseline_spec.baseline_id == fixed.spec.baseline_id),
            evidence_rule_evaluation_id=next(
                r.evaluation_id for r in benchmark.evaluation_runs
                if r.baseline_spec.baseline_id == rule.spec.baseline_id),
            base_baseline_id=base_stand_in.spec.baseline_id,
            base_evaluation_id=base_run.evaluation_id,
            base_evaluation_digest="evdig-" + "0" * 24,
            trained_baseline_id=trained_stand_in.spec.baseline_id,
            trained_evaluation_id=trained_run.evaluation_id,
            trained_evaluation_digest="evdig-" + "1" * 24)
        benchmark_binding = BenchmarkBinding(
            experiment_id=spec.experiment_id,
            benchmark_id=benchmark.spec.benchmark_id,
            benchmark_digest="benchdig-" + "0" * 24,
            ranking=tuple(
                BenchmarkRankingRow(
                    predictor_identifier=entry.predictor_identifier,
                    rank=entry.rank)
                for entry in benchmark.ranking))
        paired = PairedSummary(
            experiment_id=spec.experiment_id,
            comparison_id="cmp-" + "0" * 16,
            comparison_digest="cmpdig-" + "0" * 24,
            interpretation_conclusion="engineering_fixture",
            counts_all=compute_partition_paired_counts(
                base_run, trained_run, partitions=None),
            counts_non_train=compute_partition_paired_counts(
                base_run, trained_run,
                partitions=(DatasetPartition.VALIDATION,
                            DatasetPartition.TEST,
                            DatasetPartition.ABSTENTION)),
            counts_test=compute_partition_paired_counts(
                base_run, trained_run,
                partitions=(DatasetPartition.TEST,)),
            family_test_counts=compute_family_paired_counts(
                base_run, trained_run, partition=DatasetPartition.TEST))
        reliability = ReliabilitySummary(
            experiment_id=spec.experiment_id,
            report_id=written_report.report_id,
            report_digest=written_report.report_digest,
            base=compute_parser_statistics(base_run),
            trained=compute_parser_statistics(trained_run))
        metrics = extract_primary_metrics(
            base_run, trained_run, comparison_unconfounded=True)
        result = build_experiment_result(
            spec=spec, training=training, checkpoint=checkpoint,
            evaluations=evaluations, benchmark=benchmark_binding,
            paired=paired, reliability=reliability, metrics=metrics)
        written = write_experiment_result(
            spec=spec, training=training, checkpoint=checkpoint,
            evaluations=evaluations, benchmark=benchmark_binding,
            paired=paired, reliability=reliability, result=result,
            experiments_root=experiments_root)
        return _E(spec=spec, training=training, checkpoint=checkpoint,
                  evaluations=evaluations,
                  benchmark_binding=benchmark_binding, paired=paired,
                  reliability=reliability, result=result, written=written,
                  experiments_root=experiments_root, prepared=prepared,
                  base_run=base_run, trained_run=trained_run,
                  benchmark=benchmark, success_policy=success_policy,
                  trainctx=trainctx)

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


def catalog_node_facts(topology) -> dict:
    """Derive the simulator's node facts from a TopologySpec (Gate 14).

    Keeps the simulator honest for topology VARIANTS: the same object that the
    orchestrator validates and hashes also drives the simulated evidence."""
    by_name = {n.name: n for n in topology.nodes}
    session = topology.sessions[0]
    endpoints = {session.a.node: session.a, session.b.node: session.b}
    names = sorted(by_name)
    facts: dict = {}
    for name, node in by_name.items():
        endpoint = endpoints[name]
        peer = next(n for n in names if n != name)
        facts[name] = {"asn": node.asn, "loopback": node.loopback,
                       "peer_ip": endpoint.peer_ip,
                       "correct_remote_as": endpoint.remote_as,
                       "peer": peer, "peer_loopback": by_name[peer].loopback}
    return facts


class _CatNodeState:
    def __init__(self, name: str, facts: dict) -> None:
        self.name = name
        self.remote_as = int(facts[name]["correct_remote_as"])
        self.has_neighbor = True
        self.eth1_up = True
        self.advertised = True


class CatalogLabSim:
    """Symmetric Docker+FRR simulator: both routers carry independent state."""

    def __init__(self, facts: dict | None = None) -> None:
        self._facts = facts or _CATALOG_NODES
        self.a = _CatNodeState("router_a", self._facts)
        self.b = _CatNodeState("router_b", self._facts)
        self._up = False
        self.mutation_targets: list[str] = []

    def _state(self, node: str):
        return self.a if node == "router_a" else self.b

    @property
    def _session_up(self) -> bool:
        return (
            self.a.eth1_up and self.b.eth1_up
            and self.a.has_neighbor and self.b.has_neighbor
            and self.a.remote_as == self._facts["router_a"]["correct_remote_as"]
            and self.b.remote_as == self._facts["router_b"]["correct_remote_as"]
        )

    def _ps(self) -> str:
        if not self._up:
            return ""
        return "cid_a\trouter_a\trunning\ncid_b\trouter_b\trunning"

    def _bgp_summary(self, node: str) -> str:
        st = self._state(node)
        facts = self._facts[node]
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
        facts = self._facts[node]
        peer = self._state(facts["peer"])
        table = {facts["loopback"]: [{"protocol": "connected"}]}
        if self._session_up and peer.advertised:
            table[facts["peer_loopback"]] = [{"protocol": "bgp"}]
        return _json2.dumps(table)

    def _running_config(self, node: str) -> str:
        st = self._state(node)
        facts = self._facts[node]
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
            peer = self._state(self._facts[node]["peer"])
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


def run_catalog_case_offline(case, out_root, tmp_path, run_id=None, sim=None,
                             topology=None):
    """Run one ScenarioCase through run_accepted_case with a fresh CatalogLabSim.

    Gate 14: an explicit ``topology`` runs the case in that network context
    (the simulator derives its facts from the SAME spec the run validates and
    hashes); the default remains the approved v1 two-router topology."""
    from verifiednet.orchestrator import run_accepted_case

    rid = run_id or f"run-{case.case_id}"
    topo = topology or two_router_frr_topology()

    class _Clk:
        def __init__(self): self.t = 0.0
        def monotonic(self): return self.t
        def sleep(self, s): self.t += s

    clk = _Clk()
    return run_accepted_case(
        case=case, out_root=out_root, work_dir=tmp_path / rid,
        run_ctx=RunContext(rid, clock=_catalog_epoch),
        topology=topo,
        git_rev=CATALOG_GIT_REV, lock_hash=CATALOG_LOCK_HASH,
        runner=sim or CatalogLabSim(facts=catalog_node_facts(topo)),
        monotonic=clk.monotonic, sleep=clk.sleep,
        convergence_timeout_s=5.0,
    )


@pytest.fixture
def catalog_sim_cls():
    return CatalogLabSim


@pytest.fixture
def run_catalog_case():
    return run_catalog_case_offline


@pytest.fixture
def balanced_prepared():
    """Build a synthetic LoadedPrepared with configurable per-family TRAIN counts
    for Gate 19A source-selection tests. Objects are fully valid; only the
    trace + accepted labels the selector reads are meaningful. No evidence
    resolution is exercised (that lives in the gated real-chain proof)."""
    from verifiednet.datasets.features import (
        AcceptedLabels,
        DatasetFeatures,
        DatasetTraceMetadata,
        FeatureEvidenceRef,
        SeparatedDatasetExample,
    )
    from verifiednet.datasets.models import (
        ArtifactReference,
        DatasetExampleKind,
        DatasetPartition,
    )
    from verifiednet.datasets.prepared import LoadedPrepared, PreparedManifest

    def _ref(path: str) -> ArtifactReference:
        return ArtifactReference(run_id="run-x", relative_path=path)

    def _ex(example_id, group_id, family, *,
            partition=DatasetPartition.TRAIN,
            kind=DatasetExampleKind.ACCEPTED_FAULT):
        feats = DatasetFeatures(
            feature_policy_id="feat-x", topology_hash="a" * 64, backend="frr",
            baseline_evidence=FeatureEvidenceRef(relative_path="evidence/baseline.json"),
            onset_evidence=FeatureEvidenceRef(relative_path="evidence/onset.json"))
        labels = AcceptedLabels(
            label_policy_id="label-x", fault_family=family,
            scenario_id="scn-" + example_id,
            ground_truth_reference=_ref("gt.json"),
            recovery_reference=_ref("rec.json"))
        trace = DatasetTraceMetadata(
            example_id=example_id, group_id=group_id, run_id="run-x",
            run_digest="rd", example_kind=kind, partition=partition,
            split_policy_id="split-x", dataset_version="ds-1",
            source_index_digest="idx", example_schema_version=1,
            incident_reference=_ref("inc.json"))
        return SeparatedDatasetExample(features=feats, labels=labels, trace=trace)

    def build(counts, *, dataset_version="ds-1",
              prepared_digest="prep-" + "0" * 24, extra=()):
        examples = []
        i = 0
        for fam in sorted(counts):
            for _ in range(counts[fam]):
                i += 1
                examples.append(_ex(f"ex-{i:04d}", f"grp-{i:04d}", fam))
        examples.extend(extra)
        examples.sort(key=lambda e: e.trace.example_id)
        manifest = PreparedManifest.model_construct(
            prepared_digest=prepared_digest, dataset_version=dataset_version)
        return LoadedPrepared(
            manifest=manifest, examples=tuple(examples), by_partition={})

    build.example = _ex  # expose the factory for failure-case construction
    return build


@pytest.fixture
def remoteas_pool():
    """Build a synthetic pool of distinct remote-AS candidate identities for
    Gate 20A offline tests. Distinct parameters yield distinct production
    group_ids (~80% TRAIN under the gate6 split), so >= 8 TRAIN candidates exist
    without touching the live catalog (which the gated proof reads)."""
    from verifiednet.experiment.remoteas_expansion import (
        APPROVED_REMOTEAS_CASE_IDS,
        APPROVED_TOPOLOGY_IDS,
        RemoteAsCandidate,
        remoteas_identity,
    )

    def build(n=40, backend="frr-compose"):
        pool = []
        for i in range(n):
            ident = remoteas_identity(
                scenario_id=f"bgp-remote-as-mismatch-syn-{i}",
                target_node="router_a", target_session="a-b",
                parameters={"wrong_asn": 65000 + i},
                topology_hash=f"{i % 6}" + "a" * 63, backend=backend)
            pool.append(RemoteAsCandidate(
                case_id=APPROVED_REMOTEAS_CASE_IDS[i % 10],
                topology_id=APPROVED_TOPOLOGY_IDS[i % 6], identity=ident))
        return tuple(pool)

    return build


@pytest.fixture
def remoteas_campaign(remoteas_pool):
    """Build a Gate 20B campaign context (spec, inventory, plan) plus a factory for
    executed ``RemoteAsRunRecord`` tuples, from the synthetic remote-AS pool. No
    live catalog, no run, no dataset — only the offline campaign-result contracts."""
    from verifiednet.datasets.models import SplitPolicy
    from verifiednet.experiment.remoteas_campaign import RemoteAsRunRecord
    from verifiednet.experiment.remoteas_expansion import (
        FrozenGroup as _FrozenGroup,
    )
    from verifiednet.experiment.remoteas_expansion import (
        build_campaign_plan,
        build_frozen_inventory,
        plan_remoteas_expansion,
        remoteas_expansion_spec,
    )

    pol = SplitPolicy(salt="gate6", train_buckets=8000,
                      validation_buckets=1000, test_buckets=1000)

    class _Ctx:
        pass

    def build(*, retry_allowance=2, pool_n=40):
        frozen = build_frozen_inventory(
            "prep-" + "0" * 24,
            (_FrozenGroup(group_id="grp-frozenplaceholder", fault_family="x",
                          partition="test", example_count=3),))
        spec = remoteas_expansion_spec()
        inventory = plan_remoteas_expansion(spec, remoteas_pool(pool_n), frozen,
                                            split_policy=pol)
        plan = build_campaign_plan(spec, inventory, retry_allowance=retry_allowance)
        ctx = _Ctx()
        ctx.spec, ctx.inventory, ctx.plan, ctx.frozen = spec, inventory, plan, frozen
        return ctx

    def record(expected, *, attempt=1, accepted=True, verified=None,
               observed_group_id=None, failure_category="", run_suffix=""):
        verified = accepted if verified is None else verified
        observed = (expected.group_id if observed_group_id is None
                    else observed_group_id)
        rid = f"run-{expected.group_id[4:12]}-a{attempt}{run_suffix}"
        return RemoteAsRunRecord(
            planned_group_id=expected.group_id, case_id=expected.case_id,
            topology_id=expected.topology_id, attempt=attempt, run_id=rid,
            run_digest="rundig-" + rid, observed_group_id=observed,
            verified=verified, accepted=accepted, failure_category=failure_category)

    def accepted_records(inventory):
        """Two accepted verified runs per planned group -> full 8x2 coverage. Both
        are first attempts of their own planned slot (attempt=1); a genuine retry
        (attempt>=2) re-runs a *failed* slot and is what ``retry_count`` measures."""
        recs = []
        for e in inventory.expected:
            recs.append(record(e, attempt=1, run_suffix="s1"))
            recs.append(record(e, attempt=1, run_suffix="s2"))
        return tuple(recs)

    build.record = record
    build.accepted_records = accepted_records
    return build


@pytest.fixture
def remoteas_prepared_pair(balanced_prepared):
    """Build an append-only (v3, v4) prepared pair for Gate 20B diff tests: v4 is
    v3 with N new TRAIN accepted remote-AS examples (new group_ids) appended, every
    shared row byte-identical. Returns ``(v3, v4, frozen_remoteas_group_ids)``."""
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.datasets.prepared import LoadedPrepared, PreparedManifest

    ex = balanced_prepared.example

    def build(*, added=16, added_groups=8, mutate=None, drop_v3=False,
              repartition=False, collide_group=None):
        v3_examples = [
            ex("ex-v3-0001", "grp-ras-frozen-a", "bgp_remote_as_mismatch",
               partition=DatasetPartition.TRAIN),
            ex("ex-v3-0002", "grp-ras-val-b", "bgp_remote_as_mismatch",
               partition=DatasetPartition.VALIDATION),
            ex("ex-v3-0003", "grp-ras-test-c", "bgp_remote_as_mismatch",
               partition=DatasetPartition.TEST),
            ex("ex-v3-0004", "grp-iface-d", "iface_admin_shutdown",
               partition=DatasetPartition.TRAIN),
        ]
        frozen_ras = frozenset(
            {"grp-ras-frozen-a", "grp-ras-val-b", "grp-ras-test-c"})
        # v4 starts as a byte-identical copy of every v3 row.
        v4_examples = list(v3_examples)
        for g in range(added_groups):
            gid = (collide_group if (collide_group and g == 0)
                   else f"grp-ras-new-{g:02d}")
            for r in range(max(1, added // added_groups)):
                v4_examples.append(
                    ex(f"ex-v4-new-{g:02d}-{r}", gid, "bgp_remote_as_mismatch",
                       partition=DatasetPartition.TRAIN))
        if mutate is not None:
            # replace the shared v3 row with a byte-different version in v4 (a
            # different fault_family guarantees the model_dump bytes differ)
            idx = next(i for i, e in enumerate(v4_examples)
                       if e.trace.example_id == mutate)
            orig = v4_examples[idx]
            new_family = ("bgp_neighbor_removal"
                          if orig.labels.fault_family != "bgp_neighbor_removal"
                          else "bgp_prefix_withdrawal")
            v4_examples[idx] = ex(orig.trace.example_id, orig.trace.group_id,
                                  new_family, partition=orig.trace.partition)
        if repartition:
            idx = next(i for i, e in enumerate(v4_examples)
                       if e.trace.example_id == "ex-v3-0002")
            orig = v4_examples[idx]
            v4_examples[idx] = ex(orig.trace.example_id, orig.trace.group_id,
                                  orig.labels.fault_family,
                                  partition=DatasetPartition.TRAIN)
        if drop_v3:
            v4_examples = [e for e in v4_examples
                           if e.trace.example_id != "ex-v3-0004"]

        def _loaded(examples, digest):
            ordered = tuple(sorted(examples, key=lambda e: e.trace.example_id))
            manifest = PreparedManifest.model_construct(
                prepared_digest=digest, dataset_version="ds-1")
            return LoadedPrepared(manifest=manifest, examples=ordered,
                                  by_partition={})

        v3 = _loaded(v3_examples, "prep-v3-" + "0" * 20)
        v4 = _loaded(v4_examples, "prep-v4-" + "0" * 20)
        return v3, v4, frozen_ras

    return build


@pytest.fixture
def coverage_prepared(balanced_prepared):
    """Build a synthetic v4-like LoadedPrepared for Gate 20C group-aware selection
    tests: the three abundant families each span 10 independent TRAIN groups (4
    examples each), and bgp_remote_as_mismatch spans 9 independent TRAIN groups
    (one legacy group of 4 + eight new groups of 2 = 20 examples), mirroring the
    real v4 remote-AS coverage. Also seeds a few held-out rows. Configurable."""
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.datasets.prepared import LoadedPrepared, PreparedManifest

    ex = balanced_prepared.example
    ABUNDANT = ("bgp_neighbor_removal", "bgp_prefix_withdrawal",
                "iface_admin_shutdown")

    def build(*, ras_groups=((4,), tuple([2] * 8)),
              abundant_groups=10, abundant_per_group=4,
              dataset_version="v4-remoteas-expansion",
              prepared_digest="prep-cov-" + "0" * 16):
        rows = []
        n = 0
        for fam in ABUNDANT:
            fam_tag = fam.split("_")[0][:3]
            for g in range(abundant_groups):
                for _ in range(abundant_per_group):
                    n += 1
                    rows.append(ex(f"ex-{fam_tag}-{n:04d}", f"grp-{fam_tag}-{g:02d}",
                                   fam, partition=DatasetPartition.TRAIN))
        # remote-AS: ras_groups is (legacy_sizes, new_sizes) tuples of per-group counts
        legacy_sizes, new_sizes = ras_groups
        gi = 0
        for size in (*legacy_sizes, *new_sizes):
            for _ in range(size):
                n += 1
                rows.append(ex(f"ex-ras-{n:04d}", f"grp-ras-{gi:02d}",
                               "bgp_remote_as_mismatch",
                               partition=DatasetPartition.TRAIN))
            gi += 1
        # a couple of held-out rows (not TRAIN) that selection must ignore
        rows.append(ex("ex-ras-val-1", "grp-ras-val", "bgp_remote_as_mismatch",
                       partition=DatasetPartition.VALIDATION))
        rows.append(ex("ex-ras-test-1", "grp-ras-test", "bgp_remote_as_mismatch",
                       partition=DatasetPartition.TEST))
        rows.sort(key=lambda e: e.trace.example_id)
        manifest = PreparedManifest.model_construct(
            prepared_digest=prepared_digest, dataset_version=dataset_version)
        return LoadedPrepared(manifest=manifest, examples=tuple(rows),
                              by_partition={})

    build.example = ex
    return build
