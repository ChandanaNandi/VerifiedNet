# Part 1 — Executive Summary

`evpn-vxlan-frr-lab` is a small (471 LOC across scripts/configs/validator, plus a 267-line README), self-contained Docker Compose lab that stands up a 3-node FRRouting leaf-spine fabric (spine1, leaf1, leaf2) plus two `netshoot` tenant hosts (hostA, hostB). It builds an eBGP IPv4 underlay, activates the BGP-EVPN (`l2vpn evpn`) control plane, stretches a single L2 segment (VNI 10010) across the fabric via a hand-built Linux VXLAN data plane, and ships a Python validator that asserts overlay health end-to-end. Three fault-injection scripts break the fabric at three distinct layers (BGP session / VNI mapping / VXLAN interface) and a `restore.sh` returns to baseline.

**Verdict:** This is a genuinely competent, honest, and well-scoped networking lab. The FRR configs are correct and idiomatic (including the non-obvious `next-hop-unchanged` knob every eBGP-EVPN spine needs). The validator actually parses `vtysh ... json` and `iproute2` output correctly — it is not a stub. The single most impressive attribute is the README's intellectual honesty: it explicitly documents that EVPN **type-2** MAC/IP learning is *not* exercised, explains the precise kernel reason (LinuxKit BUM split-horizon behaviour causing a MAC-mobility ping-pong), and states that host traffic rides the BUM-flood path rather than learned-unicast. Very few portfolio labs are this candid about where the demo stops being "real."

**Principal weaknesses:** fixed `sleep` timers instead of convergence polling; the `host_reachability` check is a probabilistic `>=4/15` floor that admits false negatives; everything is hardcoded (three fully-materialized config directories, no templating), so the fabric does not scale past 2 leaves without copy-paste; `nicolaka/netshoot:latest` is unpinned; there is no CI, no test of the validator itself, and no `Makefile`. This is a demonstration/teaching artifact, not production tooling — and it is honest about that.

**Level signal:** Solid **new-grad to L4 (SWE II)** network-systems work; the *honesty and fault-matrix design* push individual dimensions toward senior. See Part 9.

---

# Part 2 — Architecture

### ASCII topology (as actually wired by `docker-compose.yml` + `scripts/setup_vxlan.sh`)

```
        OVERLAY (VNI 10010, one stretched /24: 192.168.10.0/24)
   hostA 192.168.10.10/24                              hostB 192.168.10.20/24
        │ (veth, docker-assigned 169.254.10.10                │ 169.254.20.10
        │  is FLUSHED by setup_vxlan.sh, iface                │  flushed, re-IP'd
        │  re-IP'd to 192.168.10.10 and put in br10)          │  to 192.168.10.20
        │                                                     │
   ┌────┴─────┐                                          ┌────┴─────┐
   │  leaf1   │  br10 <── vni10010 (VXLAN id 10010,      │  leaf2   │  br10 <── vni10010
   │ AS 65001 │            UDP/4789, local=10.0.0.1,     │ AS 65002 │  local=10.0.0.2
   │ lo/VTEP  │            nolearning, neigh_suppress)   │ lo/VTEP  │
   │ 10.0.0.1 │                                          │ 10.0.0.2 │
   └────┬─────┘                                          └────┬─────┘
        │ 10.0.12.2/24 (docker net "leaf1_spine1")            │ 10.0.23.3/24 (net "spine1_leaf2")
        │            eBGP + l2vpn evpn                        │
        │        ┌────────────────────────┐                  │
        └────────┤        spine1          ├──────────────────┘
    10.0.12.3    │       AS 65000         │   10.0.23.2
                 │       lo 10.0.0.3      │
                 │  next-hop-unchanged on │
                 │  l2vpn evpn (NOT a VTEP)│
                 └────────────────────────┘
        UNDERLAY (eBGP IPv4 unicast: leaves advertise /32 loopbacks;
                  spine re-advertises → 10.0.0.1 ↔ 10.0.0.2 reachable)
```

### Components (every container, `docker-compose.yml`)

| Container | Image | Role | Addresses |
|-----------|-------|------|-----------|
| `spine1` | `frrouting/frr:v8.4.1` | Route-reflector-less eBGP spine, EVPN transit only, **not a VTEP** | P2P `10.0.12.3`, `10.0.23.2`; lo `10.0.0.3` (from `frr.conf`) |
| `leaf1` | `frrouting/frr:v8.4.1` | VTEP, AS 65001 | P2P `10.0.12.2`; host net `169.254.10.2`; lo/VTEP `10.0.0.1` |
| `leaf2` | `frrouting/frr:v8.4.1` | VTEP, AS 65002 | P2P `10.0.23.3`; host net `169.254.20.2`; lo/VTEP `10.0.0.2` |
| `hostA` | `nicolaka/netshoot:latest` | tenant, `sleep infinity` | docker `169.254.10.10` → overlay `192.168.10.10/24` |
| `hostB` | `nicolaka/netshoot:latest` | tenant, `sleep infinity` | docker `169.254.20.10` → overlay `192.168.10.20/24` |

### Connections (every docker bridge network)

- `leaf1_spine1` (`10.0.12.0/24`) — leaf1↔spine1 P2P underlay.
- `spine1_leaf2` (`10.0.23.0/24`) — spine1↔leaf2 P2P underlay.
- `hostA_leaf1` (`169.254.10.0/24`) — hostA↔leaf1 access; leaf1's port here is flushed and bridged into `br10`.
- `leaf2_hostB` (`169.254.20.0/24`) — hostB↔leaf2 access; symmetric.

Every leaf runs privileged with `NET_ADMIN, SYS_ADMIN, NET_RAW` and `ip_forward=1` sysctls (needed for kernel bridge/VXLAN manipulation from `setup_vxlan.sh`). Loopback VTEP IPs are configured *inside FRR* (`interface lo / ip address 10.0.0.X/32`), not by compose, so zebra owns them.

**Architectural note:** the loopbacks (`10.0.0.0/24` conceptually, each a `/32`) are the VTEP source addresses AND the only prefixes the underlay carries. The P2P `/24`s are deliberately *not* redistributed — this is why `checks.loopback_reachable` must ping `-I 10.0.0.1` (an unsourced ping would have no return route). That is a correct, real-fabric design choice.

---

# Part 3 — Repository Structure

```
.
├── docker-compose.yml   # ORCHESTRATION: 5 containers, 4 bridge nets, IPAM, caps, sysctls, volume-mounts configs/<node> → /etc/frr
├── configs/             # FRR CONTROL-PLANE config, one dir per node, bind-mounted read/write into the container
│   ├── spine1/{frr.conf,daemons,vtysh.conf}
│   ├── leaf1/{frr.conf,daemons,vtysh.conf}
│   └── leaf2/{frr.conf,daemons,vtysh.conf}
├── scripts/             # NETWORKING / DATA-PLANE + lifecycle logic (bash)
│   ├── up.sh            # compose up → sleep 8 → setup_vxlan.sh → sleep 10
│   ├── down.sh          # compose down -v --remove-orphans
│   ├── setup_vxlan.sh   # THE data-plane builder: br10 + vni10010 on leaves, re-IP hosts
│   ├── restore.sh       # un-shut BGP neighbors + rebuild vni10010 (does NOT re-run setup_vxlan)
│   ├── fault_evpn_neighbor_down.sh  # fault @ BGP layer
│   ├── fault_vni_mismatch.sh        # fault @ VNI-mapping layer
│   └── fault_vxlan_missing.sh       # fault @ VXLAN-iface layer
├── validate/            # VALIDATION logic (python, stdlib only)
│   ├── checks.py            # 7 low-level check fns, each → (ok: bool, detail: str)
│   └── validate_overlay.py # orchestrates the check matrix, prints report, exit 0/1
├── README.md            # 267 lines, unusually honest scope/limitations
├── LICENSE              # MIT, © 2026 Chandana Nandi
└── .gitignore           # __pycache__, *.py[cod]
```

**Ownership map:**
- *Orchestration* = `docker-compose.yml` + `scripts/up.sh`/`down.sh`.
- *Control plane* = `configs/*/frr.conf` (BGP/EVPN) and `configs/*/daemons` (which FRR daemons run: only `zebra`, `bgpd`, `staticd`).
- *Data plane* = `scripts/setup_vxlan.sh` (and `restore.sh` for the rebuild path). **None of the VXLAN/bridge state is in compose or FRR** — it is imperative bash executed via `docker exec`.
- *Validation* = `validate/`.

The `daemons` files are byte-identical across all three nodes (verified: `diff` shows identical). `vtysh.conf` is the single line `service integrated-vtysh-config` everywhere. So the only per-node differentiation is `frr.conf`.

---

# Part 4 — Complete Execution Flow

### `./scripts/up.sh`
1. `set -e; cd "$(dirname "$0")/.."` — anchors to repo root.
2. `docker compose up -d` — creates 4 bridge networks with static IPAM, starts 5 containers. FRR containers boot with `/etc/frr` bind-mounted from `configs/<node>`; the FRR entrypoint reads `daemons` (starts zebra/bgpd/staticd only) and applies `frr.conf`. zebra adds `10.0.0.X/32` to `lo`. bgpd opens eBGP sessions to the P2P peer with `timers 3 9` (fast keepalive/hold). EVPN address family activates but there is **no VXLAN yet**, so no type-3 IMET is emitted.
3. `sleep 8` — blind wait for BGP to reach Established and loopbacks to propagate.
4. `./scripts/setup_vxlan.sh` (see below).
5. `sleep 10` — blind wait for FRR to detect the newly-created VXLAN (via netlink), run `advertise-all-vni`, emit type-3 IMET, exchange it through spine1 (`next-hop-unchanged`), and program the remote HER FDB entry.

### `./scripts/setup_vxlan.sh`
For each leaf via `setup_leaf <leaf> <vtep> <host_prefix>`:
- `find_iface` runs `docker exec <leaf> ip -o -4 addr show`, awk-matches the interface whose IPv4 starts with the host prefix (`169.254.10.` / `169.254.20.`). This is how it discovers the docker-assigned name (e.g. `eth1`) without hardcoding it — a nice robustness touch since Docker interface ordering is nondeterministic.
- Inside one `docker exec ... sh -c` block (with `set -e`): create `br10` (idempotent), bring it up; delete+recreate `vni10010` as `type vxlan id 10010 dstport 4789 local <vtep> nolearning`; enslave to `br10`; `bridge link set dev vni10010 neigh_suppress on learning off`; **flush the host-facing iface's IP**, enslave it to `br10`, `learning off`; then loop `bridge fdb show` to delete any MACs the bridge learned before learning was disabled.
- For each host via `setup_host`: flush the docker IP and assign `192.168.10.10/24` (hostA) / `192.168.10.20/24` (hostB) on the same iface.

Net result: hostA and hostB believe they share one flat `192.168.10.0/24` L2 segment; the fabric stretches it over VXLAN. Because bridge learning is OFF on both ports, the FDB's *only* non-local entry is FRR's HER entry (`00:00:00:00:00:00 dst <peer-VTEP> self`), so **every** host frame (ARP broadcast and subsequent unknown-unicast ICMP) is head-end-replicated to the remote VTEP.

### `./validate/validate_overlay.py`
Runs 7 rows (all via `docker exec`, parsing real output):
1. `containers` — `docker inspect -f {{.State.Running}}` for all 5; must be `true`.
2. `underlay_bgp` — `vtysh -c 'show ip bgp summary json'`, JSON-parse `ipv4Unicast.peers[<peer>].state == "Established"` for leaf1→`10.0.12.3` and leaf2→`10.0.23.2`.
3. `leaf_loopbacks` — `ping -c1 -W2 -I 10.0.0.1 10.0.0.2` (and reverse). Sourced from loopback (correct, per underlay design).
4. `evpn_imet_routes` — `vtysh -c 'show bgp l2vpn evpn json'`, iterate RD blocks, count `[2]`/`[3]` prefixes, and require the peer's IMET via needle `[3]:[0]:[32]:[<peer-vtep>]`. Passes only if the peer's type-3 is present; `type-2` count is informational (expected 0).
5. `vxlan_interfaces` — `ip -d link show vni10010`, string-match `vxlan id 10010 `.
6. `bridge_her_fdb` — `bridge fdb show dev vni10010`, require a line starting `00:00:00:00:00:00` with `dst <peer-vtep>`.
7. `host_reachability` — flush hostA neigh cache, `ping -c15 -W3 192.168.10.20`, parse "`N received`", pass if `N >= 4`.

Exit code `0` iff all rows pass. Every check consumes genuine command output; nothing is faked.

### Fault → restore cycle
- `fault_evpn_neighbor_down.sh`: `vtysh ... neighbor 10.0.12.3 shutdown` on leaf1 → rows 2, 4, 7 fail (and row 3, loopback, because the session carrying the /32 is down).
- `fault_vni_mismatch.sh`: rebuilds leaf2's `vni10010` with `id 99999` → row 5 fails on leaf2 (validator reads the real kernel VNI), row 7 fails; BGP/EVPN stay up. *Correctly diagnosable.*
- `fault_vxlan_missing.sh`: `ip link del vni10010` on leaf2 → row 5 fails, row 6 fails (bridge cmd errors), row 4 leaf1-side fails (leaf2 stops advertising IMET), row 7 fails.
- `restore.sh`: `no ... shutdown` on both leaves' BGP + `rebuild_vni` on both leaves. It deliberately does *not* re-run `setup_vxlan.sh` (documented reason: post-setup the host ports have no IP, so `find_iface`'s prefix match would fail). Sound reasoning.

---

# Part 5 — Networking Concepts

**Genuinely implemented and exercised:**

- **Leaf-spine (Clos) topology** — two leaves, one spine, no leaf-to-leaf link. `docker-compose.yml` networks.
- **eBGP underlay (IPv4 unicast)** — distinct ASNs per node (65000/65001/65002), single session leaf→spine each. `no bgp default ipv4-unicast` + explicit `activate` (idiomatic FRR datacenter style). `network 10.0.0.X/32` advertises loopbacks; eBGP transit through the spine gives leaf↔leaf VTEP reachability. `timers 3 9` for fast convergence. (`configs/*/frr.conf`.)
- **Loopback-as-VTEP** — `interface lo / ip address 10.0.0.X/32`; used both as BGP router-id and VXLAN source.
- **BGP-EVPN control plane (AFI/SAFI L2VPN EVPN, RFC 7432)** — `address-family l2vpn evpn` activated on every session. Leaves run `advertise-all-vni`; spine transits.
- **EVPN Type-3 / Inclusive Multicast Ethernet Tag (IMET) route** — the *headline* of the lab. Each leaf emits one IMET ("I'm a VTEP for VNI 10010 at 10.0.0.X"); the remote leaf imports it. Validated by prefix key `[3]:[0]:[32]:[peer]` in `show bgp l2vpn evpn json`.
- **`next-hop-unchanged` on an eBGP-EVPN spine** — the correct, non-obvious knob. Without it, spine1 (eBGP) would rewrite the EVPN next-hop to itself and, since it is not a VTEP, blackhole the overlay. Present on both spine neighbors (`configs/spine1/frr.conf:25,27`). This single line is the strongest evidence of real EVPN understanding.
- **VXLAN data plane (RFC 7348)** — `type vxlan id 10010 dstport 4789 local <VTEP> nolearning`; UDP/4789; source = loopback.
- **VTEP / VNI / L2VNI** — single L2VNI 10010, no L3VNI.
- **Head-End Replication (HER / ingress replication)** — BUM handled by unicast-replicating to each remote VTEP listed in the FDB's all-zeros entries, programmed by FRR from type-3 IMET (not by static `bridge fdb append`). Validated by `bridge_her_fdb`.
- **Linux bridge + FDB** — traditional (non-vlan-aware) `br10` per leaf; VXLAN + host veth enslaved. FDB inspected directly.
- **`nolearning` / `learning off` / `neigh_suppress on`** — VXLAN and bridge-port knobs; learning deliberately disabled so FRR is the sole FDB authority (prevents the MAC-mobility loop, see below).
- **Unknown-unicast flooding** — because learning is off, host unicast is treated as unknown-unicast and HER-flooded — this is *how* ICMP actually gets across.
- **ARP over an L2 stretch** — hostA ARPs hostB; broadcast → HER → remote bridge → hostB.

**Explicitly NOT implemented / out of scope (README is upfront about all of these):**

- **EVPN Type-2 (MAC/IP advertisement)** — intentionally OFF; validator expects `type-2=0`. The reason given is precise and correct: on the LinuxKit/Docker-Desktop kernel, plain HER lacks BUM split-horizon, so with bridge learning on, both leaves would data-plane-learn the remote MAC locally and fight over the EVPN MAC-mobility sequence number (ping-pong). Disabling learning removes local MACs to advertise → no type-2 → no loop, at the cost of unicast riding the flood path (~1s RTT).
- **EVPN BUM split-horizon** — not enforced by this kernel; the core stated limitation.
- **L3VNI / symmetric IRB / VRFs / inter-subnet routing** — none; single tenant, single subnet.
- **MLAG / EVPN multi-homing / ESI (type-1/type-4)** — none; single-attached hosts.
- **`vlan_tunnel` / vlan-aware bridge** — mentioned as the "proper" fix but not used (decap delivery failed on this kernel).
- **BFD, route-maps, prefix-lists, RD/RT policy** — none; RTs are auto-derived.

Assessment: the implemented/not-implemented boundary is drawn *exactly where a knowledgeable engineer would draw it*, and the README does not overclaim. `neigh_suppress on` is arguably a near-no-op here (no type-2/neighbor data to suppress ARP with, so ARP still floods) — harmless but slightly cargo-culted.

---

# Part 6 — AI Concepts

**N/A — confirmed.** There is no AI/ML anywhere. `grep` across the tree finds no ML libraries, no models, no inference, no data pipelines, no LLM usage. The only Python (`validate/*.py`) is stdlib `subprocess`/`json` string parsing. The README states "There is no AI, no orchestrator, no SONiC" and this is accurate. The validator is deterministic rule-checking, not anomaly detection or learned classification. The `host_reachability` threshold (`>=4/15`) is a hand-tuned statistical floor, not a model. Claim verified true.

---

# Part 7 — Software Engineering

**Folder structure / modularity:** Clean three-way separation (configs / scripts / validate) that mirrors the control-plane / data-plane / validation split. Easy to navigate.

**Abstraction:** `setup_vxlan.sh` factors `find_iface`, `setup_leaf`, `setup_host` — good. `checks.py` cleanly separates the 7 low-level checks from the `validate_overlay.py` orchestrator; the uniform `(ok, detail)` contract and the `_both()` helper are tidy. This is the best-engineered part of the repo.

**Dependency management:** Effectively zero runtime deps — stdlib Python, bash, Docker. Good for reproducibility; there is no `requirements.txt` (none needed). FRR image pinned to `v8.4.1` (good); **`nicolaka/netshoot:latest` is unpinned** (bad — a future netshoot could change `ping`/`ip` behaviour and break `host_can_ping`/`setup_host`).

**Logging:** Bash scripts echo `[stage]` breadcrumbs; the validator prints a column-aligned pass/fail table. Adequate for a lab. No log levels, no timestamps, no `--verbose`.

**Error handling:** Mixed. Python checks defend against `vtysh` non-zero exit and `JSONDecodeError` and truncate stderr — solid. Bash uses `set -e` throughout and `find_iface` guards empty results with a clear stderr message + `return 1`. **Gaps:** the blind `sleep 8` / `sleep 10` in `up.sh` has no readiness verification (a slow host silently produces a failing validator run); `restore.sh` swallows BGP errors with `2>/dev/null || true` (intentional but hides real failures); no timeout/retry wrapper anywhere; `_run` uses `shell=True` with f-string interpolation (safe here — no external input — but a poor habit).

**Testing:** No unit tests for the validator; the validator itself *is* the integration test of the fabric, but nothing tests `checks.py`'s parsers against captured fixtures. No CI (`.github/workflows` absent). No `Makefile`/task runner.

**Config:** FRR config is fully materialized per node (no Jinja/templating), so the three `frr.conf` files duplicate structure; `daemons`/`vtysh.conf` are byte-identical triplicates. Fine at N=3, does not scale.

**Docker/reproducibility:** Compose v2, static IPAM (deterministic addressing), pinned FRR. Reproducible on any Linux kernel with the `vxlan` module; README is honest that data-plane behaviour is kernel-dependent (a reproducibility caveat, disclosed).

**Code quality:** Above average for a lab. Consistent style, meaningful comments that explain *why* (esp. the long comment block in `setup_vxlan.sh` justifying learning-off), no dead code, no TODO/FIXME/HACK markers (grep-verified). `checks.py` lacks an executable bit and shebang but is imported as a module, so that is correct.

**Maintainability/extensibility:** Good within its N=2-leaf scope; poor beyond it. Adding leaf3 requires: a new compose service + network, a new `configs/leaf3/` triplicate, editing spine1's `frr.conf` (new neighbor + activate + next-hop-unchanged ×2 AFs), and new `setup_leaf`/rows in `setup_vxlan.sh`/`validate_overlay.py`. Nothing is data-driven.

---

# Part 8 — Research Quality

Framed as an NSDI/SIGCOMM **testbed/tooling** submission (it is not one, but per the brief):

**Reviewers would praise:**
- Honest, precise scoping — the type-2/split-horizon limitation is stated with the actual kernel mechanism, not hand-waved. Reviewers reward "negative results" and known-limitation transparency.
- A concrete, reproducible artifact (pinned image, static IPAM, one-command bring-up) — good artifact-evaluation hygiene.
- The fault matrix: three faults at three layers producing *distinguishable* validator signatures is a real, if small, contribution toward fault-localization testing.

**Reviewers would criticize:**
- N=3 nodes, single VNI, single tenant — no scale study, no convergence-time measurement, no throughput/latency numbers beyond "~1s RTT anecdote." No experiments, only a boolean health check.
- The data plane is knowingly *not* representative of a real EVPN ASIC (flood-based, not learned-unicast); as a "testbed" it validates control-plane signaling but not forwarding semantics.
- `host_reachability`'s `>=4/15` probabilistic pass is scientifically weak — no distribution characterization, no confidence interval, admitted false negatives. A reviewer would ask for a proper convergence/loss experiment instead of a magic floor.
- No comparison baseline, no automation of the fault→measure→restore loop, no statistical repetition.
- Docker bridges ≠ P2P links; MACs auto-assigned — disclosed, but disqualifying for any performance claim.

**Verdict:** Excellent *teaching/onboarding* artifact; not a research contribution. Would be a fine artifact-track demo companion, not a paper.

---

# Part 9 — Hiring Committee Review

**Would it impress NVIDIA (Cumulus/SONiC), Cisco, Arista, Juniper, Azure/GCP Networking, Meta Infra?** Yes, as a screening/portfolio signal — with caveats.

**Skills demonstrated:**
- Real EVPN/VXLAN control-plane fluency: correct FRR datacenter config, `advertise-all-vni`, and — critically — `next-hop-unchanged` on an eBGP-EVPN spine. That one knob separates people who *ran a tutorial* from people who *understand the eBGP-EVPN next-hop problem*.
- Linux data-plane competence: bridge/VXLAN/FDB/`neigh_suppress`/`nolearning`, netns manipulation via `docker exec`, interface discovery by IP prefix.
- Validator-first / test-in-CI mindset: parsing `vtysh json` and `iproute2` into pass/fail with a clean contract.
- Fault-injection / observability thinking across layers.
- **Engineering honesty** — the willingness to write "here is where my demo stops being EVPN and why" is exactly the trait senior network engineers value in a teammate.

**What's missing for higher levels:** no scale (N=2), no L3VNI/IRB/VRF, no multi-homing, no templating/automation, no CI, no performance data, a probabilistic health check. These are the topics a Cumulus/Arista *senior* interview would immediately probe and this repo does not cover.

**Level:** As a standalone portfolio piece, this reads as **strong new-grad / L3–L4 (SWE II / early network engineer)**. The *depth of understanding* (next-hop-unchanged, the split-horizon/MAC-ping-pong analysis) is above that band and would let the candidate interview well toward senior — but the *artifact's scope* (single VNI, hardcoded, no scale/CI) is not itself senior-level work. It is a great "talk me through this" interview springboard.

---

# Part 10 — Weaknesses (brutally honest)

**Underengineering / missing:**
- **Blind `sleep 8` / `sleep 10`** (`up.sh`) instead of polling BGP state / IMET presence. On a slow or loaded host, `validate_overlay.py` will fail for timing reasons unrelated to correctness. This is the single most impactful defect.
- **Probabilistic `host_reachability` (`>=4/15`)** — the README itself admits "expect the occasional false negative." A validator that self-admits flakiness undermines "validator-first" credibility. The right fix (documented but not done): a vlan-aware bridge with `vlan_tunnel`, or accepting the flood path and measuring loss properly.
- **No CI, no validator unit tests.** `checks.py`'s parsers are never tested against captured `vtysh`/`bridge` fixtures, so an FRR JSON schema change (e.g. across FRR versions) would break silently. The hardcoded pin to `v8.4.1` mitigates but also freezes the lab.
- **`nicolaka/netshoot:latest` unpinned** — reproducibility hole.

**Hardcoding / non-scalability:**
- Every IP, ASN, VNI, VTEP, and interface prefix is a literal, replicated across compose + 3 config dirs + 3 scripts + 2 python files. No single source of truth. Adding a third leaf is a multi-file edit. This is fine for a demo, but it is the opposite of the templated, data-driven config a fabric role expects.
- Three byte-identical `daemons`/`vtysh.conf` files — pure duplication.

**Security (lab-acceptable but noted):**
- All containers `privileged: true` with `SYS_ADMIN`/`NET_ADMIN`; hosts privileged too. Necessary for the kernel manipulation but is the broadest possible grant — a tighter version would drop privileged and keep only the needed caps.
- `subprocess.run(..., shell=True)` with f-string command construction throughout `checks.py` — no injection risk here (all inputs are internal literals) but a bad pattern to copy.

**Fault-script fidelity:**
- Faults are static, one-shot, and fully reversible — good for teaching, but README fault #3's "expect vxlan_interfaces + ping to fail" *understates* the blast radius (it also fails `bridge_her_fdb` and, on leaf1, `evpn_imet_routes`). Minor doc/behaviour drift.
- `fault_vni_mismatch.sh` rebuilds the VXLAN without re-applying `neigh_suppress`/`learning off` (restore.sh does), so the fault also perturbs bridge-port flags — restore papers over it, but the fault is not a *pure* VNI change.

**Not fake:** notably, nothing here is placeholder/stub. The configs are real, the validator really parses real output, the faults really break the fabric. No vaporware.

**Architecture ceiling:** the honestly-disclosed truth that the "EVPN data plane" is really flood-based VXLAN means the lab validates *signaling*, not *forwarding semantics*. That is a fundamental scope limit, not a bug — but it caps the artifact's value.

---

# Part 11 — Reusable Components (for a hypothetical "NetworkGym")

**Directly reusable as test fixtures (little/no change):**
- `validate/checks.py` — the star. Each `(ok, detail)` check parsing `vtysh json` / `iproute2` is a clean, dependency-free fixture. `bgp_underlay_established`, `evpn_imet_from_peer`, `vxlan_iface_exists`, `bridge_fdb_has_her_to`, `loopback_reachable` are reusable verbatim against any FRR fabric. Only `host_can_ping`'s magic floor needs parameterizing.
- The three `fault_*.sh` scripts — atomic, single-layer fault primitives; ideal as a starting fault library. Each is 6–13 lines and self-documenting.
- `configs/*/frr.conf` as reference eBGP-EVPN templates (the spine's `next-hop-unchanged` block especially).

**Needs rewriting before reuse:**
- `setup_vxlan.sh` / `restore.sh` — imperative, hardcoded to 2 leaves and specific prefixes; would need parameterization (VNI, VTEP, prefix as args) and idempotency guarantees.
- `up.sh` — replace blind sleeps with a `wait_for_bgp` / `wait_for_imet` poller before it is trustworthy in CI.
- `docker-compose.yml` — fine as a fixture but would need generation/templating to vary topology.

**Should stay independent (not generalized):**
- The README's Limitations narrative — repo-specific knowledge, not a reusable component.
- The `>=4/15` reachability heuristic — an artifact of *this* kernel's flood path; do not port it.

**Reuse verdict:** `checks.py` + the fault scripts are the crown jewels; harvest those. The bring-up/data-plane scripts are fixture *seeds*, not fixtures.

---

# Part 12 — Portfolio Positioning

**Recommendation: keep it independent** as a focused, self-contained portfolio lab, with two small hardening passes (poll instead of sleep; pin netshoot).

- **Independent (chosen):** its value is precisely that it is one thing done honestly and completely. Its README-as-teaching-document is a portfolio asset in itself. Merging it into a larger repo would dilute the clean narrative.
- **As a library:** No — it is 471 LOC of glue; there is no library surface. Only `checks.py` has library shape, and it is too FRR/lab-specific to publish standalone.
- **As a submodule / test fixture in a bigger "NetworkGym":** Only `validate/checks.py` and `scripts/fault_*.sh` should be vendored there (see Part 11), and ideally as a *copy* that gets parameterized — not the whole repo as a submodule.
- **Resume framing:** lead with "built an eBGP-EVPN/VXLAN fabric with a validator that asserts type-3 IMET → kernel HER FDB programming, plus a layered fault-injection matrix; documented exactly where the Linux data plane diverges from a hardware EVPN ASIC and why." That sentence is interview gold.

---

# Part 13 — Interview Questions (Staff-level, this repo's actual implementation)

**EVPN control plane / next-hop:**
1. `configs/spine1/frr.conf` carries `next-hop-unchanged` on both EVPN neighbors. Walk through exactly what breaks in the data plane if you delete those two lines, packet by packet.
2. Why is `next-hop-unchanged` needed for *eBGP* EVPN but typically not for iBGP-with-RR EVPN? What does the spine do to the next-hop by default in each case?
3. The spine is AS 65000, leaves 65001/65002. Trace how leaf1's IMET reaches leaf2 across the eBGP AS boundary — what happens to the AS_PATH, and could `as-path` loop prevention ever drop a legitimate EVPN route here?
4. `advertise-all-vni` is on the leaves but the spine has no VXLAN. Why does the spine still correctly transit the type-3 route without being a VTEP?
5. The IMET needle is `[3]:[0]:[32]:[10.0.0.2]`. Decode every field of that EVPN type-3 route key. What is the `[0]` and why `[32]`?
6. RTs are auto-derived here (no explicit `route-target import/export`). Given ASNs 65001/65002 and VNI 10010, what auto-RT does each leaf export, and why does import still work across different ASNs?
7. There is no `router bgp ... vni 10010` / `advertise-svi-ip` stanza. What is FRR relying on to map the kernel VXLAN device to an L2VNI for IMET generation?
8. If you added `advertise-all-vni` but forgot to enslave `vni10010` into a bridge, would the IMET still be emitted? Why/why not?

**Type-2 / MAC learning / the split-horizon story:**
9. The README claims enabling bridge learning causes a "MAC-mobility ping-pong." Reconstruct the exact sequence of type-2 advertisements and sequence-number increments that produces the loop.
10. Precisely what is EVPN BUM split-horizon (the "local bias" / source-VTEP exclusion rule), and which RFC 7432 mechanism normally prevents the loop that this lab avoids by disabling learning instead?
11. With learning off, FRR "has no local MACs to advertise." Where in the local FDB would a learned MAC have to appear for FRR to originate a type-2, and what netlink event feeds that?
12. Could you get correct learned-unicast forwarding on this exact kernel by using a vlan-aware bridge with `vlan_tunnel`? What specifically failed (per the README) and how would you debug the decap-delivery problem?
13. `neigh_suppress on` is set but there are no type-2/neighbor entries. Is ARP actually being suppressed in this lab? Justify from first principles — what does `neigh_suppress` do with an empty neighbor table?

**VXLAN data plane:**
14. `vni10010` is `local 10.0.0.1 nolearning`. Trace the full encapsulation of an ARP broadcast from hostA: outer src/dst IP, UDP dst port, VNI, inner frame.
15. The outer source IP is the loopback `10.0.0.1`. Which routing table entry on leaf1 forwards the encapsulated packet toward `10.0.0.2`, and why must the underlay carry `/32`s rather than the P2P `/24`s?
16. `dstport 4789` is set explicitly. What is the Linux default VXLAN dst port and why do EVPN deployments override it to 4789?
17. With `nolearning` on the VXLAN device AND `learning off` on the bridge port, enumerate every FDB entry that exists on `br10` in steady state and who programmed each.
18. Why does host *unicast* ICMP get flooded here rather than unicast-forwarded? At which table lookup does the "unknown-unicast" decision happen?
19. The RTT is ~1s. Give a mechanistic explanation tying the flood path to ARP/neighbor STALE-retry timers.
20. If you set `learning on` on just the VXLAN port (but off on the host port), what breaks and why?

**Underlay / BGP:**
21. `timers 3 9` — implications for convergence and for CPU/keepalive load; would you run these in production and why/why not?
22. `no bgp default ipv4-unicast` then explicit `activate`. What problem does this idiom prevent in a dual-AFI (ipv4 + l2vpn evpn) session?
23. The loopback is configured under `interface lo` in FRR, not by compose. What owns the `10.0.0.1/32` address in the kernel, and what happens to the VTEP if bgpd restarts vs if zebra restarts?
24. Only `zebra`, `bgpd`, `staticd` run (`daemons`). Why is `staticd` enabled if there are no static routes, and is it actually needed?

**Validator correctness (attack the code):**
25. `evpn_imet_from_peer` iterates `for prefix in rd_block` and matches on key strings. FRR's per-RD JSON block also contains a `"rd"` string key. Why does that not cause a false type-2/type-3 count, and what *would* break this parser if FRR changed its JSON schema?
26. `vxlan_iface_exists` string-matches `"vxlan id 10010 "`. Construct an `ip -d link show` output that is a real misconfiguration yet passes this check. How would you harden it?
27. `bridge_fdb_has_her_to` requires the line to start with `00:00:00:00:00:00` and contain `dst <peer>`. If FRR programmed the HER entry with a different all-zeros representation or ordering, would this false-negative? Rewrite it robustly.
28. `host_can_ping` passes at `>=4/15`. Derive the false-positive and false-negative probabilities if the true per-probe success rate is 30%. Is `4` a defensible threshold?
29. `bgp_underlay_established` reads `ipv4Unicast.peers`. On a fault where the session is Established for IPv4 but the EVPN AFI is down, would this check catch it? What check is missing?
30. The validator's `evpn_imet` needle doesn't include the VNI. Show how `fault_vni_mismatch.sh` (VNI 99999) still passes `evpn_imet_routes` — and argue whether that is a bug or correct layering.
31. `_run` uses `shell=True`. Given all inputs are internal literals, is there any real risk? Now suppose container names came from `docker ps` output — what would you change?
32. `loopback_reachable` pings with `-c 1 -W 2`. Why is a single probe risky given the same flood path that makes `host_reachability` flaky, and would leaf-loopback ping actually traverse the flood path?

**Faults / recovery:**
33. `fault_evpn_neighbor_down.sh` shuts only leaf1→spine1. Explain why leaf2's `evpn_imet_routes` check fails as a *consequence*, and how long that takes given `timers 3 9` and route withdrawal.
34. `restore.sh` deliberately does not call `setup_vxlan.sh`. Reconstruct the failure `find_iface` would hit if it did, and propose a design that makes a single idempotent setup path work in both cold-start and restore.
35. `fault_vxlan_missing.sh` deletes `vni10010`. Which FOUR validator rows actually change, and why does README list only two?
36. After `fault_vni_mismatch.sh`, leaf2 advertises an IMET for VNI 99999. What does leaf1 do with that route, and what exactly happens to a VNI-10010-encapsulated frame arriving at leaf2?
37. Design a fault that breaks *only* `bridge_her_fdb` (HER entry absent) while keeping BGP, EVPN routes, and the VXLAN interface all healthy. Is that even reachable given FRR programs the FDB?
38. None of the faults test *asymmetry* (leaf1 healthy, leaf2 broken in a way that only breaks one direction). Design one and predict the exact validator output.

**Systems / scale / design:**
39. To add leaf3 + hostC on a third spine port, list every file you must edit and every line you must add. What single abstraction would collapse that to a data change?
40. The three `daemons` files are byte-identical. Propose a compose/mount change that keeps one copy without breaking per-node `frr.conf`.
41. Replace the `sleep 8`/`sleep 10` in `up.sh` with a correct readiness gate. What exact `vtysh`/`bridge` conditions define "converged" for this lab?
42. Docker bridges are used as P2P links. Name two concrete behaviours (MAC learning, BUM handling) where a docker bridge differs from a real P2P cable and how each could mask or fake a result in this lab.
43. All containers are `privileged`. Produce the minimal `cap_add`/`--sysctl` set that still lets `setup_vxlan.sh` succeed, and identify which single operation forces the broadest capability.
44. The overlay subnet `192.168.10.0/24` exists on no leaf interface. If a customer wanted inter-subnet routing between two VNIs, what would you add (L3VNI, IRB) and where?
45. How would you extend `checks.py` into a CI job that runs on every FRR image bump and catches JSON-schema drift *before* the fabric is even built?

**Deep EVPN correctness:**
46. Is the RD auto-derived per-VNI or per-VTEP in this config, and what is leaf1's actual RD value? Why does RD uniqueness matter even in a single-VNI lab?
47. If both leaves used the *same* ASN (iBGP-style) with the spine as an RR, which config lines in this repo would you delete, add, or change — and would `next-hop-unchanged` still be required?
48. The type-3 route carries a PMSI Tunnel attribute. What tunnel type does FRR signal for ingress replication, and how would the FDB programming differ if it signaled a multicast (PIM) tunnel instead?
49. Explain why `advertise-all-vni` on the leaves plus `next-hop-unchanged` on the spine is *sufficient* for type-3 exchange but *insufficient* for type-2 learned-unicast forwarding even if learning were enabled on a real ASIC — i.e., what additional route/attribute the ASIC path needs.
50. Given the lab's flood-based reality, estimate the control-plane vs data-plane scaling limits: how many VTEPs before HER ingress-replication BUM traffic becomes the bottleneck, and what EVPN feature (assisted replication / multicast underlay) would you introduce first?

---

# Part 14 — Overall Score

| Dimension | Score | One-line justification |
|-----------|:-----:|------------------------|
| **Architecture** | 7/10 | Correct, idiomatic eBGP-EVPN leaf-spine incl. `next-hop-unchanged`; capped by N=2, single-VNI, no L3/IRB and fully hardcoded topology. |
| **Networking** | 8/10 | Genuine, non-trivial EVPN type-3/VXLAN/HER mastery with rare honesty about the type-2 gap; loses points only because forwarding is flood-based, not learned-unicast. |
| **AI** | N/A (1/10) | Intentionally and verifiably zero AI — correct choice for the domain; scored 1 only because the axis demands a number, not as a criticism. |
| **Systems Design** | 6/10 | Clean control/data/validation separation and a real fault matrix, undermined by blind `sleep` gating and zero data-driven config. |
| **Code Quality** | 7/10 | Tidy, commented-for-*why*, defensive JSON parsing, no TODO/dead code; dinged for `shell=True` f-strings and the magic `>=4/15` floor. |
| **Research** | 4/10 | Great honesty and reproducibility but no experiments, no scale, no measurements — a teaching artifact, not a contribution. |
| **Reproducibility** | 7/10 | Pinned FRR, static IPAM, one-command up; hurt by unpinned `netshoot:latest`, sleep-based timing, and kernel-dependent data plane (disclosed). |
| **Open Source Quality** | 6/10 | Excellent honest README + MIT license + .gitignore, but only 2 commits, no CI, no tests, no CONTRIBUTING/issues. |
| **Portfolio Value** | 8/10 | Exactly the kind of focused, honest, deep-dive lab that starts a great fabric/NOS interview conversation. |
| **Resume Value** | 7/10 | Demonstrates real EVPN/VXLAN + Linux data-plane + validator-first skills; scope reads new-grad/L4 rather than senior. |
| **Hiring Impact** | 7/10 | Strong positive signal for DC-networking roles; the next-hop-unchanged + split-horizon reasoning punches above the artifact's size. |

**Bottom line:** A small, honest, technically-correct EVPN/VXLAN lab whose greatest strengths are the depth of its networking correctness and the integrity of its documented limitations, and whose greatest weaknesses are timing fragility, a self-admittedly flaky reachability check, and total hardcoding. Keep it independent; harvest `checks.py` and the fault scripts for any future test-harness project.
