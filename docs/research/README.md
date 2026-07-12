# Source-Repository Engineering Audits

Deep, evidence-based engineering audits of the seven source repositories that
informed VerifiedNet's design. Each audit inspected the actual implementation
(not just the README) and followed a 14-part structure: executive summary,
architecture, repository structure, execution flow, networking concepts, AI
concepts, software engineering, research quality, hiring-committee review,
weaknesses, reusable components, portfolio positioning, interview questions, and
scored ratings.

These are **reference material** about the source repos — they are not part of
the VerifiedNet package. They are the basis for the Gate 0–2 migration analysis
under `../architecture/gate0/`, `../architecture/gate1/`, and `../architecture/gate2/`.

| Audit | Source repo | Role in VerifiedNet |
|---|---|---|
| `closcall_AUDIT.md` | closcall | ledger, claims, manifests, topology/IPAM (reimplemented from spec) |
| `neuronoc-network-ops-assistant_AUDIT.md` | neuronoc-network-ops-assistant | command runner, vtysh parsers, AST security scans, FRR idioms |
| `sonic-troubleshooting-agent_AUDIT.md` | sonic-troubleshooting-agent | fault lifecycle, bounded polling, FRR command grammar |
| `sonic-intent-agent_AUDIT.md` | sonic-intent-agent | Batfish verify pattern (Wave B), read-after-write settling |
| `evpn-vxlan-frr-lab_AUDIT.md` | evpn-vxlan-frr-lab | deterministic reachability check (4/15 floor rejected) |
| `sonic-acl-validation-harness_AUDIT.md` | sonic-acl-validation-harness | SAI/ACL validation (Wave B) |
| `constellation_AUDIT.md` | constellation | out of scope (CV project); reference only |

See `../architecture/gate1/code_reuse_matrix.md` and `../architecture/gate2/wave_a_file_harvest_plan.md`
for how these findings were turned into concrete reuse decisions.
