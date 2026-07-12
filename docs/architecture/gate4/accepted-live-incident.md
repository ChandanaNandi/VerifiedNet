# Gate 4 Step 3 — One Accepted Live BGP Remote-AS-Mismatch Incident

**Status:** IMPLEMENTED AND LIVE-VERIFIED. Exactly one accepted incident is
produced. No rejected incident, no general orchestrator, no CLI, no artifact
directory framework, no model, and no Gate 5 capability were added.

All numbers below are observed on the canonical host, not projected.

## Source and environment

```
source commit (baseline for this step): f7f5afa
image (manifest list): frrouting/frr:v8.4.1@sha256:0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e
linux/arm64 digest:    sha256:9602a0697e261e29b82fdf4819cd8850355851b71b80dafadd4aa4ce983355eb
FRR (live):            8.4.1_git
host:                  macOS (Darwin, arm64), Docker Engine 29.1.3, Compose 2.40.3
```

## Topology and scenario

The approved two-router lab (ADR 0006): `router_a` AS65001 ↔ `router_b` AS65002,
one `/30` link (`172.30.0.1` ↔ `172.30.0.2` on `eth1`), one eBGP session `a-b`,
one advertised loopback per side. The fault targets `router_a` only.

## Mutation commands (approved; no others exist)

Injection, restore, and forced reconvergence are the exact vtysh argv builders
from `verifiednet.faults.frr_commands`, executed through the `MutationExecutor`
under the two approved `bgp_remote_as_mutation_shapes` and a `TargetPolicy`
whose allow-set is `{"router_a"}` — a command aimed at `router_b` is DENIED by
the runtime, never reaching the wire.

```
inject : vtysh -c "configure terminal" -c "router bgp 65001" -c "neighbor 172.30.0.2 remote-as 65999"
restore: vtysh -c "configure terminal" -c "router bgp 65001" -c "neighbor 172.30.0.2 remote-as 65002"
clear  : vtysh -c "clear bgp 172.30.0.2"
```

Each is executed as the transport `docker compose -p <project> -f <file> exec -T router_a <logical…>`.

## Truth source for the configured remote-AS (Step 6, verified live)

The critical correctness question: does `show ip bgp summary json` report the
configured neighbor `remoteAs` after the wrong-AS mutation, even with the
session down? **Yes.** Live output on `router_a` immediately after injection:

```
peers."172.30.0.2".state    = "Idle"
peers."172.30.0.2".remoteAs = 65999      (the configured value)
```

`running-config` corroborates (`neighbor 172.30.0.2 remote-as 65999`). The
configured value is therefore a deterministic observation from the existing
`BgpSummaryCollector` metric `bgp.peer.172.30.0.2.remote_as` — **no new parser,
no string/LLM guess**. The onset check `remote_as_equals(..., 65999)` and the
recovery check `remote_as_equals(..., 65002)` read that same metric.

## Live evidence provider

`labs/frr/scenario_evidence.py::LiveScenarioEvidenceProvider` satisfies the
scenario's `evidence_provider` callable using the backend's READ-ONLY executor
and the existing Gate 3 collectors — it never imports or exposes the mutation
executor. Because `ClaimVerifier` matches evidence by metric key across all
records in a bundle (target-blind), the per-phase collection is tailored: the
**ONSET** bundle carries `config.sha256` for the PEER (`router_b`) only, so
`config_unchanged` evaluates the peer's hash unambiguously — mirroring the
Gate 3 fake-lifecycle shape. Bundles are sealed before return with stable
record ordering.

## Observed lifecycle (instrumented live run)

```
healthy BGP convergence : 4 attempts, 4.07 s
inject                  : 0.14 s   ledger injecting -> injected
onset observed (router_a): state=Idle, remoteAs=65999
onset verification      : 2.63 s (two consecutive confirmations)
restore                 : 0.26 s   ledger restoring -> restored
recovery verification   : 6.89 s (two consecutive confirmations)
recovery observed (router_a): state=Established, remoteAs=65002
total wall (start→stop) : 17.61 s
```

Ledger phase sequence (exact, in order):

```
prechecked → injecting → injected → onset_verified → restoring → restored → recovery_verified
```

## Deterministic onset facts proved

Session not Established (`Idle`); configured remote-AS is `65999`; `eth1` remains
admin/oper up; link ping remains `3/3`; `router_b` running-config hash equals its
baseline hash; the peer loopback route `10.255.0.2/32` is **withdrawn** on
`router_a` (`route.10.255.0.2/32.present == "false"`).

## Deterministic recovery facts proved

Established on both sides; `router_a` configured remote-AS is `65002` again; both
loopback routes restored (`10.255.0.2/32` on `router_a`, `10.255.0.1/32` on
`router_b`); interfaces up; ping `3/3`; `router_b` config hash unchanged from
baseline; `router_a` config hash restored to its baseline value (the reverted
running-config is byte-identical to baseline).

## Transcript pairing proof

The mutation path emitted **6 mutation transcript entries** = 3 write-ahead
`pending` + 3 `completed` (inject, restore, clear), each pending/completed pair
sharing one deterministic `command_id`; every mutation entry targets
`router_a`. No unmatched pending entry remained.

## Accepted IncidentRecord

Built only after the ledger reached `RECOVERY_VERIFIED` with every
precondition/onset/recovery verdict committable. `GroundTruth` was assembled by
the existing oracle from the `FaultInjection` and the deterministic verifier
verdicts alone, root-cause label `bgp_remote_as_mismatch`, oracle version
`1.0.0`; every accepted evidence id exists in the collected bundles. The record
validates: `status=accepted`, ground truth present, restoration completed with
forced reset recorded, baseline+onset evidence sealed, JSON round-trip lossless,
canonical bytes stable, deterministic `inc-…` id, no rejection information. For
this step the record is written to the test's `tmp_path`; no canonical artifact
directory was created.

## Cleanup proof

Nested `try/finally`: restoration is attempted if the ledger reached an injected
phase, and backend teardown always runs. After every live run in this step,
`docker ps -a` / `docker network ls` filtered by the Compose project label
showed **zero containers and zero networks**.

## Tests

Offline: 332 (was 319, +13 — mutation-adapter transport/policy/pairing, the full
accepted slice driven offline through the real scenario + real `MutationExecutor`
+ real provider via a deterministic lab simulator, evidence-provider shape, and
live-path failure modes). Integration (Docker-gated): 20 (was 19, +1 accepted
incident), 28.6 s total; the accepted-incident test alone ≈ 21 s.

## Limitations

Single reference host (macOS/arm64). Reachability is 3 sequential single-packet
probes (3/3 rule). Convergence/onset/recovery bounds are operational defaults
(30/30/60 s, 1 s poll, 2 confirmations), not benchmark-derived. The accepted
record is written to a temporary path — the canonical run/artifact directory is
deliberately a later step.

## Explicit non-actions

One accepted incident was produced; NO rejected incident was produced; NO
general orchestrator exists; NO CLI, NO artifact-directory framework; NO model
participated in the truth chain (ground truth is fault metadata + deterministic
verdicts only); NO Gate 5 capability was added.
