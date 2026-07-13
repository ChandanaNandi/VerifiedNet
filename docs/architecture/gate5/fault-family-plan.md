# Gate 5.0 — Fault-Family Plan, Feasibility Audit, and Proof Matrix

**Status:** PLAN COMPLETE AND EXECUTED. Gates 5.1–5.7 implemented every approved
family and the bounded scenario matrix; see `gate5-completion-report.md` for the
final acceptance matrix and live results. The plan below is retained as the
historical Gate 5.0 record.

**Original status (Gate 5.0):** PLANNING ONLY. No fault was implemented, no production code changed,
no mutation injected, no live incident launched. Every conclusion below is
derived from repository evidence at baseline `c1df6d3`
(`v0.4-gate4-complete`): source, tests, ADRs, Gate 4 documents, and the
captured live fixture set `tests/fixtures/frr/live/frr-8.4.1-linux-arm64`
(FRR 8.4.1_git, macOS/arm64 canonical host). Where a question cannot be
answered without executing a mutation, it is marked as a **feasibility probe**
belonging to the implementation substep — never assumed.

## 1. Executive recommendation

Gate 5 should implement, in order: **(1)** the existing BGP remote-AS mismatch
(reference, already live-verified), **(2)** BGP neighbor removal
(`APPROVE_FOR_GATE_5`), **(3)** interface administrative shutdown
(`APPROVE_AFTER_FEASIBILITY_PROBE` — control-point ownership must be proven
before the family is frozen), **(4)** BGP prefix-advertisement removal
(`APPROVE_FOR_GATE_5`). Incorrect-neighbor-IP is `REJECT`ed (no new truth
pattern); route-filtering/prefix-list faults are `DEFER_TO_LATER_GATE`.
The four families cover four distinct truth patterns: wrong value, missing
object, runtime/interface failure, and routing-intent failure with a healthy
session. The current lifecycle, ledger, verifier, policy, artifact, and index
layers support all of them **without redesign**; the two genuine gaps are (a)
the live evidence provider's phase plans are remote-AS-specific and must become
per-family data, and (b) the orchestrator's accepted-run entry point hardcodes
the remote-AS scenario and must accept an explicit per-family binding. Both are
parameterizations, not new architecture.

## 2. Current Gate 4 baseline (verified this session)

Repository at `c1df6d3` = `origin/main` = `v0.4-gate4-complete^{commit}`;
working tree clean; Gate 3 tag at `7d27463`; identity
`Chandana Nandi <119757091+ChandanaNandi@users.noreply.github.com>`.
Offline gate re-run on the canonical host: `uv sync --locked` OK, `ruff` clean,
`mypy` clean (69 source files), **440 passed, 22 deselected** (live tier not
run — no mutation permitted in Gate 5.0, and no feasibility question required
it). Zero `vnet-*` Docker containers/networks exist.

Foundation facts that the plan builds on (all from source/fixtures):

- **Mutation policy** (`runtime/policy.py`, ADR-0005): mutations must match a
  named, complete `MutationCommandShape` — exact command count, exact order,
  `re.fullmatch` per position; parameters only in named positions (`_ASN`,
  `_IPV4`). The only allowed mutation binary anywhere is `vtysh`
  (`backend.build_mutation_adapter` passes
  `allowed_binaries=frozenset({"vtysh"})`).
- **Policy finding (security-relevant):** `MutationCommandPolicy.check` applies
  shape validation **only when `argv[0] == "vtysh"`**. Any other allowed binary
  would be checked only for allow-listed name and shell metacharacters — no
  shape validation. Today this is unreachable (only `vtysh` is ever allowed),
  but it means **adding a non-vtysh mutation binary (e.g. `ip`) requires
  extending the policy to shape-check every binary first**. This constrains
  Candidate B (below).
- **Rendered baseline config** (`labs/frr/render.py`): each node gets
  `no bgp default ipv4-unicast`, `no bgp ebgp-requires-policy`, one
  `neighbor <ip> remote-as <asn>`, and an `address-family ipv4 unicast` block
  with `neighbor <ip> activate` and `network <loopback>`. Because default
  ipv4-unicast is off, **a re-created neighbor is not activated until
  `neighbor <ip> activate` is re-issued** — restoration for neighbor removal is
  a multi-command sequence, not a single line.
- **FRR serialization is canonical** (live fixture
  `router_a_running_config.txt`): FRR re-orders the running config relative to
  the rendered input (`no bgp ebgp-requires-policy` before
  `no bgp default ipv4-unicast`; `network` before `neighbor … activate` inside
  the address-family). All `config.sha256` baselines are captured from live
  `show running-config`, i.e. from FRR's canonical serialization — so
  byte-identical restore is plausible whenever the same logical config is
  reached again, and Gate 4 proved it live for the remote-AS revert (baseline
  vs recovery `config.sha256` equality on both nodes, Steps 3–5).
- **Collectors** already normalize: `bgp.peer.<ip>.state` /
  `bgp.peer.<ip>.remote_as` / `bgp.local_as` (peer entries only when present),
  `iface.<name>.admin` **and** `iface.<name>.oper` (both states; live fixture
  confirms `administrativeStatus`/`operationalStatus` for `eth1`),
  `route.<prefix>.present` / `route.<prefix>.protocols` (requested-but-absent
  is affirmative `"false"` evidence, the rejected-path pattern),
  `config.sha256`, `ping.<ip>.all_success` / `success_count` / `probe_count`.
  A failed ping is **evidence, never an exception** — an interface-down onset
  cannot crash collection.
- **Verifier** (`ClaimVerifier`): metric-keyed, target-blind; a metric absent
  from all records yields `INSUFFICIENT` (not committable, not FAIL). Therefore
  "the peer entry disappeared from the BGP summary" is **not provable with
  existing checks alone** — absence must be turned into an affirmative
  observation (Candidate A requirement).
- **Ledger** (`faults/ledger.py`): phases and legal transitions are
  family-agnostic; `INJECTING → RESTORING` and `INJECTED → RESTORING` recovery
  edges exist; `restore()` idempotency (safe no-op after `RESTORED`) and
  `inject()` double-call refusal are contract-tested.
- **Compose caps** (`render.py`, ADR-0015): every service gets
  `cap_add: [NET_ADMIN, SYS_ADMIN]` — kernel interface control is
  capability-feasible in-container.
- **Environment persistence:** `/etc/frr/frr.conf` is delivered read-only
  (0444) and `write`/`copy` are forbidden vtysh tokens; a mutated running
  config can never persist across a container restart — restart implies
  reversion to the rendered baseline for every candidate below.

(Note: the task list referenced `tests/integration/test_frr_run_index.py`; the
actual file is `tests/integration/test_frr_shared_run_index.py` — read in
full.)

## 3. Candidate-family evaluation

### Reference — BGP remote-AS mismatch (implemented, live-verified)

Wrong-value fault on one session endpoint: session drops (`Idle`), the
configured wrong value is readable from `show ip bgp summary json` even while
down (Gate 4 Step 3 verified live), restore + `clear bgp` forced reset,
byte-identical config recovery proven. This family is the comparison baseline
and its lifecycle shape (poll-twice onset/recovery, ledger discipline,
write-ahead mutation transcripts) is the template all candidates reuse.

### Candidate A — BGP neighbor removal → `APPROVE_FOR_GATE_5`

Conceptual mutation `router bgp 65001; no neighbor 172.30.0.2` (grammar to be
pinned in the family's first live step, as remote-AS grammar was). Evidence for
approval: the command family is the exact inverse of the rendered baseline
idiom the repo already generates and mutates; every command is `vtysh`
(no policy-model change); every onset/recovery fact has a deterministic truth
source. FRR neighbor removal deletes the whole peer object **including its
address-family activation**, so:

- **Restore sequence** (multi-command, one new shape):
  `configure terminal / router bgp <ASN> / neighbor <IP> remote-as <ASN> /
  address-family ipv4 unicast / neighbor <IP> activate`, then the existing
  `clear bgp <IP>` forced reset. Without the `activate` step the session would
  re-establish but exchange no IPv4 routes (`no bgp default ipv4-unicast`) —
  recovery checks (routes present both sides) would loudly catch a wrong
  restore, which is exactly the desired failure mode.
- **Absence proof (new, required):** after removal the peer vanishes from
  `ipv4Unicast.peers`, so `bgp.peer.<ip>.state` is absent →
  `INSUFFICIENT`, not `FAIL`. The collector must emit an affirmative
  observation. Smallest change, mirroring `RoutePresenceCollector`'s
  requested-prefix discipline: an optional `expected_peers` parameter on
  `BgpSummaryCollector` emitting `bgp.peer.<ip>.present` = `"true"|"false"`
  for each requested peer (additive; existing behavior unchanged when the
  parameter is omitted). New check factories `bgp_peer_present` /
  `bgp_peer_absent` over that metric.
- **Peer-side onset corroboration** needs no new machinery: `router_b` still
  has its neighbor configured, so the existing `bgp_not_established` check
  applies on the peer, and `route_absent` proves withdrawal on both sides.

### Candidate B — Interface administrative shutdown → `APPROVE_AFTER_FEASIBILITY_PROBE`

The control point is genuinely unresolved from repository evidence, exactly as
suspected. The two candidate mechanisms:

1. **FRR configuration:** `configure terminal / interface eth1 / shutdown`.
   FRR's zebra is expected to drive the kernel link admin-down via netlink
   (it holds `NET_ADMIN`), which the interface collector would observe as
   `iface.eth1.admin = down` and `iface.eth1.oper = down`. But **nothing in
   this repository proves that FRR 8.4.1 in this pinned container image
   actually changes the Linux link state** — the live fixtures were captured
   on a healthy lab only. This is a runtime question, not a grammar question.
2. **Linux interface control:** `ip link set eth1 down`. Capability-feasible
   (`NET_ADMIN` is granted) **but architecturally expensive**: it requires
   (a) proving `ip` exists in the pinned image, (b) adding a non-vtysh binary
   to the mutation allow-list, and (c) — per the policy finding in §2 —
   **first extending `MutationCommandPolicy` to shape-check every binary**,
   because today only `vtysh` argv gets shape validation. Without (c), adding
   `ip` would create an unshaped mutation channel. That is a security-boundary
   change requiring its own tests and ADR.

**Decision:** approve only after a bounded probe, executed as the FIRST action
of the family's implementation substep (Gate 5.3), on a disposable lab:
apply `interface eth1; shutdown` on `router_a`; observe
`show interface json` (`administrativeStatus`, `operationalStatus`),
`show ip bgp summary json` on both nodes, `show running-config` (does a
`shutdown` line appear); then `no shutdown` and observe recovery + config
byte-comparison vs baseline; then teardown. Decision rule: if FRR-mode drives
**both** admin and oper to `down` and the BGP session to a down state, adopt
FRR-mode (one new vtysh shape pair, no policy-model change, config-fault
semantics: the `shutdown` line appears in running-config and leaves on
restore). If FRR-mode changes only FRR's view and not the kernel link, either
adopt ip-link-mode **after** the policy hardening described above (making it a
pure runtime-state fault whose invariant is `config_unchanged` on the target
**throughout the fault** — a genuinely distinct proof), or stop and report.
The probe itself follows the full lifecycle discipline (transcripted, restored,
torn down) — it is a Gate 5.3 mutation, not a Gate 5.0 action.

Container-restart semantics (both modes): the read-only rendered config has no
`shutdown` line and `write` is forbidden, so a restart reverts the fault; the
backend's leftover-detection and zero-resource teardown are unaffected.

### Candidate C — BGP prefix-advertisement removal → `APPROVE_FOR_GATE_5`

Conceptual mutation `router bgp 65001 / address-family ipv4 unicast /
no network 10.255.0.1/32` on the target; restore re-issues
`network 10.255.0.1/32`. Evidence for approval: the grammar is exactly the
rendered baseline idiom (`render_frr_conf` emits the `network` statement under
the address-family); all commands are `vtysh`; and every fact has an existing
truth source. This family's value is its distinct truth pattern: **the BGP
session stays Established on both sides for the entire fault** —
`bgp_established` becomes an *invariant* while `route_absent(peer,
10.255.0.1/32)` proves the withdrawal and `route_present(target, …connected)`
proves locality. No session flap means **no forced reset**:
`RestorationMetadata(forced_reset_used=False)` — already supported by the
schema; recovery is proven by `route_present` returning on the peer plus
config equality. One new mutation shape pair with one new named parameter class
(an IPv4 **prefix** `<IPV4>/<len>`, alongside the existing `_ASN`/`_IPV4`).

### Candidate D — evaluated, not included

- **Incorrect neighbor IP** → `REJECT`. Truth pattern is "wrong value on a
  session endpoint → session down" — identical in kind to remote-AS mismatch
  (same onset checks, same restore shape family, same truth source). It adds a
  parameter variation, not a new truth pattern; the bounded parameter matrix
  (Gate 5.5) is the right place for value variations, and even there
  remote-AS values cover it.
- **Route filtering / prefix-list fault** → `DEFER_TO_LATER_GATE`. It IS a
  genuinely distinct pattern (policy-object fault: routes filtered while
  session healthy and config intent present), but it requires creating new
  config objects (`ip prefix-list`, `route-map`, neighbor attachment), a
  multi-object restoration with ordering constraints, and a much wider mutation
  grammar. Candidate C already provides the "session healthy, route missing"
  observable class for Gate 5; the policy-object class deserves its own gate
  step with its own grammar audit.

## 4. Proof matrix

Legend: metrics/checks named exactly as in `verifiers/checks.py` and the
collectors. `T` = target node (`router_a` in the canonical orientation), `P` =
peer (`router_b`), `T_lo`/`P_lo` = loopbacks (`10.255.0.1/32`/`10.255.0.2/32`),
`peer_ip` = `172.30.0.2` seen from `T`.

### A. BGP neighbor removal (`bgp_neighbor_removal`)

1. **Family:** `bgp_neighbor_removal`.
2. **Taxonomy:** missing object (configuration).
3. **Healthy preconditions:** existing set — `bgp_established(T, peer_ip)`,
   `reachability_ok(T, peer_ip)`, `iface_operational(T, eth1)`,
   `route_present(T, P_lo)`; plus `bgp_peer_present(T, peer_ip)` (new).
4. **Target:** `T` only; `TargetPolicy` allows only `T`.
5. **Mutation:** `vtysh -c "configure terminal" -c "router bgp <ASN>"
   -c "no neighbor <IP>"` (grammar pinned live in 5.2, like Gate 4 Step 3 did
   for remote-AS).
6. **Policy-approved today:** no.
7. **New shapes:** `remove_neighbor` (3 commands) and `restore_neighbor`
   (5 commands: conf t / router bgp / neighbor remote-as / address-family ipv4
   unicast / neighbor activate); reuses `clear_bgp`.
8. **Onset facts:** `bgp.peer.<ip>.present = "false"` on `T` (new metric);
   `bgp.peer.<ip>.state ∈ {Idle,Active,Connect}` on `P` (existing
   `bgp_not_established`); `route.P_lo.present = "false"` on `T` and
   `route.T_lo.present = "false"` on `P` (existing `route_absent`).
9. **Unaffected invariants:** `iface.eth1.oper = up` on both;
   `ping.<link-ip>.all_success = "true"` both directions (link-level
   reachability is session-independent); `config.sha256(P)` unchanged.
10. **Collectors:** `BgpSummaryCollector` (+ `expected_peers`, additive),
    `RoutePresenceCollector`, `InterfaceStateCollector`,
    `ReachabilityCollector`, `RunningConfigCollector`.
11. **Metric keys:** `bgp.peer.<ip>.present` (new), `bgp.peer.<ip>.state`,
    `route.<prefix>.present`, `iface.eth1.oper`, `ping.<ip>.all_success`,
    `config.sha256`.
12. **Checks:** new factories `bgp_peer_present`/`bgp_peer_absent`; existing
    `bgp_not_established`, `route_absent`, `route_present`,
    `iface_operational`, `reachability_ok`, `config_unchanged`,
    `bgp_established`, `remote_as_equals`.
13. **Truth source for the faulty state:** `show ip bgp summary json` peers
    object no longer contains the key (affirmative `present="false"` from the
    requested-peer normalization); corroborated by the peer-side session state
    and route withdrawal.
14. **Routes:** both loopback routes withdrawn during fault; restored after.
15. **Reachability:** link-IP ping unaffected throughout (invariant).
16. **BGP session:** down (from `P`'s view); absent (from `T`'s view).
17. **Config hash:** `T` hash changes at inject (neighbor + activate lines
    leave the canonical serialization), expected byte-identical after restore
    (FRR canonical serialization, single peer — §8 probe pins it);
    `P` hash unchanged throughout.
18. **Restoration:** `restore_neighbor` shape then `clear_bgp` forced reset.
19. **Forced reset:** yes (same rationale as remote-AS: re-establishment
    latency after re-configuration).
20. **Idempotency:** double-inject → `PhaseTransitionError` (ledger,
    unchanged); double-restore → safe no-op after `RESTORED` (unchanged).
    `no neighbor` on an already-removed neighbor would be a nonzero-exit —
    unreachable through the ledger, and loud if ever reached.
21. **Partial failure:** removal command fails → ledger stays `INJECTING`,
    `InjectFailedError`, restore path still open (`INJECTING → RESTORING`);
    restore fails → ledger stays `RESTORING`, `RestoreFailedError`; activate
    omitted/failed → recovery route checks cannot pass → no accepted record.
22. **Ledger:** the standard eight-phase path; no new phases or transitions.
23. **Cleanup ownership:** composition root (`finally`: restore-if-injected,
    `backend.stop()`), unchanged.
24. **Accepted requirements:** `RECOVERY_VERIFIED` + all verdicts committable +
    `GroundTruth` from `FaultInjection`(parameter_name=`neighbor`,
    before=`<ip> remote-as <asn> (activated)` → exact encoding fixed in 5.2;
    before/after are strings, schema unchanged) + persisted artifacts + index.
25. **Rejected-path design:** reuse the existing impossible-route rejected run
    (family-independent); optionally a family-specific deterministic rejection
    (precondition requires `bgp_peer_present` of a peer the topology never
    configures, e.g. an RFC 5737 address) — decided in 5.2, at most one extra.
26. **Artifacts:** standard layout; evidence bundles per phase; transcript
    pairs for remove/restore/clear; run indexed and reload-verified.
27. **Offline tests:** shape allow/deny (exact count/order/params; partial
    prefixes denied); collector `expected_peers` normalization (present,
    absent, malformed); check factories; LabSim-driven lifecycle happy path +
    failure paths (remove fails, restore fails, activate-missing recovery
    timeout); wiring through the orchestrator with the new binding.
28. **Live tests:** one accepted incident end-to-end through the composition
    root; config-equality probe assertions; shared-index coexistence with
    other families (5.6).
29. **Contract/policy changes:** two new named shapes; additive collector
    parameter; new check factories. No schema changes.
30. **Security boundary:** none beyond the new shapes (vtysh-only).
31. **Provenance:** grammar continuity from the existing remote-AS family
    (sonic-troubleshooting-agent lineage already recorded); no new external
    source.
32. **Risks:** restore ordering (activate required — caught by recovery
    checks); serialization drift on re-add (§8 probe; deterministic fallback
    defined); `before_value` encoding must be fixed once and documented.
33. **Status:** `APPROVE_FOR_GATE_5`.
34. **Order:** first new family (Gate 5.2) — closest to the reference family,
    exercises the new binding with the least novelty.

### B. Interface administrative shutdown (`iface_admin_shutdown`)

1. **Family:** `iface_admin_shutdown`.
2. **Taxonomy:** runtime/interface failure (FRR-mode: configuration fault
   *with* runtime effect; ip-link-mode: pure runtime-state fault). The probe
   decides which — this dual nature is exactly why the family is not frozen.
3. **Preconditions:** existing set plus `iface.eth1.admin = up` on `T`
   (new check factory `iface_admin_up` over the existing metric).
4. **Target:** `T` only.
5. **Mutation (FRR-mode, to be probed):** `vtysh -c "configure terminal"
   -c "interface eth1" -c "shutdown"`; restore `… -c "no shutdown"`.
   (ip-link-mode alternative: `ip link set eth1 down|up` — only after policy
   hardening; see 30.)
6. **Policy-approved today:** no.
7. **New shapes:** `iface_shutdown` / `iface_no_shutdown` (3 commands each;
   new named parameter class for the interface name — pinned to the topology
   iface grammar `eth<digit>`, no free-form names).
8. **Onset facts:** `iface.eth1.admin = "down"` AND `iface.eth1.oper = "down"`
   on `T` (both metrics exist today); `bgp.peer.<ip>.state ∈
   {Idle,Active,Connect}` on both nodes; `route.P_lo.present = "false"` on
   `T`; `ping.<link-ip>.all_success = "false"` from `T` (new
   `reachability_fails` factory over the existing metric — failed pings are
   already evidence, never exceptions).
9. **Unaffected invariants:** `P`'s `iface.eth1.oper` may legitimately go down
   (carrier) — NOT asserted as invariant; `config.sha256(P)` unchanged;
   `iface.lo.oper = up` on `T`.
10. **Collectors:** all existing; no collector changes.
11. **Metric keys:** `iface.eth1.admin`, `iface.eth1.oper`,
    `bgp.peer.<ip>.state`, `route.<prefix>.present`,
    `ping.<ip>.all_success`, `config.sha256`.
12. **Checks:** new factories `iface_admin_up`/`iface_admin_down`,
    `iface_oper_down`, `reachability_fails`; existing `iface_operational`,
    `bgp_not_established`, `bgp_established`, `route_absent`/`route_present`,
    `config_unchanged`.
13. **Truth source:** `show interface json` administrative + operational
    status (both normalized today; live fixture confirms field presence).
14. **Routes:** connected + BGP routes over `eth1` withdrawn during fault.
15. **Reachability:** link ping fails during fault (affirmative evidence).
16. **BGP session:** down on both sides during fault.
17. **Config hash:** FRR-mode — `T` hash changes (a `shutdown` line appears
    under `interface eth1`) and must return byte-identical after
    `no shutdown`; ip-link-mode — `T` hash unchanged THROUGHOUT (the
    distinguishing invariant of a pure runtime fault). The probe decides which
    contract this family carries.
18. **Restoration:** `no shutdown` (or `ip link set up`), then poll oper=up,
    session Established, routes present; forced reset decided by probe
    observation (if re-establishment is slow after link-up, reuse
    `clear_bgp`).
19. **Forced reset:** to be observed in the probe; default no, add if needed.
20. **Idempotency:** `shutdown` twice is config-idempotent (FRR-mode);
    ledger prevents double-inject anyway; restore no-op discipline unchanged.
21. **Partial failure:** command fails → standard `INJECTING`/`RESTORING`
    stall + loud error; **link fails to come back oper-up → recovery times
    out, ledger visibly `RESTORED`-not-verified, no accepted record** (the
    highest-risk residue: a lab left with a down link is still torn down by
    `backend.stop()`, and teardown is zero-resource-verified).
22. **Ledger:** standard path, no changes.
23. **Cleanup:** composition root, unchanged; container restart reverts the
    fault in both modes (read-only baseline config, `write` forbidden).
24. **Accepted requirements:** standard; `FaultInjection(parameter_name=
    "admin_state", before="up", after="down")`.
25. **Rejected-path design:** precondition `iface_admin_up` on an interface
    the topology does not define (e.g. `eth9`) yields INSUFFICIENT — NOT a
    valid deterministic rejection (must be FAIL). Correct deterministic
    rejection: require `iface.<real-iface>.admin = "down"` on the healthy lab
    → observed `"up"` → FAIL. Decided in 5.3; at most one.
26. **Artifacts:** standard.
27. **Offline tests:** shape tests; check factories; LabSim gains an
    interface-state dimension (sim answers `show interface json` with
    admin/oper transitions and drops BGP/routes/ping when down) — lifecycle
    happy + failure paths (shutdown fails; link never comes back).
28. **Live tests:** the 5.3 probe (recorded observations first), then one
    accepted incident; fixture capture of `show interface json` in the down
    state for the offline corpus.
29. **Contract/policy changes:** FRR-mode — two shapes + one parameter class.
    ip-link-mode — **blocked until** `MutationCommandPolicy` shape-checks all
    binaries (policy change + tests + ADR).
30. **Security boundary:** FRR-mode none beyond shapes. ip-link-mode: new
    binary in the mutation allow-list = widened mutation surface; the §2
    policy finding makes this a prerequisite hardening, not an option.
31. **Provenance:** FRR interface grammar is standard vtysh; no new source.
32. **Risks:** the control-point question itself (probed); slow/unstable
    link-up recovery (bounded by recovery timeout, loud on failure);
    peer-side oper state flapping observations (not asserted as invariant).
33. **Status:** `APPROVE_AFTER_FEASIBILITY_PROBE` (probe = first action of
    Gate 5.3; decision rule in §3).
34. **Order:** second new family (Gate 5.3) — after A proves the binding, the
    probe result decides FRR-mode vs ip-link-mode vs stop-and-report.

### C. BGP prefix-advertisement removal (`bgp_prefix_withdrawal`)

1. **Family:** `bgp_prefix_withdrawal`.
2. **Taxonomy:** routing-intent failure (session healthy).
3. **Preconditions:** existing set plus `route_present(P, T_lo)` (the
   advertised route must be visible on the peer before withdrawal).
4. **Target:** `T` only.
5. **Mutation:** `vtysh -c "configure terminal" -c "router bgp <ASN>"
   -c "address-family ipv4 unicast" -c "no network <prefix>"`; restore
   re-issues `network <prefix>` (grammar pinned live in 5.4).
6. **Policy-approved today:** no.
7. **New shapes:** `withdraw_network` / `restore_network` (4 commands each;
   new named parameter class `<IPV4>/<0-32>` prefix — the first prefix-typed
   parameter position).
8. **Onset facts:** `route.T_lo.present = "false"` on `P` (existing
   `route_absent`); **invariant** `bgp.peer.*.state = "Established"` on BOTH
   nodes (existing `bgp_established` used as an onset invariant — the novel
   proof of this family); `route.T_lo.present = "true"` on `T` (still
   connected locally).
9. **Unaffected invariants:** session Established both sides; `iface.eth1.oper
   = up`; link reachability `"true"`; `config.sha256(P)` unchanged;
   `route.P_lo.present = "true"` on `T` (peer's advertisement unaffected).
10. **Collectors:** all existing; no changes.
11. **Metric keys:** `route.<prefix>.present`, `route.<prefix>.protocols`
    (peer-side entry should show `bgp` before, absent after),
    `bgp.peer.<ip>.state`, `config.sha256`.
12. **Checks:** all existing (`route_absent`, `route_present`,
    `bgp_established`, `config_unchanged`, `reachability_ok`,
    `iface_operational`).
13. **Truth source:** the peer's `show ip route json` — the withdrawn prefix's
    entry disappears (affirmative `"false"` from requested-prefix
    normalization). Session health from `show ip bgp summary json`.
14. **Routes:** exactly one prefix withdrawn on the peer; everything else
    intact (assert `route.P_lo.present` unchanged on `T` as the
    unrelated-route invariant).
15. **Reachability:** link ping unaffected throughout (invariant).
16. **BGP session:** Established throughout — the family's signature.
17. **Config hash:** `T` hash changes at inject (the `network` line leaves),
    expected byte-identical after restore (canonical serialization, single
    `network` statement; §8 probe); `P` unchanged throughout.
18. **Restoration:** `restore_network`; **no forced reset** (session never
    dropped): `RestorationMetadata(forced_reset_used=False,
    forced_reset_command="")` — schema already supports it; recovery polls
    `route_present(P, T_lo)`.
19. **Forced reset:** no (first family exercising the no-reset path — a real
    coverage gain for `RestorationMetadata`).
20. **Idempotency:** standard ledger discipline; `no network` on an absent
    statement is a nonzero-exit, unreachable through the ledger.
21. **Partial failure:** standard stall semantics; route never reappears →
    recovery timeout, no accepted record.
22. **Ledger:** standard path.
23. **Cleanup:** composition root, unchanged.
24. **Accepted requirements:** standard; `FaultInjection(parameter_name=
    "network", before="10.255.0.1/32 advertised", after="withdrawn")` — exact
    string encoding fixed in 5.4 (schema unchanged).
25. **Rejected-path design:** the existing impossible-route rejection already
    covers the route-presence precondition class; no family-specific rejected
    run needed.
26. **Artifacts:** standard; transcript has exactly two mutation pairs
    (withdraw, restore) — no clear.
27. **Offline tests:** shape tests (prefix parameter class: exact `/len`
    required, no bare IP, no traversal); LabSim route-advertisement dimension;
    lifecycle happy + failure paths; established-throughout invariant test.
28. **Live tests:** one accepted incident; config-equality probe assertions.
29. **Contract/policy changes:** two shapes + prefix parameter class only.
30. **Security boundary:** none beyond shapes (vtysh-only).
31. **Provenance:** standard FRR grammar; no new source.
32. **Risks:** minimal — smallest blast radius of the three; main risk is
    asserting session stability strictly enough (poll-twice both sides).
33. **Status:** `APPROVE_FOR_GATE_5`.
34. **Order:** third new family (Gate 5.4) — most independent, benefits from
    the matured binding.

### D. Rejected/deferred candidates

**Incorrect neighbor IP — `REJECT`** (truth pattern identical to remote-AS:
wrong value → session down; same checks, same restore family; value variation
belongs to the 5.5 parameter matrix if anywhere).
**Prefix-list/route-filtering — `DEFER_TO_LATER_GATE`** (distinct
policy-object pattern, but multi-object create/attach/restore grammar and
ordering constraints deserve a dedicated gate step; Candidate C already covers
the "session healthy, route missing" observable class for Gate 5).

## 5. Feasibility uncertainties (all assigned to substeps)

1. **B control point** — does FRR-mode `shutdown` drive the kernel link?
   (Gate 5.3 probe; decision rule in §3.)
2. **Config byte-identity after remove/re-add** (A: neighbor block; C:
   network statement) — canonical-serialization evidence makes byte-identity
   likely; each family's substep runs a config-equality probe BEFORE freezing
   its recovery check (§8 fallback defined).
3. **Forced-reset need after link-up** (B) — observed in the 5.3 probe.
4. **Exact FRR nonzero-exit behavior** for `no neighbor`/`no network` on
   absent objects — captured (not assumed) during each family's live step;
   only affects failure-path test realism, not design.
5. **`ip` binary availability in the pinned image** — only relevant if
   FRR-mode fails the probe.

## 6. Configuration-equivalence analysis

Gate 4 proved **byte-identical** restore for remote-AS live (value overwrite —
serialization position unchanged). For Gate 5:

- **A (neighbor removal):** restore recreates the same logical objects; FRR
  re-serializes canonically (live fixture evidence: FRR reorders rendered
  input into a canonical form), and with a single neighbor there is exactly
  one serialization slot. **Expected: byte-identical.** Pinned by a 5.2 probe
  comparing baseline vs post-restore `show running-config` bytes. **Fallback
  if drift is observed:** targeted-value recovery proof —
  `remote_as_equals(T, peer_ip, <correct>)` + `bgp_peer_present` + session +
  routes (all deterministic), with `config_unchanged` retained for the PEER
  only. Do not fuzzy-diff; do not normalize by hand unless the drift is shown
  and the normalization rule is exact and tested.
- **B FRR-mode:** `shutdown` line appears/disappears; **expected
  byte-identical** after `no shutdown` (probe-pinned). **B ip-link-mode:**
  config NEVER changes — the family's invariant becomes `config_unchanged(T)`
  at EVERY phase (a stronger statement than equality-after-restore).
- **C (network withdrawal):** single `network` statement, canonical slot —
  **expected byte-identical** (probe-pinned in 5.4; same fallback discipline
  as A with `route_present`-based targeted proof).
- **Rule adopted:** exact hash equality is the default recovery proof wherever
  the probe confirms it; a family may weaken to targeted-value equality ONLY
  with recorded probe evidence of serialization drift, and the weakened check
  set must still prove every restored value deterministically.

## 7. Shared-lifecycle analysis (Step 7 answers)

**Reusable unchanged:** `Ledger`/`LifecyclePhase`/`LEGAL_TRANSITIONS` (all
families use the same eight phases and recovery edges); `poll_until`;
`ClaimVerifier` + `VerificationCheck`/`Predicate`; `MutationExecutor` +
write-ahead transcript + `CommandInvocation` pairing; `TargetPolicy`;
`FaultInjection`/`RestorationMetadata`/`IncidentRecord` schemas (all
family-generic strings/fields); `build_ground_truth` + builders; the whole
artifacts + index layer; `FrrComposeBackend` + convergence; the rejected-run
adapter; `assemble_verified_run` and the manifests.

**Needs parameterization (the only two real gaps):**

- `LiveScenarioEvidenceProvider._plans` hardcodes remote-AS phase plans
  (e.g. ONSET collects routes only for the peer loopback on the target, config
  only on the peer). Families need different plans (C must collect routes on
  the PEER during onset; B needs interface+reachability emphasis). Smallest
  change: accept explicit per-phase `_NodePlan` tuples at construction —
  **pure data, not code**; the current plan becomes the remote-AS default.
- `run_accepted_incident` hardcodes `BgpRemoteAsMismatchScenario`,
  `bgp_remote_as_mutation_shapes()`, `ROOT_CAUSE`, and the generator string.
  Introduce an explicit, frozen **family binding** (a small dataclass): scenario
  factory `(topology, scenario, mutation, ledger, run_ctx, provider, verifier,
  monotonic, sleep) -> FaultScenario`, allowed mutation shapes, evidence phase
  plans, root-cause label, generator name. The orchestrator takes ONE binding
  per call; approved bindings live in an explicit tuple/dict in the faults
  package — **no registration, no discovery, no plugins**. The existing
  `FaultScenario` protocol already gives the orchestrator a family-blind
  lifecycle interface.

**Family-specific (stays per-family):** check sets per phase, command
builders, `FaultInjection` field encodings, restoration semantics
(forced-reset or not), and the family's LabSim dimensions in tests.

**Shared base class?** Not yet. The candidate-shared logic is the
"poll checks twice → append ledger phase or raise" skeleton (~30 lines per
phase in `BgpRemoteAsMismatchScenario`). Extract a small module-level helper
(function, not a class hierarchy) in Gate 5.1 **only if** writing family A
shows the duplication is mechanical; a premature base class would freeze the
wrong seams before three data points exist. A generic fault DSL is premature —
rejected.

**Does anything incorrectly assume remote-AS?** `orchestrator/live_run.py`
(by Gate 4 design — the binding fixes it), the evidence provider's plans
(same fix), and the test-side `LabSim` (test code; grows dimensions per
family). `checks.py`, schemas, ledger, runtime, artifacts are family-clean.

## 8. Gate 5 target scope

Approved set: remote-AS mismatch (existing) + neighbor removal + interface
shutdown (post-probe) + prefix withdrawal. Gate 5 finishes with: one accepted
live incident per approved family; failure-path coverage per family; at least
one deterministic precondition-rejected incident overall (existing
impossible-route run, plus at most one family-specific rejection each for A/B
if adopted in their substeps); complete restoration/recovery; canonical
artifacts and run-index entries for every live run; cross-family isolation
tests; zero orphaned resources. Bounded parameter matrix ONLY after every
family works once: **2–4 scenarios per family maximum** (orientation flip
`router_a`↔`router_b`; a second wrong-AS value; a second withdrawn prefix
where the topology provides one) — no large-scale generation, no new
topologies.

## 9. Gate 5 implementation sequence

- **Gate 5.0 — this plan.** Docs only. Stop: plan committed, CI green.
- **Gate 5.1 — shared enablers (only what §7 proved necessary).**
  Scope: evidence-provider plan parameterization (data-only); orchestrator
  family binding; new check factories (`bgp_peer_present/absent`,
  `iface_admin_up/down`, `iface_oper_down`, `reachability_fails`);
  `BgpSummaryCollector.expected_peers` (additive). Files:
  `labs/frr/scenario_evidence.py`, `orchestrator/live_run.py`,
  `verifiers/checks.py`, `collectors/frr/bgp.py`, unit tests. Offline
  acceptance: full suite green; remote-AS wiring tests unchanged in behavior.
  Live acceptance: existing live tier re-run green (proves zero regression).
  Stop: no new family code. Out of scope: any new mutation shape.
- **Gate 5.2 — BGP neighbor removal.** Probe config byte-identity first;
  shapes `remove_neighbor`/`restore_neighbor`; scenario + binding + LabSim
  dimension + failure paths; one live accepted incident. Stop conditions:
  restore does not re-establish routes; config drift without a deterministic
  fallback; any policy denial ambiguity.
- **Gate 5.3 — interface shutdown.** FIRST: the control-point probe (§3
  decision rule). Then FRR-mode implementation (or the policy-hardening +
  ip-link path with its own ADR, or stop-and-report). Live accepted incident.
- **Gate 5.4 — prefix-advertisement removal.** Shapes with the prefix
  parameter class; no-forced-reset restoration; established-throughout
  invariant; live accepted incident.
- **Gate 5.5 — bounded parameterization.** 2–4 scenarios/family, offline
  matrix tests + one live spot-check per family maximum.
- **Gate 5.6 — cross-family regression + isolation.** All families
  sequentially into ONE shared index; per-run zero-resource proof; repeatable
  verdicts (same scenario twice → same verdict set); tamper-one-run index
  refusal re-proven at scale.
- **Gate 5.7 — closure.** Completion report + acceptance matrix; propose
  (NOT create) `v0.5-gate5-complete`; stop for approval.

Each substep: offline gate green before live; live green before commit; small
commits; stop-and-report on any policy, serialization, or probe surprise.

## 10. Gate 5 acceptance matrix

Gate 5 closes only when ALL hold, each with named evidence: all approved
families implemented; healthy preconditions verified per family; onset proven
deterministically (affirmative evidence, never absence-as-INSUFFICIENT);
unaffected invariants proven per family (incl. session-Established-throughout
for C and config-unchanged-throughout if B lands as ip-link-mode); restoration
and recovery verified (byte-identical config or probe-justified targeted
equality); exact mutation transcripts paired by `command_id` with no unmatched
pending; one accepted incident per family; rejected-path coverage;
canonical artifact directory per live run; run-index verification incl.
tamper refusal; cross-family isolation (shared index, sequential runs, no
leakage); repeatable verdicts; zero containers/networks after every run;
offline CI green; local live integration green; documentation + provenance
complete; **no model in the truth chain**; **no Gate 6 dataset engine**.

## 11. Stop conditions

Stop and report (never work around): baseline drift at any substep start; any
mutation denied ambiguously by policy; the 5.3 probe showing neither FRR-mode
nor a safely-shaped ip-link-mode works; config serialization drift without a
deterministic fallback; recovery timeout patterns suggesting lab instability;
any need to weaken a verifier check without recorded evidence; any temptation
to add discovery/registration/DSL machinery.

## 12. Explicit out-of-scope for Gate 5

New topologies or lab backends (SONiC-VS, EVPN/VXLAN, SR Linux); Batfish/ACL;
plugin discovery; YAML/DSL fault definitions; dynamic agents; event bus;
generic workflow engine; scenario auto-registration; parallel incident runs;
large-scale scenario generation; Gate 6 dataset engine; any model, SLM, RAG,
GraphRAG, memory, or persistent workflow. Ground truth remains model-free
(ADR-0009/0010).

## 13. Recommendation for Gate 5.1

Implement exactly the §7 enablers — evidence-plan parameterization, the
explicit family binding in the orchestrator, the new check factories, and the
additive `expected_peers` collector parameter — with offline tests proving the
remote-AS path is byte-for-byte behaviorally unchanged, then re-run the live
tier once to prove zero regression, commit, and stop. No new mutation shape,
no new family, no policy change in 5.1. The first prompt after approval should
be: "Gate 5.1 — Shared lifecycle enablers only where proven necessary,"
scoped to those four changes with the acceptance criteria from §9.
