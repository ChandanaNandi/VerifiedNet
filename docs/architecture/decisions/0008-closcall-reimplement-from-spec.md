# 0008 — ClosCall-derived behavior reimplemented from specification

**Status:** Accepted (Gate 0 licensing posture; applied in Gate 3)
**Date:** 2026-07-11

## Context

ClosCall is the strongest source of several primitives VerifiedNet needs — the fault
ledger, claim verification, manifest hashing, structured logging, and the deterministic
topology/IPAM model. But at the Gate 0 pinned commit, ClosCall has **no published
license**. The author owns the copyright and may reuse her own code, but VerifiedNet is
intended for public release, and copying protected expression from an unlicensed repo
would leave a provenance gap for any third party.

## Decision

For every ClosCall-derived component, **reimplement from specification** rather than copy
source. The Gate 2 file-harvest appendices captured the required *behavior* (ledger phase
semantics, verdict logic, hash discipline, log fields, address math); VerifiedNet's
implementations were written against those specifications, not by pasting ClosCall code.
The provenance register records each as "reimplemented from specification (closcall
license unresolved)". Public redistribution of anything claiming ClosCall *expression*
stays blocked until ClosCall publishes a license (recommended: Apache-2.0, matching
VerifiedNet's own outbound license).

MIT-licensed sources (NeuroNOC, sonic-troubleshooting-agent, evpn-vxlan-frr-lab) were
adapted directly ("copy with modifications") with attribution in `NOTICE`.

## Consequences

- VerifiedNet carries a clean, defensible provenance trail for public release.
- The reimplementations were free to improve on the originals (e.g. content-derived
  evidence ids, exact phase guards) rather than inherit their quirks.
- One open action remains outside this repo: publish a license on ClosCall.

## References

- `../gate0/license_inventory.md`
- `../provenance/wave_a_provenance.md` (rows marked "reimplemented from specification")
- `../../NOTICE`
