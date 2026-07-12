# Gate 4 Step 4 — One Deliberately-Rejected Live Incident (Healthy Lab)

**Status:** IMPLEMENTED AND LIVE-VERIFIED. Exactly one deliberately-rejected
incident is produced. The rejection occurs during precondition validation,
before any mutation. No general orchestrator, CLI, or artifact-directory
framework was added; no model participated; no Gate 5 capability was added.

All numbers below are observed on the canonical host, not projected.

## Source and environment

```
source commit (baseline for this step): 14d1d25
image (manifest list): frrouting/frr:v8.4.1@sha256:0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e
FRR (live):            8.4.1_git
host:                  macOS (Darwin, arm64), Docker Engine 29.1.3, Compose 2.40.3
```

## Healthy topology

The same approved two-router lab and configuration as the accepted run
(ADR 0006): `router_a` AS65001 ↔ `router_b` AS65002, one `/30` link, one eBGP
session `a-b`, one advertised loopback per side. The lab is booted normally and
converges healthily — it is never broken, corrupted, or misconfigured.

## Impossible precondition (why it is deterministic)

The rejected scenario requires that `router_a` carry the route
`203.0.113.99/32` — an RFC 5737 **TEST-NET-3 documentation** prefix. It is:

- never configured, never advertised, and cannot arise from this topology;
- an ordinary read observation (no timing, no mutation, no broken command);
- proven absent by the EXISTING `RoutePresenceCollector`, which emits
  `route.203.0.113.99/32.present == "false"` for a requested-but-absent prefix.

Because the metric is present with the value `"false"`, the existing
`route_present` check returns a real **FAIL** — distinct from `INSUFFICIENT`,
which is what a *missing* observation would yield. Only a deterministic `FAIL`
produces the rejection; an `INSUFFICIENT`/`UNKNOWN` verdict (missing or unusable
evidence) raises `NonDeterministicRejectionError` and is never converted into a
`PRECONDITION_FAILED` record.

Live confirmation before implementation (healthy lab): `route 10.255.0.2/32
present: true`, `route 203.0.113.99/32 present: false`, BGP `Established`, ping
`all_success: true`.

## Rejected-run adapter

`labs/frr/rejected_scenario.py::RejectedPreconditionRun` is the smallest explicit
wiring: it collects baseline evidence via the existing
`LiveScenarioEvidenceProvider` (healthy facts for both routers) plus one
additional `RoutePresenceCollector` for the impossible prefix, evaluates the
`route_present` check with the existing `ClaimVerifier`, and — on the
deterministic `FAIL` — raises `PreconditionResultsError` and builds the record
via the existing `build_rejected_record`. It constructs **no** mutation executor,
creates **no** `FaultInjection`, calls **no** oracle, and never appends
`PRECHECKED`.

## Observed lifecycle (instrumented live run)

```
healthy BGP convergence : 3 attempts, 2.81 s
precondition evaluation : 1.68 s
failed check            : route_present:router_a:route.203.0.113.99/32.present:precondition
verdict                 : fail    observed = ("false",)    evidence_ids = 1
ledger final phase      : PENDING
transcript              : 21 read entries, 0 mutation entries
record                  : status=rejected  code=precondition_failed
                          ground_truth=None  fault=None  restoration=None
incident_id             : inc-d0c1852346c0cc57  (deterministic content hash)
```

## Zero-mutation proof

No `MutationExecutor` was constructed or invoked; the shared transcript contains
**0 mutation-mode entries** (21 read entries only); the ledger never left
`PENDING` (never reached `INJECTING`). After the rejected run the lab is proven
still healthy: BGP `Established` on both routers, both loopback routes present,
link ping `3/3`, and the `router_a`/`router_b` running-config hashes are
byte-identical to the baseline captured during the run.

## Restoration-not-required proof

Because no mutation occurred, restoration is not applicable: the rejected record
carries `restoration = None` (not attempted, not needed), consistent with the
released schema for a precondition-rejected record.

## Rejected IncidentRecord fields

```
status              = rejected
failed_phase        = precondition
rejection.code      = PRECONDITION_FAILED
rejection.details   = "required route 203.0.113.99/32 was absent on router_a"
ground_truth        = absent
fault               = absent
baseline_evidence   = retained and sealed (healthy facts + impossible-route observation)
onset_evidence      = absent
recovery_evidence   = absent
restoration         = absent (not applicable)
precondition_results = 1 result, verdict FAIL, not committable
completed_phases    = ()
cleanup_status      = clean
final ledger phase  = PENDING
```

The record preserves the scenario definition, topology, baseline evidence, the
failed verification result (with its supporting evidence id), a deterministic
`inc-…` id, provenance, and factual rejection details. It round-trips losslessly
through `IncidentRecord` and its canonical bytes are stable. For this step the
record is written to the test's `tmp_path`; no canonical artifact directory was
created.

## Cleanup proof

Teardown runs in `finally`; after every live run in this step, `docker ps -a` /
`docker network ls` filtered by the Compose project label showed **zero
containers and zero networks**.

## Tests

Offline: 332 → 341 (+9 — impossible-route determinism, FAIL-vs-INSUFFICIENT,
rejected-record shape/round-trip/deterministic-id, ledger stays PENDING, mutation
spy never called, and infra/evidence failures that must NOT become
`PRECONDITION_FAILED`). Integration (Docker-gated): 20 → 21 (+1 rejected
incident), 36.7 s total; the rejected-incident test ≈ 7 s. The accepted incident
test continues to pass unchanged (`RECOVERY_VERIFIED`).

## Limitations

Single reference host (macOS/arm64). The impossible precondition is a route-
presence check; other deterministic precondition families are later work. The
rejected record is written to a temporary path — the canonical artifact directory
is deliberately a later step. No Gate 3 contract was modified.

## Explicit non-actions

One deliberately-rejected incident was produced; rejection occurred BEFORE any
mutation; no ground truth was produced; no fault was injected; restoration was
not required; no general orchestrator exists; no model participated; no Gate 5
capability was added.
