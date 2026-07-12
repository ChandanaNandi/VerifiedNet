# 0005 — Mutation commands matched by exact, named command shapes

**Status:** Accepted (Gate 3 dependency-freeze correction 5)
**Date:** 2026-07-11

## Context

The mutation-command allow-list initially matched vtysh command sequences by prefix
(`startswith`) and allowed a candidate with *fewer* commands than the template. That
permitted partial sequences — for example a lone `configure terminal` — and did not pin
the count, ordering, or the shape of parameter positions. For a component that is the only
one allowed to change device state, "close enough" is unacceptable.

## Decision

Replace prefix templates with **named, complete command shapes**
(`MutationCommandShape`). A candidate is permitted only if it matches exactly one shape:
identical command **count**, identical **ordering**, and each command fully matching
(`re.fullmatch`) its position's pattern. Parameters may vary only in explicitly named
positions (an ASN or an IPv4 address); everything else is literal. The BGP remote-AS
scenario is allowed exactly two shapes — `set_remote_as` (`configure terminal` /
`router bgp <ASN>` / `neighbor <IPv4> remote-as <ASN>`) and `clear_bgp`
(`clear bgp <IPv4>`).

## Consequences

- Partial prefixes, missing leading commands, missing parameters, reordered sequences, and
  extra commands are all denied — each is covered by a dedicated test.
- Adding a new mutation requires adding a named shape, which is an explicit, reviewable act.
- Slightly more rigid than prefix matching, deliberately: the mutation surface is the
  highest-risk path in the system.

## References

- Gate 3 dependency-freeze report (correction 5)
- `../gate3/runtime_security.md` (mutation policy)
- `../provenance/wave_a_provenance.md` row 10
