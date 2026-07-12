"""Unit tests for ClaimVerifier: every predicate, trusted semantics, edge cases."""

from __future__ import annotations

import json
from typing import Any

import pytest

from verifiednet.common.hashing import sha256_bytes
from verifiednet.common.runctx import RunContext
from verifiednet.schemas import (
    EvidenceBundle,
    EvidenceRecord,
    EvidenceSource,
    Phase,
    Predicate,
    Verdict,
    VerificationCheck,
)
from verifiednet.verifiers.claims import ClaimVerifier, Verifier

pytestmark = pytest.mark.unit


def mk_record(
    run_ctx: RunContext,
    normalized: dict[str, Any],
    *,
    trusted: bool = True,
    phase: Phase = "precondition",
    target: str = "router_a",
) -> EvidenceRecord:
    payload = json.dumps(normalized, sort_keys=True)
    seq = run_ctx.next_seq()
    return EvidenceRecord(
        evidence_id=run_ctx.content_id("ev", {"n": normalized, "seq": seq, "t": trusted}),
        phase=phase,
        source=EvidenceSource(collector="fake.collector", target=target, trusted=trusted),
        raw_sha256=sha256_bytes(payload.encode("utf-8")),
        raw_payload=payload,
        normalized=normalized,
        captured_at=run_ctx.now(),
        run_seq=seq,
    )


def mk_bundle(
    run_ctx: RunContext,
    *records_normalized: dict[str, Any],
    trusted: bool = True,
    phase: Phase = "precondition",
) -> EvidenceBundle:
    records = tuple(
        mk_record(run_ctx, n, trusted=trusted, phase=phase) for n in records_normalized
    )
    return EvidenceBundle(
        bundle_id=run_ctx.content_id("bundle", {"phase": phase, "n": len(records)}),
        phase=phase,
        records=records,
    )


def mk_check(
    predicate: Predicate,
    expected: tuple[str, ...],
    metric: str = "bgp.peer.172.30.0.2.state",
    *,
    require_trusted: bool = True,
) -> VerificationCheck:
    return VerificationCheck(
        check_id=f"t:{metric}:{predicate.value}",
        claim="test claim",
        subject="router_a",
        metric=metric,
        predicate=predicate,
        expected=expected,
        phase="precondition",
        require_trusted=require_trusted,
    )


@pytest.fixture
def verifier(run_ctx: RunContext) -> ClaimVerifier:
    return ClaimVerifier(run_ctx)


def test_claim_verifier_satisfies_protocol(verifier: ClaimVerifier) -> None:
    proto: Verifier = verifier
    assert proto is verifier


# ------------------------------------------------------------------ EQUALS


def test_equals_pass(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [bundle])
    assert result.verdict is Verdict.PASS
    assert result.committable
    assert result.observed == ("Established",)
    assert len(result.evidence_ids) == 1


def test_equals_fail(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Idle"})
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [bundle])
    assert result.verdict is Verdict.FAIL
    assert not result.committable


def test_equals_contradictory_evidence_fails_with_detail(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(
        run_ctx,
        {"bgp.peer.172.30.0.2.state": "Established"},
        {"bgp.peer.172.30.0.2.state": "Idle"},
    )
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [bundle])
    assert result.verdict is Verdict.FAIL
    assert "contradictory" in result.detail


# -------------------------------------------------------------- NOT_EQUALS


def test_not_equals_pass(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.remote_as": "65999"})
    check = mk_check(
        Predicate.NOT_EQUALS, ("65002",), metric="bgp.peer.172.30.0.2.remote_as"
    )
    assert verifier.verify(check, [bundle]).verdict is Verdict.PASS


def test_not_equals_fail(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.remote_as": "65002"})
    check = mk_check(
        Predicate.NOT_EQUALS, ("65002",), metric="bgp.peer.172.30.0.2.remote_as"
    )
    assert verifier.verify(check, [bundle]).verdict is Verdict.FAIL


# ------------------------------------------------------ IN_SET / NOT_IN_SET


def test_in_set_pass_and_fail(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    down = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Idle"})
    up = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    check = mk_check(Predicate.IN_SET, ("Idle", "Active", "Connect"))
    assert verifier.verify(check, [down]).verdict is Verdict.PASS
    assert verifier.verify(check, [up]).verdict is Verdict.FAIL


def test_not_in_set_pass_and_fail(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    up = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    down = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Active"})
    check = mk_check(Predicate.NOT_IN_SET, ("Idle", "Active", "Connect"))
    assert verifier.verify(check, [up]).verdict is Verdict.PASS
    assert verifier.verify(check, [down]).verdict is Verdict.FAIL


# ---------------------------------------------- GREATER_THAN / LESS_THAN


def test_greater_than_pass_and_fail(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    high = mk_bundle(run_ctx, {"bgp.prefixes.received": "12"})
    low = mk_bundle(run_ctx, {"bgp.prefixes.received": "3"})
    check = mk_check(Predicate.GREATER_THAN, ("5",), metric="bgp.prefixes.received")
    assert verifier.verify(check, [high]).verdict is Verdict.PASS
    assert verifier.verify(check, [low]).verdict is Verdict.FAIL


def test_less_than_pass_and_fail(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    low = mk_bundle(run_ctx, {"ping.rtt_avg_ms": "0.5"})
    high = mk_bundle(run_ctx, {"ping.rtt_avg_ms": "250.0"})
    check = mk_check(Predicate.LESS_THAN, ("100",), metric="ping.rtt_avg_ms")
    assert verifier.verify(check, [low]).verdict is Verdict.PASS
    assert verifier.verify(check, [high]).verdict is Verdict.FAIL


def test_non_numeric_comparison_is_unknown(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    check = mk_check(Predicate.GREATER_THAN, ("5",))
    result = verifier.verify(check, [bundle])
    assert result.verdict is Verdict.UNKNOWN
    assert "non-numeric" in result.detail
    assert not result.committable


# ----------------------------------------------------------------- ANY


def test_any_passes_when_observation_exists(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Idle"})
    result = verifier.verify(mk_check(Predicate.ANY, ()), [bundle])
    assert result.verdict is Verdict.PASS
    assert result.observed == ("Idle",)


def test_any_insufficient_without_observation(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"other.metric": "x"})
    result = verifier.verify(mk_check(Predicate.ANY, ()), [bundle])
    assert result.verdict is Verdict.INSUFFICIENT


# --------------------------------------------------------- trusted semantics


def test_untrusted_only_evidence_is_insufficient(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"}, trusted=False)
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [bundle])
    assert result.verdict is Verdict.INSUFFICIENT
    assert "untrusted" in result.detail
    assert result.observed == ()
    assert result.evidence_ids == ()


def test_untrusted_ignored_when_mixed_with_trusted(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    trusted = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    untrusted = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Idle"}, trusted=False)
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [trusted, untrusted])
    # The contradicting untrusted record must not poison the verdict.
    assert result.verdict is Verdict.PASS
    assert result.observed == ("Established",)


def test_require_trusted_false_admits_untrusted(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"}, trusted=False)
    check = mk_check(Predicate.EQUALS, ("Established",), require_trusted=False)
    assert verifier.verify(check, [bundle]).verdict is Verdict.PASS


# ----------------------------------------------------------------- edges


def test_no_observations_is_insufficient(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"unrelated": "1"})
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [bundle])
    assert result.verdict is Verdict.INSUFFICIENT
    assert not result.committable


def test_empty_bundles_is_insufficient(verifier: ClaimVerifier) -> None:
    result = verifier.verify(mk_check(Predicate.EQUALS, ("Established",)), [])
    assert result.verdict is Verdict.INSUFFICIENT


def test_empty_expected_non_any_is_unknown(
    run_ctx: RunContext, verifier: ClaimVerifier
) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    result = verifier.verify(mk_check(Predicate.EQUALS, ()), [bundle])
    assert result.verdict is Verdict.UNKNOWN
    assert result.detail == "no expected value"


def test_result_metadata_populated(run_ctx: RunContext, verifier: ClaimVerifier) -> None:
    bundle = mk_bundle(run_ctx, {"bgp.peer.172.30.0.2.state": "Established"})
    check = mk_check(Predicate.EQUALS, ("Established",))
    first = verifier.verify(check, [bundle])
    second = verifier.verify(check, [bundle])
    assert first.check_id == check.check_id
    assert first.phase == check.phase
    assert second.evaluated_at_seq > first.evaluated_at_seq
    assert first.evaluated_at.tzinfo is not None
