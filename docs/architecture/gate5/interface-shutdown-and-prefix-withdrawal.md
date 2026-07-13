# Gate 5.3 + 5.4 — Interface Administrative Shutdown and Prefix-Advertisement Withdrawal

**Status:** IMPLEMENTED and live-verified. Two additional verified fault
families, both additive on the frozen Gate 4/5.1/5.2 architecture: no verifier,
artifacts, run index, composition root, ledger, oracle, transcript, or evidence
provider was redesigned; no schema changed; no mutation binary beyond `vtysh`
was permitted. **No AI component determines ground truth** — every truth value
originates from deterministic collectors and the frozen `ClaimVerifier`.

## The mandatory Gate 5.3 probe (control point)

Before any implementation, the approved feasibility probe ran on the canonical
host: a disposable healthy lab, one `interface eth1 / shutdown` on `router_a`
through the EXISTING mutation path, deterministic observation only, immediate
`no shutdown` + `clear bgp`, teardown. **Recorded result:**

```
baseline        : router_a eth1 admin=up  oper=up   bgp=Established ping=true  routes(both)=true
after shutdown  : router_a eth1 admin=DOWN oper=DOWN bgp=Active      ping=false peer-loopback route=false
                  running-config gained a `shutdown` line under interface eth1
recovery (~7s)  : admin=up oper=up bgp=Established ping=true routes restored
                  running-config BYTE-IDENTICAL to baseline (same sha256)
onset elapsed ≈ 4.4s   recovery elapsed ≈ 6.9s   mutation pairs = 3
```

**Decision rule applied:** `administrativeStatus == down` AND
`operationalStatus == down` both held → **FRR-mode is approved and
implemented.** No `ip link` is used; the mutation policy was NOT expanded to
permit any additional binary; ADR/policy expansion remained out of scope.
(A second recorded observation, used to shape the checks: the PEER stayed
`Established` during onset until its hold timer expired — so interface-shutdown
onset is verified TARGET-side only, with peer session/route recovery checked at
RECOVERY after the forced reset resynchronizes both ends.)

## Gate 5.3 — interface administrative shutdown (`iface_admin_shutdown`)

Runtime/interface fault. Mutation on `router_a` only; three exact shapes
(`iface_admin_shutdown_mutation_shapes`, ADR-0005): `iface_shutdown`
(`configure terminal / interface <ethN> / shutdown`), `iface_no_shutdown`
(`… / no shutdown`), and the reused `clear_bgp`. The interface parameter
position accepts only lab link interfaces (`ethN`) — never `lo`, never
free-form names (both denied, unit-tested).

| Phase | Deterministic checks |
|---|---|
| Precondition | `iface_admin_up`, `iface_operational`, `bgp_established`, `reachability_ok`, `route_present(T, P_lo)`; capture baseline `config.sha256` (both nodes) |
| Onset (polled ×2, target-side) | `iface_admin_down`, `iface_oper_down`, `bgp_not_established(T)`, `reachability_fails(T)`, `route_absent(T, P_lo)` |
| Onset invariant | `config_unchanged(P)` |
| Recovery (polled ×2) | `iface_admin_up`, `iface_operational`, `bgp_established`, `reachability_ok` |
| Recovery final | `route_present` both directions; **`config_unchanged(T, baseline)` — byte-identical restore** |

`FaultInjection`: `parameter_name="admin_state"`, `before="up"`,
`after="down"`. Restoration uses `no shutdown` + `clear bgp` forced reset
(`forced_reset_used=True`), matching the probe's ~7 s recovery.

## Gate 5.4 — prefix-advertisement withdrawal (`bgp_prefix_withdrawal`)

Routing-intent fault, intentionally distinct: **the BGP session stays
Established throughout — only one advertised prefix moves.** Mutation on
`router_a` only; two exact shapes (`bgp_prefix_withdrawal_mutation_shapes`):
`withdraw_network` (`configure terminal / router bgp <ASN> /
address-family ipv4 unicast / no network <IPv4/len>`) and `restore_network`
(same with `network`). A new prefix parameter class (`<IPv4>/<len>`, CIDR
required — a bare address is denied). **There is no `clear_bgp` shape for this
family**: the session never resets.

| Phase | Deterministic checks |
|---|---|
| Precondition | `bgp_established(T)`, `reachability_ok(T)`, `route_present(P, T_lo)` (the advertisement must be visible on the peer first) |
| Onset (polled ×2) | `route_absent(P, T_lo)` — affirmative `"false"`; `bgp_established(T)` — **the session-up onset invariant** |
| Onset invariants | `bgp_established(P)`, `route_present(T, P_lo)` (peer's own advert unaffected), `config_unchanged(P)` |
| Recovery (polled ×2) | `route_present(P, T_lo)`, `bgp_established(T)` |
| Recovery final | `reachability_ok(T)`; **`config_unchanged(T, baseline)` — byte-identical restore** |

`FaultInjection`: `parameter_name="network"`,
`before="<prefix> advertised"`, `after="withdrawn"`. Restoration re-advertises
with **NO forced reset** — the first family to exercise
`RestorationMetadata.forced_reset_used=False`; the transcript carries exactly
two mutation pairs (withdraw, restore).

## Shared enablers reused (no redesign)

Both families plug into the Gate 5.1 machinery unchanged: the pure-data
evidence phase plans (`_iface_shutdown_phase_plans`,
`_prefix_withdrawal_phase_plans`), the explicit `FaultFamilyBinding`
(`APPROVED_FAMILY_BINDINGS` now has four entries), and the existing check
factories (`iface_admin_up/down`, `iface_oper_down`, `reachability_fails` were
added in 5.1 precisely for this gate). `run_accepted_incident(binding=…)` runs
all four families through the ONE composition root; a scenario/binding template
mismatch is refused before any lab action. The AST guard's artifacts-forbidden
list gained the two new scenario modules.

## Offline verification

`ruff` clean, `mypy` clean (73 source files), **497 offline tests passed**
(474 baseline + 23 new), 25 live tests collected (skip without Docker). New
coverage: exact shape allow/deny for both families (loopback denied, free-form
iface denied, non-vtysh `ip` binary denied, bare-address prefix denied, no
`clear_bgp` in the prefix family, cross-family command denial); full lifecycle
through the REAL scenario + executor + provider + family plans for each
(`IfaceLabSim`, `PrefixLabSim` — canonical config text, byte-identical restore
proven offline); failure paths (inject fails → `INJECTING`; restore fails →
`RESTORING`; restore-after-restored no-op); composition-root wiring per family;
and an **all-four-families-share-one-index** regression.

## Live verification (canonical host)

The full live integration tier ran on the canonical host (macOS/arm64, Docker
29.1.3 / Compose 2.40.3-desktop.1, pinned
`frrouting/frr:v8.4.1@sha256:0f8c174d…`, FRR 8.4.1_git):

```
25 integration tests passed, 497 deselected, in 117.56s.
```

`test_accepted_live_iface_shutdown_incident` passed end to end through the
composition root: eth1 driven admin-down AND oper-down (the probe decision rule
re-proven as committable verdicts), target BGP lost, `reachability_fails`
observed, peer-loopback route withdrawn, then `no shutdown` + `clear bgp`
recovered link/session/routes with a **byte-identical running-config recovery
verdict** — three mutation pairs, router_a only.
`test_accepted_live_prefix_withdrawal_incident` passed with the session
**Established throughout** (onset invariant), the withdrawn prefix absent on the
peer, all other routes intact, restoration by re-advertisement with **no forced
reset** (exactly two mutation pairs), and byte-identical config recovery.

All five families now pass live in ONE run — Gate 4 accepted, Gate 4 rejected,
neighbor removal, interface shutdown, prefix withdrawal — plus the shared-index
and healthy/evidence/fixture tiers, with **zero regression**. Zero `vnet-*`
containers and zero `vnet-*` networks remained afterwards (independent
host-side checks).

## Explicitly not done

No `ip link` control path and no mutation-policy binary expansion (FRR-mode
sufficed); no scenario parameterization; no additional families; no
datasets/benchmarks; no SLM/RAG/GraphRAG/memory/agents/workflows/outcome
engine/planner/scheduler/CLI/dashboard; no Gate 6. Gate 5.5 (bounded
parameterization) awaits approval.
