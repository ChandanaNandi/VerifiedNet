"""Unit tests for the artifact layout schemas and path safety."""

from __future__ import annotations

import pytest

from verifiednet.artifacts.layout import (
    ArtifactEntry,
    ArtifactHash,
    ArtifactHashIndex,
    ArtifactRole,
    RunLayout,
    is_safe_relative_path,
    is_safe_run_id,
)
from verifiednet.artifacts.verify import compute_run_digest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("run_id", ["run-test-0001", "it-acc-1783", "a1"])
def test_safe_run_ids(run_id: str) -> None:
    assert is_safe_run_id(run_id)


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b", "../x", "A-upper", "x/../y", "/abs"])
def test_unsafe_run_ids(run_id: str) -> None:
    assert not is_safe_run_id(run_id)


@pytest.mark.parametrize("path", ["incident.json", "evidence/baseline.json", "a/b/c.json"])
def test_safe_relative_paths(path: str) -> None:
    assert is_safe_relative_path(path)


@pytest.mark.parametrize(
    "path", ["/abs/x.json", "../escape.json", "a/../b.json", "a\\b.json", "evidence/../x"]
)
def test_unsafe_relative_paths(path: str) -> None:
    assert not is_safe_relative_path(path)


def test_artifact_entry_rejects_absolute_path() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        ArtifactEntry(relative_path="/etc/passwd", role=ArtifactRole.INCIDENT)


def test_artifact_hash_validates_sha_hex() -> None:
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ArtifactHash(relative_path="incident.json", role=ArtifactRole.INCIDENT,
                     sha256="XYZ", size=1)
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ArtifactHash(relative_path="incident.json", role=ArtifactRole.INCIDENT,
                     sha256="A" * 64, size=1)  # uppercase rejected


def test_run_layout_rejects_bad_run_id() -> None:
    with pytest.raises(ValueError):
        RunLayout(run_id="../evil", acceptance_status="accepted",
                  artifacts=(ArtifactEntry(relative_path="layout.json", role=ArtifactRole.LAYOUT),))


def test_run_digest_is_deterministic_and_order_independent() -> None:
    a = ArtifactHash(relative_path="a.json", role=ArtifactRole.INCIDENT, sha256="a" * 64, size=1)
    b = ArtifactHash(relative_path="b.json", role=ArtifactRole.LAYOUT, sha256="b" * 64, size=2)
    assert compute_run_digest([a, b]) == compute_run_digest([b, a])  # sorted internally
    assert compute_run_digest([a, b]) != compute_run_digest([a])


def test_run_digest_changes_when_a_hash_changes() -> None:
    a = ArtifactHash(relative_path="a.json", role=ArtifactRole.INCIDENT, sha256="a" * 64, size=1)
    a2 = ArtifactHash(relative_path="a.json", role=ArtifactRole.INCIDENT, sha256="c" * 64, size=1)
    assert compute_run_digest([a]) != compute_run_digest([a2])


def test_hash_index_requires_digest_hex() -> None:
    entry = ArtifactHash(
        relative_path="a.json", role=ArtifactRole.INCIDENT, sha256="a" * 64, size=1
    )
    with pytest.raises(ValueError, match="run_digest"):
        ArtifactHashIndex(run_id="run-x", run_digest="nope", entries=(entry,))
