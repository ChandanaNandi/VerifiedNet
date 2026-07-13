# Gate 5.5 — Bounded Scenario Parameterization

**Status:** IMPLEMENTED and live-verified. A small, explicit, immutable scenario
catalog introduces a bounded variation matrix across the four verified fault
families, with deterministic parameter validation and a thin
catalog-case execution API. Additive only: no verifier, artifacts, run index,
composition root, ledger, oracle, transcript, or evidence provider was
redesigned; **no schema changed**; no new mutation shape was introduced; and
the reference (`router_a`) family APIs continue to work unchanged. **No AI
component determines ground truth.**

## Core principle held

Gate 5 is scenario engineering, not dataset generation. The catalog is
deliberately small: *few variations, deeply verified, fully restorable.* There
is no combinatorial explosion, no generator, no random parameters.

## The scenario catalog

`verifiednet.orchestrator.catalog` defines a frozen `ScenarioCase`
(`case_id`, `scenario`, `expected_target`, `description`) and a hand-maintained
static tuple `SCENARIO_CATALOG` of **9 cases** (2-4 per family):

| case_id | family | target | parameter |
|---|---|---|---|
| `ras-ref` | bgp_remote_as_mismatch | router_a | wrong_asn 65999 |
| `ras-rev` | bgp_remote_as_mismatch | router_b | wrong_asn 65998 |
| `ras-alt` | bgp_remote_as_mismatch | router_a | wrong_asn 65123 |
| `nr-ref` | bgp_neighbor_removal | router_a | — |
| `nr-rev` | bgp_neighbor_removal | router_b | — |
| `if-ref` | iface_admin_shutdown | router_a | eth1 (derived) |
| `if-rev` | iface_admin_shutdown | router_b | eth1 (derived) |
| `pf-ref` | bgp_prefix_withdrawal | router_a | 10.255.0.1/32 |
| `pf-rev` | bgp_prefix_withdrawal | router_b | 10.255.0.2/32 |

There is NO plugin discovery, NO reflection, NO YAML/DSL, NO runtime
registration, NO Cartesian auto-generation, and NO randomness. Case `case_id`s
are unique (enforced at module import); adding a case is a reviewed edit to
this one file.

**Why the combinations are limited.** Each case must add unique proof. The
reverse-orientation case per family proves orientation-independence; the
remote-AS `ras-alt` proves the wrong-ASN value is genuinely parameterized. More
combinations (e.g. every value × every node) would multiply runtime without
proving anything the abstractions do not already guarantee — so they are
deliberately excluded.

## Reverse orientation needed no scenario code

The audit confirmed all four scenarios were already orientation-independent:
each derives its peer address, correct remote-AS, peer node, local ASN,
interface, and loopbacks from the topology via `_session_endpoints()` and
`topology.node(target_node)` — there were **zero `router_a` literals** in any
scenario or phase-plan builder. Running a `router_b` case therefore required no
new mutation shape and no code change, only new parameter data. This is the
central architectural result of Gate 5.5.

## Parameter validation (deterministic, visible, never-normalizing)

`validate_scenario_case(case, topology)` runs BEFORE any lab action and raises
`ScenarioValidationError` (never silently normalizes an invalid case into a
valid one). Rules per family:

- **remote-AS mismatch:** `wrong_asn` present, in `[1, 4294967295]`, and not
  equal to the local ASN or the actual peer ASN (both derived from topology).
- **neighbor removal:** target node known, peer_ip derivable, remote-AS
  baseline present.
- **interface shutdown:** the derived interface matches `^eth\d+$` (a real lab
  link interface) — the loopback and free-form names cannot be selected.
- **prefix withdrawal:** the prefix is a well-formed CIDR and equals exactly the
  target node's configured advertised loopback.

Every case also validates target-node membership in the named session (unknown
node or unknown session is rejected). Case data is plain scalars
(`dict[str, str | int]`, schema-enforced) — it can carry no callable and can
inject no command; mutations still flow only through the exact per-family
`MutationCommandShape` allow-list.

## Catalog-case execution API

`run_accepted_case(*, case, ...)` is a thin wrapper: it confirms the case is the
approved catalog instance (an uncatalogued or forged case is refused with
`LiveRunError` before any lab action), validates it against the topology,
resolves the existing `FaultFamilyBinding` via `binding_for_template`, and
dispatches to the existing `run_accepted_incident` path. It adds no new
execution logic, no workflow engine, no dynamic import, and no free-form
command generation. The Gate 4/5.1–5.4 family APIs are untouched.

## Verification

Offline (`ruff`, `mypy` on 74 source files, **527 tests passed**): every catalog
case — including the reverse-orientation `router_b` cases — runs end to end
through the REAL `run_accepted_case` path against a symmetric `CatalogLabSim`
(both routers modeled independently), asserting accepted status, correct
target/peer, model-free GroundTruth, canonical artifacts, and load-through-index;
all 9 cases coexist in ONE verified index; validation rejections are unit-tested.

Live (canonical host): one reverse-orientation case per family
(`ras-rev`, `nr-rev`, `if-rev`, `pf-rev`) executed live through
`run_accepted_case` — see `cross-family-regression.md` and
`gate5-completion-report.md` for the recorded results.
