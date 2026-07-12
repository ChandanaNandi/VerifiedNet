# 0015 — Live FRR execution requirements: SYS_ADMIN, API config delivery, pinned interface names

**Status:** Accepted (owner decision, Gate 4 Step 2)
**Date:** 2026-07-12

## Context

Gate 4 Step 2 booted the approved two-router FRR 8.4.1 lab against a real Docker
daemon for the first time with its generated configuration. Three offline design
assumptions failed against live reality. Each failure was reproduced, root-caused,
and the fix verified live before being adopted; the owner approved all three
deviations explicitly.

## Decision 1 — `CAP_SYS_ADMIN` is granted to lab containers (reverses Gate 3)

Gate 3 rejected NeuroNOC's `SYS_ADMIN` grant as an unnecessary privilege. Live
evidence disproved that judgment: FRR 8.4.1 `zebra` and `bgpd` request
`cap_net_admin,cap_net_raw,cap_sys_admin` in their permitted capability set during
`privs_init` and abort when `cap_set_proc` fails:

```
privs_init: initial cap_set_proc failed: Operation not permitted
Wanted caps: cap_net_admin,cap_net_raw,cap_sys_admin=p
Failed to start zebra!
Failed to start bgpd!
```

With `cap_add: [NET_ADMIN, SYS_ADMIN]` both daemons start (running as user
`frr`), the integrated `frr.conf` is applied, and the eBGP session establishes.
NeuroNOC's grant was load-bearing, not gratuitous. The requirement is a
compile-time property of the pinned image's daemons, not something a policy layer
can waive.

## Decision 2 — Generated configs are delivered as inline Compose `configs`, not bind mounts

Bind mounts from the repository or any temp directory are DENIED on the reference
host: Docker Desktop file sharing was restricted to a single unrelated folder, and
macOS `mktemp` locations (`/var/folders/…`) are never shared. Compose's
`configs:` element with inline `content:` delivers the generated `daemons` and
per-node `frr.conf` through the Docker API — no host-path sharing at all —
arriving read-only (`0444`, verified `-r--r--r--`) at the paths the pinned image's
entrypoint actually reads (`/etc/frr/daemons`, `/etc/frr/frr.conf`, inspected from
`/usr/lib/frr/docker-start` + `frrcommon.sh`).

The generated files still live in the backend's per-run build directory
(`write_rendered`), and the compose text embeds byte-identical copies of them —
the renderer stays pure and deterministic. This also removes any dependence on
host file-sharing configuration (CI-friendly). No repository-wide or arbitrary
host mount exists; the rendered compose contains no `volumes` at all.

## Decision 3 — The link interface name is pinned with `interface_name`

With a single attached network Docker names the container NIC `eth0`; the
approved topology (ADR 0006) declares `eth1`. Attaching a second network makes
naming order-dependent and creates an accidental parallel L2 path (observed live:
both routers' `/30` addresses landed on the default bridge and traffic crossed
it). The Compose per-attachment `interface_name` option (supported by the
reference host's Engine 29.1.3 / Compose 2.40.3) pins the single link network to
`eth1` deterministically — no extra network, no accidental path, topology-faithful
naming. Service `hostname:` is likewise pinned to the node name so live captures
are deterministic (FRR otherwise reports the random container id as hostname).

## Consequences

- The renderer emits `SYS_ADMIN`; the security posture note in
  `labs/frr/render.py` documents why, and regression tests assert both caps and
  the absence of host mounts/ports.
- Lab startup requires Docker Engine/Compose versions recent enough for inline
  `configs.content` and `interface_name` (Compose ≥ 2.32 effectively). Recorded
  in the environment manifest of every live capture.
- ADR 0006's "not /29" note referred to FRR link addressing, which remains /30;
  the Docker IPAM widening (Step 1) and this ADR concern the transport layer
  only.

## References

- `../gate4/healthy-live-lab.md` (live evidence, capture set, cleanup proof)
- `0006-two-router-frr-lab.md` (approved topology)
- `tests/fixtures/frr/live/frr-8.4.1-linux-arm64/manifest.json` (provenance)
