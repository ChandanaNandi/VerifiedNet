"""Integrity + cross-link verification for a canonical run directory.

Returns a structured :class:`ArtifactVerificationResult` (never just a bool).
Pure and offline: it reads files, recomputes hashes, re-serializes canonical
bytes, and checks structural consistency. It never executes commands, contacts
Docker, or mutates the directory.

Evidence-linkage note (honest): an accepted run's verification *verdicts* are
computed over transient per-poll evidence, so their evidence ids are not the
persisted bundles. The authoritative, verified linkage is
``GroundTruth.accepted_evidence_ids``, which references the persisted per-phase
bundles. A precondition-rejected run's single verdict IS computed over the
persisted baseline, so its evidence id is checked to resolve there.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path

from pydantic import BaseModel

from verifiednet.artifacts.layout import (
    HASH_INDEX_FILE,
    INCOMPLETE_MARKER,
    JSONL_ROLES,
    LAYOUT_FILE,
    VERIFICATION_REPORT_FILE,
    ArtifactHash,
    ArtifactHashIndex,
    ArtifactRole,
    ArtifactVerificationResult,
    CheckOutcome,
    RunLayout,
    is_safe_relative_path,
)
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.common.runctx import utc_now
from verifiednet.faults.ledger import LEGAL_TRANSITIONS, LedgerRecord, LifecyclePhase
from verifiednet.runtime.transcript import TranscriptEntry
from verifiednet.schemas.evidence import EvidenceBundle
from verifiednet.schemas.incident import IncidentRecord
from verifiednet.schemas.manifests import EnvironmentManifest, RunManifest

# Env-dump command guard: recorded argv must never look like an environment dump.
_ENV_DUMP_TOKENS = frozenset({"env", "printenv", "set", "export"})
_SECRETISH = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "APIKEY", "API_KEY", "PRIVATE_KEY")


class ArtifactIntegrityError(VerifiedNetError):
    """A run directory failed structural integrity verification."""


def compute_run_digest(entries: Iterable[ArtifactHash]) -> str:
    """Deterministic whole-run digest over the truth-bearing files.

    Digest = ``sha256_canonical`` of the path-sorted list of
    ``{path, sha256, size, role}``. Excludes ``hashes.json`` and
    ``verification_report.json`` (they are META, never in ``entries``), so there
    is no recursive self-hashing.
    """
    items = sorted(
        (
            {"path": e.relative_path, "sha256": e.sha256, "size": e.size, "role": e.role.value}
            for e in entries
        ),
        key=lambda d: str(d["path"]),
    )
    return sha256_canonical(items)


def _c(rule: str, passed: bool, detail: str = "") -> CheckOutcome:
    return CheckOutcome(rule=rule, passed=passed, detail=detail)


def _iter_regular_files(root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in sorted(root.rglob("*")) if p.is_file()]


def _rel_for_role(
    indexed: dict[str, ArtifactHash], role: ArtifactRole
) -> str | None:
    for rel, entry in indexed.items():
        if entry.role is role:
            return rel
    return None


def verify_run_dir(
    root: str | Path, *, allow_incomplete_marker: bool = False
) -> ArtifactVerificationResult:
    """Verify a run directory; return a structured result (never raises on tamper)."""
    root = Path(root)
    checks: list[CheckOutcome] = []
    run_id_from_dir = root.name

    layout: RunLayout | None = None
    index: ArtifactHashIndex | None = None
    try:
        layout = RunLayout.model_validate_json((root / LAYOUT_FILE).read_bytes())
        checks.append(_c("layout_parses", True))
    except (OSError, ValueError) as exc:
        checks.append(_c("layout_parses", False, str(exc)))
    try:
        index = ArtifactHashIndex.model_validate_json((root / HASH_INDEX_FILE).read_bytes())
        checks.append(_c("hash_index_parses", True))
    except (OSError, ValueError) as exc:
        checks.append(_c("hash_index_parses", False, str(exc)))

    if layout is None or index is None:
        return _finish(run_id_from_dir, "", checks)

    checks.append(_c("dir_name_equals_run_id", run_id_from_dir == index.run_id,
                     f"dir={run_id_from_dir!r} index={index.run_id!r}"))
    checks.append(_c("layout_run_id_matches_index", layout.run_id == index.run_id))

    marker = (root / INCOMPLETE_MARKER).exists()
    if not allow_incomplete_marker:
        checks.append(_c("no_incomplete_marker", not marker, "the .INCOMPLETE marker is present"))

    indexed = {e.relative_path: e for e in index.entries}
    allowed_meta = {HASH_INDEX_FILE, VERIFICATION_REPORT_FILE}
    if allow_incomplete_marker:
        allowed_meta |= {INCOMPLETE_MARKER}
    unindexed = [f for f in _iter_regular_files(root) if f not in indexed and f not in allowed_meta]
    checks.append(_c("no_unindexed_files", not unindexed, f"unindexed={unindexed!r}"))

    for rel, entry in indexed.items():
        fp = root / rel
        if not is_safe_relative_path(rel):
            checks.append(_c(f"safe_path:{rel}", False, "unsafe artifact path"))
        if not fp.is_file():
            checks.append(_c(f"file_present:{rel}", False, "indexed file missing"))
            continue
        data = fp.read_bytes()
        actual = sha256_bytes(data)
        checks.append(_c(f"hash_matches:{rel}", actual == entry.sha256,
                         f"expected={entry.sha256} actual={actual}"))
        checks.append(_c(f"size_matches:{rel}", len(data) == entry.size))

    recomputed = compute_run_digest(index.entries)
    checks.append(_c("run_digest_matches", recomputed == index.run_digest,
                     f"index={index.run_digest} recomputed={recomputed}"))

    incident = _load_json(root, ArtifactRole.INCIDENT, indexed, IncidentRecord, checks)
    run_manifest = _load_json(root, ArtifactRole.RUN_MANIFEST, indexed, RunManifest, checks)
    _load_json(root, ArtifactRole.ENVIRONMENT_MANIFEST, indexed, EnvironmentManifest, checks)
    transcript = _load_jsonl(root, ArtifactRole.TRANSCRIPT, indexed, TranscriptEntry, checks)
    ledger = _load_jsonl(root, ArtifactRole.LEDGER, indexed, LedgerRecord, checks)
    evidence = _load_evidence(root, indexed, checks)

    if incident is not None and run_manifest is not None:
        _check_incident_manifest(incident, run_manifest, run_id_from_dir, checks)
    if incident is not None:
        _check_incident_evidence(incident, evidence, checks)
        _check_no_host_path_leak(root, indexed, checks)
    _check_transcript(incident, transcript, checks)
    _check_ledger(incident, ledger, checks)

    return _finish(index.run_id, index.run_digest, checks)


def _finish(run_id: str, run_digest: str, checks: list[CheckOutcome]) -> ArtifactVerificationResult:
    return ArtifactVerificationResult(
        run_id=run_id or "unknown",
        verified=all(c.passed for c in checks),
        run_digest=run_digest or ("0" * 64),
        checks=tuple(checks),
        verified_at=utc_now(),
    )


def _load_json[M: BaseModel](
    root: Path,
    role: ArtifactRole,
    indexed: dict[str, ArtifactHash],
    model: type[M],
    checks: list[CheckOutcome],
) -> M | None:
    rel = _rel_for_role(indexed, role)
    if rel is None:
        return None
    try:
        raw = (root / rel).read_bytes()
        obj = model.model_validate_json(raw)
        checks.append(_c(f"canonical_bytes:{rel}", canonical_json_bytes(obj) == raw,
                         "stored bytes are not canonical / do not round-trip"))
        return obj
    except (OSError, ValueError) as exc:
        checks.append(_c(f"loads:{rel}", False, str(exc)))
        return None


def _load_jsonl[M: BaseModel](
    root: Path,
    role: ArtifactRole,
    indexed: dict[str, ArtifactHash],
    model: type[M],
    checks: list[CheckOutcome],
) -> list[M] | None:
    rel = _rel_for_role(indexed, role)
    if rel is None:
        return None
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        checks.append(_c(f"loads:{rel}", False, str(exc)))
        return None
    items: list[M] = []
    ok = True
    for i, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            obj = model.model_validate_json(line)
        except ValueError as exc:
            checks.append(_c(f"jsonl_line:{rel}:{i}", False, str(exc)))
            ok = False
            continue
        if canonical_json_bytes(obj) != line.encode("utf-8"):
            ok = False
        items.append(obj)
    checks.append(_c(f"jsonl_canonical:{rel}", ok))
    return items


def _load_evidence(
    root: Path, indexed: dict[str, ArtifactHash], checks: list[CheckOutcome]
) -> dict[ArtifactRole, EvidenceBundle]:
    out: dict[ArtifactRole, EvidenceBundle] = {}
    for role in (
        ArtifactRole.EVIDENCE_BASELINE,
        ArtifactRole.EVIDENCE_ONSET,
        ArtifactRole.EVIDENCE_RECOVERY,
    ):
        if _rel_for_role(indexed, role) is None:
            continue
        bundle = _load_json(root, role, indexed, EvidenceBundle, checks)
        if bundle is not None:
            checks.append(_c(f"evidence_sealed:{role.value}", bundle.sealed))
            out[role] = bundle
    return out


def _check_incident_manifest(
    incident: IncidentRecord,
    run_manifest: RunManifest,
    dir_name: str,
    checks: list[CheckOutcome],
) -> None:
    checks.append(_c("scenario_id_matches",
                     incident.scenario.scenario_id == run_manifest.scenario_id))
    checks.append(_c("template_id_matches",
                     incident.scenario.template_id == run_manifest.template_id))
    topo_hash = sha256_canonical(incident.topology)
    checks.append(_c("topology_hash_matches",
                     topo_hash == incident.topology_hash == run_manifest.topology_hash))
    checks.append(_c("run_id_matches_dir", incident.run_id == dir_name))
    fault_payload = None if incident.fault is None else incident.fault.model_dump(mode="json")
    reconstructed = "inc-" + sha256_canonical(
        {"scenario": incident.scenario.scenario_id, "fault": fault_payload,
         "topology_hash": incident.topology_hash}
    )[:16]
    checks.append(_c("incident_id_reconstructs", reconstructed == incident.incident_id,
                     f"reconstructed={reconstructed} stored={incident.incident_id}"))
    checks.append(_c("acceptance_status_matches",
                     run_manifest.acceptance_status == incident.status))


def _check_incident_evidence(
    incident: IncidentRecord,
    evidence: dict[ArtifactRole, EvidenceBundle],
    checks: list[CheckOutcome],
) -> None:
    if incident.status == "accepted":
        checks.append(_c("accepted_has_ground_truth", incident.ground_truth is not None))
        checks.append(_c("accepted_has_baseline", ArtifactRole.EVIDENCE_BASELINE in evidence))
        checks.append(_c("accepted_has_onset", ArtifactRole.EVIDENCE_ONSET in evidence))
        checks.append(_c("accepted_has_recovery", ArtifactRole.EVIDENCE_RECOVERY in evidence))
        if incident.ground_truth is not None:
            written = {r.evidence_id for b in evidence.values() for r in b.records}
            missing = [e for e in incident.ground_truth.accepted_evidence_ids if e not in written]
            checks.append(_c("ground_truth_evidence_resolves", not missing, f"missing={missing!r}"))
    else:
        checks.append(_c("rejected_has_no_ground_truth", incident.ground_truth is None))
        checks.append(_c("rejected_has_baseline_only",
                         set(evidence) == {ArtifactRole.EVIDENCE_BASELINE}))
        base = evidence.get(ArtifactRole.EVIDENCE_BASELINE)
        if base is not None:
            written = {r.evidence_id for r in base.records}
            ids = [e for r in incident.precondition_results for e in r.evidence_ids]
            missing = [e for e in ids if e not in written]
            checks.append(_c("rejected_precondition_evidence_resolves", not missing,
                             f"missing={missing!r}"))


def _check_no_host_path_leak(
    root: Path, indexed: dict[str, ArtifactHash], checks: list[CheckOutcome]
) -> None:
    abs_root = str(root.resolve())
    leaks: list[str] = []
    for rel, entry in indexed.items():
        if entry.role in JSONL_ROLES:
            continue  # transcript legitimately records the lab's compose-file path
        try:
            if abs_root in (root / rel).read_text(encoding="utf-8"):
                leaks.append(rel)
        except OSError:
            leaks.append(rel)
    checks.append(_c("no_run_dir_path_leak", not leaks, f"leaks={leaks!r}"))


def _check_transcript(
    incident: IncidentRecord | None,
    transcript: list[TranscriptEntry] | None,
    checks: list[CheckOutcome],
) -> None:
    if transcript is None:
        return
    seqs = [e.seq for e in transcript]
    checks.append(_c("transcript_seq_valid", all(s >= 1 for s in seqs)))
    checks.append(_c("transcript_seq_monotonic",
                     all(a <= b for a, b in pairwise(seqs))))
    guard_ok = True
    for e in transcript:
        argvs = list(e.argv)
        if e.invocation is not None:
            argvs += list(e.invocation.logical_argv) + list(e.invocation.transport_argv)
        for tok in argvs:
            base = tok.rsplit("/", 1)[-1]
            if base in _ENV_DUMP_TOKENS or any(s in tok.upper() for s in _SECRETISH):
                guard_ok = False
    checks.append(_c("transcript_no_env_dump", guard_ok))
    muts = [e for e in transcript if e.mode == "mutation"]
    pend_ids = [e.invocation.command_id for e in muts
                if e.stage == "pending" and e.invocation is not None]
    done_ids = [e.invocation.command_id for e in muts
                if e.stage == "completed" and e.invocation is not None]
    if incident is not None and incident.status == "rejected":
        checks.append(_c("rejected_zero_mutation", not muts, f"count={len(muts)}"))
    else:
        unmatched = [cid for cid in pend_ids if cid not in done_ids]
        checks.append(_c("mutation_pairs_complete", not unmatched, f"unmatched={unmatched!r}"))
        checks.append(_c("mutation_ids_paired", set(pend_ids) == set(done_ids)))


def _check_ledger(
    incident: IncidentRecord | None,
    ledger: list[LedgerRecord] | None,
    checks: list[CheckOutcome],
) -> None:
    if ledger is None:
        return
    phase = LifecyclePhase.PENDING
    legal = True
    for rec in ledger:
        if rec.phase not in LEGAL_TRANSITIONS[phase]:
            legal = False
            break
        phase = rec.phase
    checks.append(_c("ledger_legal_transitions", legal, f"stopped_at={phase.value}"))
    final = ledger[-1].phase if ledger else LifecyclePhase.PENDING
    if incident is not None and incident.status == "accepted":
        checks.append(_c("accepted_final_recovery_verified",
                         final is LifecyclePhase.RECOVERY_VERIFIED, f"final={final.value}"))
    elif incident is not None:
        checks.append(_c("rejected_final_pending",
                         final is LifecyclePhase.PENDING, f"final={final.value}"))
