"""Contract tests for the Gate 4 CommandInvocation and its additive integration.

Two guarantees are pinned here:

1. ``CommandInvocation`` is a frozen, extra-forbidding, JSON-round-tripping,
   deterministically-hashing value object.
2. The ``invocation`` field added to ``ExecResult`` and ``TranscriptEntry`` is
   *additive and optional*: v0.3-serialized payloads (which have no
   ``invocation`` key) still validate, and default the field to ``None``. This
   is the compatibility promise made when the field was introduced without a
   schema-version bump.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from verifiednet.common.hashing import sha256_canonical
from verifiednet.runtime import CommandInvocation, ExecResult, ExecStatus
from verifiednet.runtime.transcript import TranscriptEntry

pytestmark = pytest.mark.contract


def make_invocation() -> CommandInvocation:
    return CommandInvocation(
        command_id="cmd-0123456789abcdef",
        target="router_a",
        logical_argv=("vtysh", "-c", "show ip bgp summary json"),
        transport_argv=(
            "docker",
            "compose",
            "-p",
            "vnet-run-test-0001",
            "-f",
            "docker-compose.yml",
            "exec",
            "-T",
            "router_a",
            "vtysh",
            "-c",
            "show ip bgp summary json",
        ),
    )


def test_invocation_json_round_trip() -> None:
    inv = make_invocation()
    restored = CommandInvocation.model_validate_json(inv.model_dump_json())
    assert restored == inv
    assert restored.logical_argv == inv.logical_argv
    assert restored.transport_argv == inv.transport_argv


def test_invocation_is_frozen() -> None:
    inv = make_invocation()
    with pytest.raises((TypeError, ValueError)):
        inv.command_id = "mutated"  # type: ignore[misc]


def test_invocation_forbids_extra_fields() -> None:
    with pytest.raises(ValueError):
        CommandInvocation.model_validate(
            {
                "command_id": "cmd-x",
                "target": "router_a",
                "logical_argv": ["vtysh"],
                "transport_argv": ["docker"],
                "unexpected": True,
            }
        )


def test_invocation_hash_is_deterministic() -> None:
    assert sha256_canonical(make_invocation()) == sha256_canonical(make_invocation())


# --- additive-compatibility guarantees ------------------------------------


def test_exec_result_without_invocation_key_still_validates() -> None:
    # A v0.3-shaped ExecResult payload has no "invocation" key at all.
    v03_json = (
        '{"status":"ok","target":"router_a",'
        '"argv":["vtysh","-c","show ip bgp summary json"],'
        '"exit_code":0,"stdout":"{}","stderr":"","truncated":false,'
        '"duration_s":0.25,"seq":1,"transcript_ok":true,"detail":""}'
    )
    restored = ExecResult.model_validate_json(v03_json)
    assert restored.invocation is None
    assert restored.status is ExecStatus.OK


def test_transcript_entry_without_invocation_key_still_validates() -> None:
    v03_json = (
        '{"seq":1,"mode":"read","stage":"completed","target":"router_a",'
        '"argv":["vtysh","-c","show version"],"status":"ok",'
        '"started_at":"2026-01-01T00:00:00+00:00","duration_s":0.0}'
    )
    restored = TranscriptEntry.model_validate_json(v03_json)
    assert restored.invocation is None
    assert restored.mode == "read"


def test_exec_result_with_invocation_pairs_argv_with_transport() -> None:
    inv = make_invocation()
    result = ExecResult(
        status=ExecStatus.OK,
        target=inv.target,
        argv=inv.transport_argv,
        exit_code=0,
        stdout="{}",
        stderr="",
        duration_s=0.1,
        seq=1,
        invocation=inv,
    )
    # When an invocation is present the executed argv is the transport argv.
    assert result.argv == result.invocation.transport_argv  # type: ignore[union-attr]
    restored = ExecResult.model_validate_json(result.model_dump_json())
    assert restored == result


def test_transcript_entry_with_invocation_round_trips() -> None:
    inv = make_invocation()
    entry = TranscriptEntry(
        seq=1,
        mode="read",
        stage="completed",
        target=inv.target,
        argv=inv.transport_argv,
        status="ok",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        duration_s=0.0,
        invocation=inv,
    )
    restored = TranscriptEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry
    assert restored.invocation == inv
