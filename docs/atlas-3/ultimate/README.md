# Ultimate Atlas — Wave ULT-00 reconciliation

| Field | Value |
|---|---|
| Input | "PROJECT ATLAS — ULTIMATE ATLAS MASTER UPDATE PACKAGE" (compiled 2026-09-08) |
| Status | **ULT-00 RECONCILIATION — ACCEPTED by owner directive `D-PROJECT-ATLAS-ULTIMATE-KNOWLEDGE-DEVELOPMENT-CONVERGENCE-001` (2026-09-08); not merged** |
| Reconciled against | `origin/main` = `9972d16448a9cefd3444363b59eae2c0dbed7f20`, tree `2067f1253d411e3bef9fd20ad534b29316b6e953` |
| Package's observed SHA | `e264d599…` — an ancestor of the pin above, 9 commits behind (all docs/seal commits + merge of #737) |
| `MERGE_AUTHORIZATION` | **NOT_GRANTED** |
| `FULL_LIVE_DEMO_READY` | **NO** (unchanged; `docs/atlas-3/PACKAGE-MATURITY.json`) |
| Precedence | Owner directive > re-derived `main` truth > AGENTS/CLAUDE/GOVERNANCE/SECURITY > Atlas 3 canon > the Ultimate package > history |

This directory is the canonical **Ultimate Atlas planning-extension home**
(accepted by the owner directive above). It follows the existing
`docs/atlas-3/` sub-program convention (`llm-memory/`, `chronicle/`). Nothing
here grants merge authority or changes any gate.

Ultimate Atlas is an extension/convergence program under Atlas 3.

```text
It does not create a second Truth Core.
It does not supersede Atlas 3 runtime truth.
It does not wake autonomous systems automatically.
It does not make horizon features implemented.
```

## Files

| File | Role |
|---|---|
| [CURRENT-STATE.md](CURRENT-STATE.md) | Exact `main` pin, CI evidence at that pin, open-PR landscape, and the per-subsystem classification (`LANDED_COMPLETE` … `MISSING`) |
| [REUSE-GAP-MATRIX.md](REUSE-GAP-MATRIX.md) | The §65.2 matrix: subsystem / current implementation / contract / test coverage / maturity / reuse / gap / risk / first package |
| [CONFLICTS.md](CONFLICTS.md) | Where the package is wrong about `main` (`CURRENT MAIN WINS`), where a proposal must stop or be re-shaped (`STOP THAT PROPOSAL`), and wave-order adjustments |
| [FIRST-PACKAGE-ULT-01A.md](FIRST-PACKAGE-ULT-01A.md) | The smallest proposed first implementation package, with the §0.5 header, acceptance, negative controls, and the §0.6 return-packet skeleton. Owner-assigned canonical id: **AT3-103** (`docs/atlas-3/AT3-103.md` once that package lands) |
| [VISION-2031-KNOWLEDGE-DEVELOPMENT-CONVERGENCE.md](VISION-2031-KNOWLEDGE-DEVELOPMENT-CONVERGENCE.md) | **STRATEGIC_HORIZON.** The five-year Knowledge + Development convergence direction (one substrate, many planes; KCP / Agent Governance Control Plane / Development Plane terminology; lanes; field observatory). Additive successor/horizon extension; does not rewrite the Atlas 3 North Star |

Not produced in ULT-00 (deliberately): `NORTH-STAR-EXTENSION.md`,
`TARGET-ARCHITECTURE.md`, `CONTRACT-MAP.md`, `SECURITY-EXTENSION.md`,
`EVAL-STRATEGY.md`. Those are wave documents, and writing them before the
owner accepts this reconciliation would encode the package's target as truth
ahead of the evidence.

## Owner decisions recorded (directive of 2026-09-08)

| Decision | Value |
|---|---|
| ULT-00 reconciliation | ACCEPTED; wave-order adjustments ACCEPTED (Capability Broker precedes Agent Genome) |
| ULT-01a canonical id | **AT3-103** — Universal execution identity + evidence attestation + proof v2 foundation |
| Open atlas-dag stack #720–#739 | `COMPOSE_AS_PRIOR_ART`; do not wholesale merge or supersede; every harvested concept records SOURCE_PR / LANDED_EQUIVALENT / CONTRACT_REUSED / CONCEPT_REUSED / CODE_REUSED / AUTHORITY_TRANSFERRED = NO / TESTS_RECREATED |
| Documentation home | `docs/atlas-3/ultimate/` on a dedicated docs branch off `origin/main` (not the portability branch) |

## Honesty stamps (carried unchanged)

```text
PREP != IMPLEMENTED
DEMO != RELEASE
UI != CANONICAL TRUTH
MODEL OUTPUT != AUTHORITY
GRAPH != AUTHORITY
PROMOTE_ELIGIBLE != MERGED
OPEN_PR != LANDED
CAPABILITY AVAILABILITY != CAPABILITY GRANT
VERIFIER RESULT != MERGE AUTHORITY
MERGE_AUTHORIZATION = NOT_GRANTED
```

## ULT-00 return packet

```text
CURRENT_MAIN                  = 9972d16448a9cefd3444363b59eae2c0dbed7f20
CURRENT_TREE                  = 2067f1253d411e3bef9fd20ad534b29316b6e953
ULTIMATE_RECONCILIATION       = COMPLETE (documentary); runtime figures cite exact-head CI, not a local re-run
EXISTING_CAPABILITIES_REUSED  = see REUSE-GAP-MATRIX.md "REUSE" column (34 landed modules/contracts named)
DUPLICATE_ENGINES_PROPOSED    = 0
TOP_GAPS                      = (1) no execution identity / no object-bound, hashed proof evidence
                                (2) no program intelligence (declared graphs only; no symbol extraction anywhere)
                                (3) Start budget is characters labelled as tokens; drops provenance; truncation == UNKNOWN
                                (4) three unlinked capability models; no grant/token/scope/expiry in atlas3
                                (5) no critic/breaker/verifier-mesh roles in src/ (open PRs prototype them in scripts/)
FIRST_IMPLEMENTATION_PACKAGE  = ULT-01a — ExecutionIdentity v1 + typed EvidenceAttestation + object-bound proof v2 (compat with proof v1)
CERTIFIED_SURFACES_TOUCHED    = NO
OWNER_GATES_REQUIRED          = (a) accept this reconciliation and the ULT wave-order adjustments in CONFLICTS.md
                                (b) assign a canonical package id (AT3-05x is exhausted; AT3-13x is free)
                                (c) decide the relationship to the open atlas-dag series #720–#739 (compose vs. supersede)
                                (d) no certified-surface exception is needed for ULT-01a
MERGE_AUTHORIZATION           = NOT_GRANTED
```
