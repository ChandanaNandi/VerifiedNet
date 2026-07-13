"""Leakage audit over assigned dataset examples (Gate 6.2).

The audit is a PURE function of the assigned examples — no filesystem, no
network, no model, no randomness. It re-derives every identity independently
(``group_id`` from the embedded stable identity, ``example_id`` from the run id)
so a tampered id is caught rather than trusted, and it FAILS CLOSED: any
ERROR-severity finding forces ``passed=False`` (enforced again by the
``LeakageAuditResult`` model).

Checks (ERROR unless noted):

1. ``GROUP_SPANS_SPLITS`` — one ``group_id`` lands in >1 partition.
2. ``DUPLICATE_EXAMPLE_ID`` — the same ``example_id`` appears twice.
3. ``DUPLICATE_SOURCE_RUN`` — the same source ``run_id`` appears twice.
4. ``GROUP_ID_MISMATCH`` — stored ``group_id`` != hash of the stable identity.
5. ``EXAMPLE_ID_MISMATCH`` — stored ``example_id`` != hash of the ``run_id``.
6. ``INVALID_ABSTENTION_ASSIGNMENT`` — an abstention example not in the
   ``abstention`` partition (or a non-abstention example placed there).
7. ``INVALID_ACCEPTED_ASSIGNMENT`` — an accepted example in the ``abstention``
   partition, or in no train/validation/test partition.
8. ``ORIENTATION_SIBLING`` (INFO) — distinct groups differing only by target
   orientation. Informational; never a leak.
9. ``PARAMETER_SIBLING`` (INFO) — distinct groups differing only by a non-
   orientation parameter. Informational; never a leak.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from verifiednet.datasets.models import (
    AssignedDatasetExample,
    DatasetExampleKind,
    DatasetPartition,
    LeakageAuditResult,
    LeakageFinding,
    LeakageFindingCode,
    LeakageSeverity,
    StableScenarioIdentity,
)
from verifiednet.datasets.projection import (
    example_id_for_run_id,
    group_id_for_identity,
)

_ORIENTATION_KEYS = ("target_node", "target_session")

_TRAINABLE_PARTITIONS = frozenset(
    {DatasetPartition.TRAIN, DatasetPartition.VALIDATION, DatasetPartition.TEST}
)


def _err(
    code: LeakageFindingCode,
    detail: str,
    *,
    group_id: str | None = None,
    example_ids: tuple[str, ...] = (),
) -> LeakageFinding:
    return LeakageFinding(
        code=code,
        severity=LeakageSeverity.ERROR,
        group_id=group_id,
        example_ids=example_ids,
        detail=detail,
    )


def _info(
    code: LeakageFindingCode,
    detail: str,
    *,
    group_id: str | None = None,
    example_ids: tuple[str, ...] = (),
) -> LeakageFinding:
    return LeakageFinding(
        code=code,
        severity=LeakageSeverity.INFO,
        group_id=group_id,
        example_ids=example_ids,
        detail=detail,
    )


def _other_params(identity: StableScenarioIdentity) -> tuple[tuple[str, object], ...]:
    return tuple(
        (k, identity.parameters[k])
        for k in sorted(identity.parameters)
        if k not in _ORIENTATION_KEYS
    )


def _check_group_cohesion(
    assigned: Sequence[AssignedDatasetExample],
) -> list[LeakageFinding]:
    partitions: dict[str, set[DatasetPartition]] = defaultdict(set)
    members: dict[str, set[str]] = defaultdict(set)
    for a in assigned:
        partitions[a.example.group_id].add(a.partition)
        members[a.example.group_id].add(a.example.example_id)
    findings: list[LeakageFinding] = []
    for gid in sorted(partitions):
        if len(partitions[gid]) > 1:
            names = ", ".join(sorted(p.value for p in partitions[gid]))
            findings.append(
                _err(
                    LeakageFindingCode.GROUP_SPANS_SPLITS,
                    f"group {gid} spans partitions: {names}",
                    group_id=gid,
                    example_ids=tuple(sorted(members[gid])),
                )
            )
    return findings


def _check_duplicates(
    assigned: Sequence[AssignedDatasetExample],
) -> list[LeakageFinding]:
    findings: list[LeakageFinding] = []
    by_example_id: dict[str, list[str]] = defaultdict(list)
    by_run_id: dict[str, list[str]] = defaultdict(list)
    for a in assigned:
        by_example_id[a.example.example_id].append(a.example.run_id)
        by_run_id[a.example.run_id].append(a.example.example_id)
    for eid in sorted(by_example_id):
        if len(by_example_id[eid]) > 1:
            findings.append(
                _err(
                    LeakageFindingCode.DUPLICATE_EXAMPLE_ID,
                    f"example_id {eid} used by {len(by_example_id[eid])} examples",
                    example_ids=(eid,),
                )
            )
    for rid in sorted(by_run_id):
        if len(by_run_id[rid]) > 1:
            findings.append(
                _err(
                    LeakageFindingCode.DUPLICATE_SOURCE_RUN,
                    f"run_id {rid} projected into {len(by_run_id[rid])} examples",
                    example_ids=tuple(sorted(set(by_run_id[rid]))),
                )
            )
    return findings


def _check_identity_integrity(
    assigned: Sequence[AssignedDatasetExample],
) -> list[LeakageFinding]:
    findings: list[LeakageFinding] = []
    for a in assigned:
        ex = a.example
        expected_group = group_id_for_identity(ex.stable_identity)
        if ex.group_id != expected_group:
            findings.append(
                _err(
                    LeakageFindingCode.GROUP_ID_MISMATCH,
                    f"group_id {ex.group_id} != recomputed {expected_group}",
                    group_id=ex.group_id,
                    example_ids=(ex.example_id,),
                )
            )
        expected_example = example_id_for_run_id(ex.run_id)
        if ex.example_id != expected_example:
            findings.append(
                _err(
                    LeakageFindingCode.EXAMPLE_ID_MISMATCH,
                    f"example_id {ex.example_id} != recomputed {expected_example}",
                    example_ids=(ex.example_id,),
                )
            )
    return findings


def _check_assignment_kind(
    assigned: Sequence[AssignedDatasetExample],
) -> list[LeakageFinding]:
    findings: list[LeakageFinding] = []
    for a in assigned:
        is_abstention = a.example.example_kind is DatasetExampleKind.ABSTENTION
        in_abstention = a.partition is DatasetPartition.ABSTENTION
        if is_abstention and not in_abstention:
            findings.append(
                _err(
                    LeakageFindingCode.INVALID_ABSTENTION_ASSIGNMENT,
                    f"abstention example {a.example.example_id} in {a.partition.value}",
                    group_id=a.example.group_id,
                    example_ids=(a.example.example_id,),
                )
            )
        if not is_abstention and in_abstention:
            findings.append(
                _err(
                    LeakageFindingCode.INVALID_ACCEPTED_ASSIGNMENT,
                    f"accepted example {a.example.example_id} in abstention partition",
                    group_id=a.example.group_id,
                    example_ids=(a.example.example_id,),
                )
            )
        if not is_abstention and a.partition not in _TRAINABLE_PARTITIONS:
            findings.append(
                _err(
                    LeakageFindingCode.INVALID_ACCEPTED_ASSIGNMENT,
                    f"accepted example {a.example.example_id} not in a train/val/test split",
                    group_id=a.example.group_id,
                    example_ids=(a.example.example_id,),
                )
            )
    return findings


def _check_siblings(
    assigned: Sequence[AssignedDatasetExample],
) -> list[LeakageFinding]:
    """Informational-only sibling signals (never affect ``passed``)."""
    identity_by_group: dict[str, StableScenarioIdentity] = {}
    for a in assigned:
        identity_by_group.setdefault(a.example.group_id, a.example.stable_identity)

    findings: list[LeakageFinding] = []

    # Orientation siblings: same everything except target orientation.
    orientation_family: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for gid, ident in identity_by_group.items():
        orientation_key: tuple[object, ...] = (
            ident.template_id,
            ident.scenario_id,
            ident.topology_hash,
            ident.backend,
            _other_params(ident),
        )
        orientation_family[orientation_key].add(gid)
    for okey in sorted(orientation_family, key=str):
        group_ids = orientation_family[okey]
        if len(group_ids) > 1:
            findings.append(
                _info(
                    LeakageFindingCode.ORIENTATION_SIBLING,
                    "groups differ only by target orientation: "
                    + ", ".join(sorted(group_ids)),
                )
            )

    # Parameter siblings: same orientation, differ in a non-orientation param.
    param_family: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for gid, ident in identity_by_group.items():
        param_key: tuple[object, ...] = (
            ident.template_id,
            ident.scenario_id,
            ident.topology_hash,
            ident.backend,
            ident.target_node,
            ident.target_session,
        )
        param_family[param_key].add(gid)
    for pkey in sorted(param_family, key=str):
        group_ids = param_family[pkey]
        if len(group_ids) > 1:
            findings.append(
                _info(
                    LeakageFindingCode.PARAMETER_SIBLING,
                    "groups differ only by a non-orientation parameter: "
                    + ", ".join(sorted(group_ids)),
                )
            )

    return findings


def audit_leakage(
    assigned_examples: Iterable[AssignedDatasetExample],
) -> LeakageAuditResult:
    """Run the full leakage audit and return a fail-closed result."""
    assigned = tuple(assigned_examples)
    findings: list[LeakageFinding] = []
    findings.extend(_check_group_cohesion(assigned))
    findings.extend(_check_duplicates(assigned))
    findings.extend(_check_identity_integrity(assigned))
    findings.extend(_check_assignment_kind(assigned))
    findings.extend(_check_siblings(assigned))

    has_error = any(f.severity is LeakageSeverity.ERROR for f in findings)
    return LeakageAuditResult(passed=not has_error, findings=tuple(findings))
