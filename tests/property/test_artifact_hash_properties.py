"""Property tests for the run-digest: deterministic, order-independent, sensitive."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from verifiednet.artifacts.layout import ArtifactHash, ArtifactRole
from verifiednet.artifacts.verify import compute_run_digest

pytestmark = pytest.mark.property

_ROLES = list(ArtifactRole)
_HEX = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


@st.composite
def _entry(draw: st.DrawFn) -> ArtifactHash:
    name = draw(st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=8))
    return ArtifactHash(
        relative_path=f"{name}.json",
        role=draw(st.sampled_from(_ROLES)),
        sha256=draw(_HEX),
        size=draw(st.integers(min_value=0, max_value=10_000)),
    )


@st.composite
def _unique_entries(draw: st.DrawFn) -> list[ArtifactHash]:
    entries = draw(st.lists(_entry(), min_size=1, max_size=8))
    seen: dict[str, ArtifactHash] = {e.relative_path: e for e in entries}
    return list(seen.values())


@given(_unique_entries())
def test_digest_is_deterministic(entries: list[ArtifactHash]) -> None:
    assert compute_run_digest(entries) == compute_run_digest(list(entries))


@given(_unique_entries())
def test_digest_is_order_independent(entries: list[ArtifactHash]) -> None:
    assert compute_run_digest(entries) == compute_run_digest(list(reversed(entries)))


@given(_unique_entries())
def test_digest_changes_when_a_hash_changes(entries: list[ArtifactHash]) -> None:
    original = compute_run_digest(entries)
    flipped_hex = "0" if entries[0].sha256[0] != "0" else "1"
    mutated = [entries[0].model_copy(update={"sha256": flipped_hex + entries[0].sha256[1:]})]
    mutated.extend(entries[1:])
    assert compute_run_digest(mutated) != original


@given(_unique_entries())
def test_digest_is_lowercase_hex_64(entries: list[ArtifactHash]) -> None:
    digest = compute_run_digest(entries)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
