# ATLAS Global Goals G1–G15

| Field | Value |
|---|---|
| Canonical ID | `ATLAS_GLOBAL_GOALS` |
| Version | 1 |
| Directive | `ATLAS-GLOBALIZE-AUTONOMY-2026-10-02` |
| Status | PROPOSED — binding once the owner merges it to `main` |
| Current-state baseline | [baseline/2026-10-02-G1-G15-BASELINE.md](./baseline/2026-10-02-G1-G15-BASELINE.md) |
| Index | [README.md](./README.md) |

These goals describe the operating system Atlas is becoming, meaning *how* Atlas gets
built and run. The *product* direction stays in
[`docs/product/CODER-ALPHA-NORTH-STAR.md`](../product/CODER-ALPHA-NORTH-STAR.md) and
[`docs/atlas-3/NORTH-STAR.md`](../atlas-3/NORTH-STAR.md). The goals do not replace either,
and they open no parallel backlog.

**Identifiers.** Each goal has a stable ID `ATLAS-GOAL-Gnn`. In prose it may be written
"Global Goal Gn". Never write a bare `G-<n>`: that form is a loop grant under
`autonomy/policy.md` §5.

**Goals are not claims.** Stating a goal asserts nothing about current status. Status lives
only in dated, evidence-backed baselines using `PROVEN` / `PARTIAL` / `NOT_PROVEN` /
`UNKNOWN`. A goal is never promoted on aspiration.

| ID | Short name | Goal |
|---|---|---|
| `ATLAS-GOAL-G01` | END_TO_END_AUTONOMY | Atlas can take an authorized goal through planning, execution, verification, repair and an integration-ready state with minimal human relay. |
| `ATLAS-GOAL-G02` | HUMAN_AT_REAL_AUTHORITY_BOUNDARIES | Humans authorize meaningful trust, merge, secret and destructive boundaries, not routine engineering operations. |
| `ATLAS-GOAL-G03` | CURRENT_TRUTH | Atlas reconciles authoritative current state before decisions or mutations. |
| `ATLAS-GOAL-G04` | FAIL_CLOSED_TRUST | Unknown, stale, ambiguous or unauthorized states never silently become trusted. |
| `ATLAS-GOAL-G05` | EXACT_EVIDENCE | Claims are tied to exact revisions, trees, runs, artifacts and identities. |
| `ATLAS-GOAL-G06` | INDEPENDENT_ASSURANCE | Critical success claims are verified independently of the implementer. |
| `ATLAS-GOAL-G07` | SELF_REMEDIATION | Atlas diagnoses and repairs normal implementation defects autonomously within bounded authority. |
| `ATLAS-GOAL-G08` | CONTINUOUS_PROGRESS | Atlas advances independent safe work instead of idling behind one dependency. |
| `ATLAS-GOAL-G09` | SAFE_CONCURRENCY | Distributed workers preserve exactly-once and ownership invariants under real concurrency. |
| `ATLAS-GOAL-G10` | PRODUCTION_PRESERVATION | Existing valid production state remains protected during change. |
| `ATLAS-GOAL-G11` | NO_CERTIFICATION_TRANSFER | Verification of one byte identity does not certify another identity. |
| `ATLAS-GOAL-G12` | DURABLE_KNOWLEDGE | Operational evidence, decisions, contracts and lessons remain reconstructable across sessions and hosts. |
| `ATLAS-GOAL-G13` | REDUCED_OPERATOR_WORK | Atlas progressively removes manual copy/paste, relays, polling and routine coordination from the owner. |
| `ATLAS-GOAL-G14` | MINIMUM_NECESSARY_CONSTRAINT | Use the narrowest authority and restrictions that preserve safety and trust without needlessly suppressing useful autonomy. |
| `ATLAS-GOAL-G15` | TERMINAL_OUTCOME_OR_PRECISE_FRONTIER | Every mission ends at a proven target state or at an exact, evidence-backed frontier naming the remaining boundary. |

## Relationship to the operating contract

| Goal | Primary contract principles ([contract](./ATLAS-GLOBAL-OPERATING-CONTRACT.md)) |
|---|---|
| G01, G08, G13 | OC-A, OC-C, OC-K |
| G02, G14 | OC-B, section 3 (authority boundaries) |
| G03 | OC-E |
| G04 | OC-F |
| G05, G11 | OC-G |
| G06 | OC-H |
| G07 | OC-D |
| G09 | OC-I |
| G10, G12 | OC-J |
| G15 | section 5 (terminal rule) |

## Fleet status is a separate claim

`ATLAS_VPS_FLEET_AUTONOMOUS` is **not** implied by any of these goals reaching `PROVEN` for
the software-development loop. It has its own evidence requirements, listed in
[baseline/2026-10-02-AUTONOMY-FRONTIER.md](./baseline/2026-10-02-AUTONOMY-FRONTIER.md) §4.

## Change control

Only the owner adds, removes or changes the meaning of a goal. IDs are never reused. New
baselines are new dated files; old baselines are never edited except for factual errata,
and each erratum is marked as one.
