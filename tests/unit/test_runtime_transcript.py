"""Unit tests for transcript entries, in-memory and file transcripts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from verifiednet.runtime import FileTranscript, InMemoryTranscript, TranscriptEntry

pytestmark = pytest.mark.unit

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def make_entry(seq: int = 1, status: str = "ok") -> TranscriptEntry:
    return TranscriptEntry(
        seq=seq,
        mode="read",
        stage="completed",
        target="router_a",
        argv=("vtysh", "-c", "show ip bgp summary json"),
        status=status,
        started_at=EPOCH,
        duration_s=0.25,
    )


def test_in_memory_append_and_entries() -> None:
    transcript = InMemoryTranscript()
    transcript.append(make_entry(1))
    transcript.append(make_entry(2))
    assert [e.seq for e in transcript.entries] == [1, 2]


def test_sha256_deterministic_across_identical_runs() -> None:
    first = InMemoryTranscript()
    second = InMemoryTranscript()
    for transcript in (first, second):
        transcript.append(make_entry(1))
        transcript.append(make_entry(2, status="nonzero_exit"))
    assert first.sha256() == second.sha256()
    assert first.sha256() == first.sha256()


def test_sha256_changes_with_content() -> None:
    first = InMemoryTranscript()
    second = InMemoryTranscript()
    first.append(make_entry(1))
    second.append(make_entry(2))
    assert first.sha256() != second.sha256()


def test_entry_json_round_trip() -> None:
    entry = make_entry()
    restored = TranscriptEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry


def test_entry_rejects_naive_started_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TranscriptEntry(
            seq=1,
            mode="read",
            stage="completed",
            target="router_a",
            argv=("vtysh",),
            status="ok",
            started_at=EPOCH.replace(tzinfo=None),
        )


def test_file_transcript_writes_readable_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    transcript = FileTranscript(path)
    entries = [make_entry(1), make_entry(2, status="timeout")]
    for entry in entries:
        transcript.append(entry)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    restored = [TranscriptEntry.model_validate_json(line) for line in lines]
    assert restored == entries
