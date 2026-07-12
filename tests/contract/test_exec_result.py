"""Contract tests: ExecResult serialization stability and ExecStatus snapshot."""

from __future__ import annotations

import pytest

from verifiednet.common.hashing import sha256_canonical
from verifiednet.runtime import ExecResult, ExecStatus

pytestmark = pytest.mark.contract


def make_result() -> ExecResult:
    return ExecResult(
        status=ExecStatus.OK,
        target="router_a",
        argv=("vtysh", "-c", "show ip bgp summary json"),
        exit_code=0,
        stdout='{"peers": 1}',
        stderr="",
        truncated=False,
        duration_s=0.25,
        seq=1,
        transcript_ok=True,
        detail="",
    )


def test_exec_result_json_round_trip() -> None:
    result = make_result()
    restored = ExecResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.argv == result.argv
    assert restored.status is ExecStatus.OK


def test_exec_result_canonical_hash_stable() -> None:
    result = make_result()
    assert sha256_canonical(result) == sha256_canonical(result)
    assert sha256_canonical(make_result()) == sha256_canonical(make_result())


def test_exec_result_none_exit_code_round_trips() -> None:
    denied = ExecResult(
        status=ExecStatus.DENIED_COMMAND,
        target="router_a",
        argv=("rm", "-rf", "/"),
        exit_code=None,
        stdout="",
        stderr="",
        duration_s=0.0,
        seq=2,
        transcript_ok=True,
        detail="binary not allowed: 'rm'",
    )
    restored = ExecResult.model_validate_json(denied.model_dump_json())
    assert restored == denied
    assert restored.exit_code is None


def test_exec_result_rejects_extra_fields() -> None:
    with pytest.raises(ValueError, match="extra"):
        ExecResult.model_validate({**make_result().model_dump(), "surprise": True})


def test_exec_result_rejects_seq_below_one() -> None:
    payload = {**make_result().model_dump(), "seq": 0}
    with pytest.raises(ValueError, match="seq"):
        ExecResult.model_validate(payload)


def test_exec_status_values_snapshot() -> None:
    assert {status.value for status in ExecStatus} == {
        "ok",
        "denied_command",
        "denied_target",
        "timeout",
        "target_not_found",
        "nonzero_exit",
        "internal_error",
    }
    assert len(list(ExecStatus)) == 7
