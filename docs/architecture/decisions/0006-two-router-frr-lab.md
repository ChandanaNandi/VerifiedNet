# 0006 — First vertical slice is a minimal two-router FRR eBGP lab

**Status:** Accepted (owner decision, Gate 1; validated Gate 2.5 §9)
**Date:** 2026-07-11

## Context

The platform must eventually support several lab backends (routed FRR, EVPN/VXLAN,
SONiC-VS, SR Linux). For the first end-to-end incident — inject → verify onset → collect
→ restore → verify recovery — a lab is needed that is small enough to run and reason about
deterministically, yet real enough to exercise a genuine control-plane fault.

## Decision

The first vertical slice uses a **minimal two-router FRR eBGP lab** derived from
NeuroNOC's four-router lab: `router_a` (AS 65001) and `router_b` (AS 65002), one
point-to-point link, one eBGP session, and one advertised loopback per side (needed to
prove route restoration both ways). The fault is a remote-AS mismatch injected on
`router_a` only (wrong ASN `65999`). Link addressing is `/30` (not `/29`, which NeuroNOC
only used to park a host gateway; not `/31`, which buys nothing here). `TopologySpec`
carries **explicit `sessions:`** rather than deriving sessions from links, because faults
target a session and derivation is ambiguous for iBGP and multi-session links.

The four-router NeuroNOC topology is retained as a later backend/profile for
blast-radius, multi-hop, and topology-generalization experiments.

## Consequences

- Repeatability and cleanup are fast and easy to assert.
- All topology values (names, ASNs, addresses, the wrong ASN) are scenario/topology
  configuration data, never Python constants.
- The FRR image is pinned by multi-arch digest at Gate 4; compose uses per-run project
  names and never sets `container_name`.

## References

- `../gate1/code_reuse_matrix.md` §10 (Gate 4 lab decision)
- `../gate2/wave_a_file_harvest_plan.md` §6 (lab design)
- `../gate2_5/architecture_validation.md` §9 (topology validation)
