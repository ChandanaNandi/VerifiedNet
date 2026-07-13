"""Deterministic, leakage-safe split assignment (Gate 6.2).

Splitting is a PURE function of ``(group_id, SplitPolicy)`` — no randomness, no
Python ``hash()``, no wall-clock, no environment, no global salt. The unit of
assignment is the leakage GROUP, never the individual run: every example that
shares a ``group_id`` lands in exactly one partition, so two runs of the same
scenario can never straddle train/validation/test (ADR-0018 §5).

Assignment NEVER mutates the source ``DatasetExample`` — it wraps it in an
``AssignedDatasetExample``, leaving ``example_id``/``group_id`` untouched.

Abstention (rejected) examples are EVAL-ONLY: they bypass the bucket space
entirely and are always assigned the ``abstention`` partition under a fixed
``ABSTENTION_POLICY_ID``, so no rejected run is ever a train/dev/test member.
"""

from __future__ import annotations

from collections.abc import Iterable

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.models import (
    SPLIT_BUCKET_COUNT,
    AssignedDatasetExample,
    DatasetExample,
    DatasetExampleKind,
    DatasetPartition,
    SplitPolicy,
)

#: The fixed policy id recorded on abstention assignments (never split by ratio).
ABSTENTION_POLICY_ID = "abstention-v1"

_GROUP_ID_RE_PREFIX = "grp-"


class DatasetSplitError(VerifiedNetError):
    """A split could not be assigned deterministically."""


def split_policy_id(policy: SplitPolicy) -> str:
    """Deterministic content id of a split policy (salt + ratios + versions).

    Delegates to ``SplitPolicy.policy_id`` so there is exactly one formula shared
    with the exported ``DatasetManifest``; the value is unchanged from Part 2.
    """
    return policy.policy_id


def _bucket_for_group(group_id: str, policy: SplitPolicy) -> int:
    """The integer bucket in ``[0, SPLIT_BUCKET_COUNT)`` for a group."""
    payload = {
        "algorithm_version": policy.algorithm_version,
        "salt": policy.salt,
        "group_id": group_id,
    }
    digest = sha256_canonical(payload)
    return int(digest, 16) % SPLIT_BUCKET_COUNT


def assign_group_split(*, group_id: str, policy: SplitPolicy) -> DatasetPartition:
    """Map a leakage ``group_id`` to a train/validation/test partition.

    Deterministic and total over the integer bucket space; abstention is NOT a
    possible output here (that is a per-example property, not a group property).
    """
    if not group_id.startswith(_GROUP_ID_RE_PREFIX):
        raise DatasetSplitError(f"not a group id: {group_id!r}")
    bucket = _bucket_for_group(group_id, policy)
    if bucket < policy.train_buckets:
        return DatasetPartition.TRAIN
    if bucket < policy.train_buckets + policy.validation_buckets:
        return DatasetPartition.VALIDATION
    return DatasetPartition.TEST


def assign_example_split(
    *, example: DatasetExample, policy: SplitPolicy
) -> AssignedDatasetExample:
    """Bind one example to a partition without mutating it.

    Abstention examples bypass the policy and go to the ``abstention`` partition;
    accepted examples are split by their ``group_id`` under ``policy``.
    """
    if example.example_kind is DatasetExampleKind.ABSTENTION:
        return AssignedDatasetExample(
            example=example,
            partition=DatasetPartition.ABSTENTION,
            split_policy_id=ABSTENTION_POLICY_ID,
        )
    partition = assign_group_split(group_id=example.group_id, policy=policy)
    return AssignedDatasetExample(
        example=example,
        partition=partition,
        split_policy_id=split_policy_id(policy),
    )


def assign_splits(
    *, examples: Iterable[DatasetExample], policy: SplitPolicy
) -> tuple[AssignedDatasetExample, ...]:
    """Assign a whole collection, enforcing group cohesion across the batch.

    Beyond per-example assignment this re-checks the batch-level invariant that a
    single ``group_id`` never lands in two different partitions (fail closed).
    """
    assigned = tuple(
        assign_example_split(example=e, policy=policy) for e in examples
    )
    group_partition: dict[str, DatasetPartition] = {}
    for a in assigned:
        gid = a.example.group_id
        prev = group_partition.get(gid)
        if prev is None:
            group_partition[gid] = a.partition
        elif prev is not a.partition:
            raise DatasetSplitError(
                f"group {gid} assigned to both {prev} and {a.partition}"
            )
    return assigned
