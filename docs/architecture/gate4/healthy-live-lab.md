# Gate 4 Step 2 — The Healthy Live Lab

**Status:** IMPLEMENTED AND LIVE-VERIFIED (this step proves the healthy
foundation only — no fault was injected, no mutation command was executed, and
no incident record was built).

## What was booted

The approved two-router routed-eBGP topology (ADR 0006), from the approved
immutable image:

```
image (manifest list): frrouting/frr:v8.4.1@sha256:0f8c174d95add7916101077d4716822552c758b8ff3d2dcb55104f6534202e3e
linux/arm64 digest:    sha256:9602a0697e261e29b82fdf4819cd8850355851b71b80dafadd4aa4ce983355eb
FRR version (live):    8.4.1_git
```

| | router_a | router_b |
|---|---|---|
| ASN | 65001 | 65002 |
| link iface | eth1 (pinned via `interface_name`) | eth1 |
| link IP (FRR) | 172.30.0.1/30 | 172.30.0.2/30 |
| loopback | 10.255.0.1/32 | 10.255.0.2/32 |
| peer | 172.30.0.2 remote-as 65002 | 172.30.0.1 remote-as 65001 |

Reference host: macOS (Darwin, kernel 25.5.0, arm64), Docker Engine 29.1.3,
Compose 2.40.3, Docker server arch arm64.

## Configuration delivery (actual mounted paths)

Generated `daemons` and per-node `frr.conf` are rendered into the backend's
per-run build directory and embedded byte-identically as inline Compose
`configs`, delivered read-only (0444) via the Docker API to the verified
in-container paths:

```
/etc/frr/daemons     (enables bgpd; stock image ships bgpd=no)
/etc/frr/frr.conf    (integrated config, applied by the image entrypoint)
```

No bind mounts, no repository mount, no arbitrary host path, no published
ports, no `container_name`. `cap_add` is `NET_ADMIN + SYS_ADMIN` — the latter
is a live-proven hard requirement of FRR 8.4.1 `privs_init` (ADR 0015; Gate 3's
rejection is reversed there with the recorded evidence).

## Readiness vs convergence

Two deliberately separate concepts:

- **Readiness** (`FrrComposeBackend.start()`): services exist, containers
  running, FRR answers a harmless read-only `vtysh` command. Bounded polling,
  monotonic deadline. Start does NOT claim BGP convergence.
- **Convergence** (`wait_for_bgp_established`): the eBGP session is
  `Established` on BOTH endpoints, observed through the policy-checked
  read-only path with the SAME parser the BGP collector uses, for TWO
  consecutive polls. Monotonic deadline (default 60 s), fixed 1 s interval.
  Timeout raises typed `BgpConvergenceTimeoutError` carrying attempts, elapsed
  time, and last observed per-endpoint states. Defaults are bounded
  operational defaults for this first live slice, not benchmark-derived.

Observed live (fixture capture run, this host): **converged in 2.8 s / 3 poll
attempts** after readiness; full-lifecycle integration runs observe the same
order of magnitude (BGP is typically Established before the first poll because
readiness polling already gave the daemons time to peer).

## Live healthy evidence (collected by the existing collectors)

All five collectors ran against the live backend through its read-only
transport executor (`backend.readonly_executor`) — no collector bypasses its
contract, no direct `docker exec` anywhere. Verified live values include, per
router: `bgp.local_as` (65001/65002), peer `state=Established` with the correct
`remote_as`, `iface.eth1.admin/oper=up`, the peer loopback present via protocol
`bgp` and the own loopback via `connected`, link reachability `3/3`
(`all_success=true`), and a real `config.sha256` over the running config.

### Parser change forced by real output

Live FRR 8.4.1 `show interface json` includes kernel pseudo-interfaces
(`erspan0`, `gre0`, `tunl0`, …) that are administratively down and OMIT the
`operationalStatus` key entirely — the source-derived provisional fixtures
never showed this. The interface parser now normalizes a missing operational
status to `down` ONLY when the administrative status is `down` (admin-down
implies oper-down); any other absence remains a loud `ParserError`. All
source-derived fixture shapes parse unchanged (backward compatible). This was
the only parser change required; BGP summary, routes, and running-config live
formats parsed as-is.

## Live fixture capture set

```
tests/fixtures/frr/live/frr-8.4.1-linux-arm64/
  router_{a,b}_bgp_summary_established.json
  router_{a,b}_interfaces.json
  router_{a,b}_routes.json
  router_{a,b}_running_config.txt
  manifest.json
```

Raw command output saved byte-exactly (never hand-edited); normalization
happens only in code. The canonical-JSON manifest binds the set to: fixture
schema version, live FRR version, both image digests, host arch/OS/kernel,
Docker server arch + Docker/Compose versions, the exact logical AND transport
argv of every capture, per-file SHA-256, capture timestamp, topology hash,
source commit, and explicit statements that the set came from the live
two-router healthy lab with zero mutation commands. Capture aborts if the
backend transcript contains any mutation-mode entry (checked before AND after).
Recapture: `uv run python scripts/capture_live_frr_fixtures.py`.

The Gate 3 source-derived provisional fixtures under `tests/fixtures/frr/`
remain untouched and still pass their tests.

## Integration tier

`tests/integration/` (marker: `integration`) is Docker-gated: without a usable
daemon — or with a Docker Engine older than 28.1, which lacks compose
`interface_name` — every test SKIPS with an explicit reason. CI runs
`pytest -m "not integration"`; the tier is local-first and not a required
check anywhere. The tier covers: configured-lab full lifecycle (start → health → real
convergence → config-applied proof → read-only transcript → teardown →
independent zero-resource checks by project label), the full healthy-evidence
matrix on a live lab, and integrity/parseability of the committed live fixture
set. Project names derive deterministically from unique per-run ids.

## Cleanup proof

Every live path (probes, capture script, integration tests) tears down with
`compose down --volumes --remove-orphans` and then verifies zero containers and
zero networks carrying the project label — enforced inside `stop()` AND
re-checked independently host-side in the lifecycle test. All runs during this
step ended with zero VerifiedNet resources on the host.

## Limitations

- Single reference host (macOS/arm64/Docker Desktop) so far; the capture set is
  explicitly platform-labeled and the manifest records the environment.
- `health_check()` and convergence prove control-plane health, not data-plane
  throughput; reachability is 3 sequential single-packet probes (3/3 rule).
- Docker assigns the widened /29 address on eth1 alongside FRR's /30 (two
  addresses on the link NIC); evidence semantics are unaffected — peering,
  routes, and reachability are asserted directly.
- Convergence defaults (60 s / 1 s / 2 consecutive) are operational defaults,
  to be revisited when the incident orchestrator (Step 3) defines scenario
  timeouts end-to-end.

## Explicit non-actions

No remote-AS mismatch was injected; no mutation command was executed (proven by
transcript checks and the capture manifest statement); no incident record was
built; no orchestrator was implemented; Gate 5 was not started.
