"""Unit tests for the pure bounded-polling state machine."""

from __future__ import annotations

import pytest

from verifiednet.verifiers.polling import PollOutcome, poll_until

pytestmark = pytest.mark.unit


class FakeTime:
    """Deterministic monotonic clock advanced only by recorded sleeps."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class ScriptedSample:
    def __init__(self, values: list[bool], repeat_last: bool = False) -> None:
        self._values = list(values)
        self._repeat_last = repeat_last
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if len(self._values) > 1 or not self._repeat_last:
            return self._values.pop(0)
        return self._values[0]


def test_satisfied_on_two_consecutive_after_flap() -> None:
    time = FakeTime()
    sample = ScriptedSample([True, False, True, True])
    outcome = poll_until(
        sample,
        timeout_s=30.0,
        interval_s=0.5,
        monotonic=time.monotonic,
        sleep=time.sleep,
        consecutive=2,
    )
    assert outcome.satisfied
    assert outcome.attempts == 4
    assert outcome.consecutive_successes == 2
    # The flap must have reset the streak: satisfaction required attempts 3+4.
    assert sample.calls == 4
    assert time.sleeps == [0.5, 0.5, 0.5]
    assert "unsatisfied" in outcome.last_detail  # records the last failure seen


def test_timeout_returns_unsatisfied_with_attempt_count() -> None:
    time = FakeTime()
    outcome = poll_until(
        lambda: False,
        timeout_s=2.0,
        interval_s=0.5,
        monotonic=time.monotonic,
        sleep=time.sleep,
        consecutive=2,
    )
    assert not outcome.satisfied
    assert outcome.attempts == 5  # t = 0, 0.5, 1.0, 1.5, 2.0
    assert outcome.consecutive_successes == 0
    assert outcome.elapsed_s == pytest.approx(2.0)
    assert time.sleeps == [0.5] * 4


def test_interval_respected_via_sleep_log() -> None:
    time = FakeTime()
    poll_until(
        lambda: False,
        timeout_s=3.0,
        interval_s=1.5,
        monotonic=time.monotonic,
        sleep=time.sleep,
    )
    assert time.sleeps == [1.5, 1.5]


def test_consecutive_one_satisfies_on_first_success() -> None:
    time = FakeTime()
    outcome = poll_until(
        lambda: True,
        timeout_s=10.0,
        interval_s=0.5,
        monotonic=time.monotonic,
        sleep=time.sleep,
        consecutive=1,
    )
    assert outcome.satisfied
    assert outcome.attempts == 1
    assert time.sleeps == []


def test_single_success_never_satisfies_consecutive_two() -> None:
    time = FakeTime()
    flapper = ScriptedSample([True, False], repeat_last=True)
    outcome = poll_until(
        flapper,
        timeout_s=2.0,
        interval_s=1.0,
        monotonic=time.monotonic,
        sleep=time.sleep,
        consecutive=2,
    )
    assert not outcome.satisfied


def test_sample_exception_propagates() -> None:
    time = FakeTime()

    def explode() -> bool:
        raise RuntimeError("collector blew up")

    with pytest.raises(RuntimeError, match="collector blew up"):
        poll_until(
            explode,
            timeout_s=5.0,
            interval_s=0.5,
            monotonic=time.monotonic,
            sleep=time.sleep,
        )


def test_invalid_parameters_rejected() -> None:
    time = FakeTime()
    with pytest.raises(ValueError, match="consecutive"):
        poll_until(
            lambda: True,
            timeout_s=1.0,
            interval_s=0.1,
            monotonic=time.monotonic,
            sleep=time.sleep,
            consecutive=0,
        )
    with pytest.raises(ValueError, match="positive"):
        poll_until(
            lambda: True,
            timeout_s=0.0,
            interval_s=0.1,
            monotonic=time.monotonic,
            sleep=time.sleep,
        )


def test_outcome_is_frozen() -> None:
    outcome = PollOutcome(
        satisfied=True, attempts=1, consecutive_successes=1, elapsed_s=0.0
    )
    with pytest.raises(AttributeError):
        outcome.satisfied = False  # type: ignore[misc]
