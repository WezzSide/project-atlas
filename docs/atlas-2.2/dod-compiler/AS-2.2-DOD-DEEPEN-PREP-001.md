# AS-2.2-DOD-DEEPEN-PREP-001 — Definition-of-Done compiler deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-DOD-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-DOD-COMPILER-001` runtime |
| Tip audited | `d9949530ca023647c6e6c9c76e3ba6864265e8c7` |
| Tree | `d9949530ca023647c6e6c9c76e3ba6864265e8c7` |
| Scope | `docs/atlas-2.2/dod-compiler/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| Core authority / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |
| Evidence root | `D:\project-atlas-orphans\atlas-2.1-productionization-001\` |

## Purpose

Deepen the wave-1 DoD compiler PREP **beyond** the base chain stubs already
landed under `docs/atlas-2.2/contracts/dod-compiler/` and
`docs/atlas-2.2/fixtures/dod-compiler/` (PR [#170](https://github.com/WezzSide/project-atlas/pull/170)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/dod-compiler/**` for:

- explicit fail-closed forbidden-action vocabulary (Layer B promotion, LLM
  authority, fixture-as-pilot, invented PASS),
- hard invariants and fixture-plan inventory aligned with wave-2 sibling depth,
- negative rehearsal payloads and the missing FX-2.2-DOD-004 proof shape,

without reopening 2.1 release gates, without shipping package-data schemas, and
without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base DoD PREP | `AS-2.2-DOD-COMPILER-001` → `contracts/dod-compiler/` | Goal→proof chain stubs (peer; do not dual-own) |
| Base fixtures | `fixtures/dod-compiler/` | PASS / INCOMPLETE / FAIL(class) (peer) |
| Base ADR | `adr/ADR-2.2-DOD-001-dod-compiler-prep.md` | Prep boundary (peer) |
| Threat rows | `dod-compiler/THREAT-ROWS.md` | ADV catalog sketch (peer) |
| KCI | AS-2.0-KCI-001 | Optional compile-request consumer |
| Explain receipts | AS-EXPLAIN-001 | Evidence pointer substrate |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Pipeline sketch (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | Proof≠authority / evidence class / no invented PASS |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Deepen fixture family inventory |
| [`contracts/`](contracts/) | Forbidden-action JSON Schema stub (docs-owned) |
| [`fixtures/`](fixtures/) | Negative rehearsal payloads |
| [`adr/ADR-2.2-DOD-002-dod-compiler-deepen-prep.md`](adr/ADR-2.2-DOD-002-dod-compiler-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`README.md`](README.md) and
[`../AS-2.2-DOD-COMPILER-001.md`](../AS-2.2-DOD-COMPILER-001.md). Index
ownership stays with the 2.2 prep-index lane; this deepen card is the deepen
entry.

## Deepen delta vs base DoD PREP

| Concern | base DoD (#170) | This deepen PREP |
|---|---|---|
| Chain stubs | Six schemas under `contracts/dod-compiler/` | Peer reference only |
| Positive / class fixtures | PASS / INCOMPLETE / FAIL(class) | Peer reference only |
| FX-2.2-DOD-004 | Listed in FIXTURE-PLAN only | `expected-proof-fail-unknown-criterion.json` |
| Fail-closed ops | THREAT-ROWS strings | Forbidden-action vocabulary + negatives |
| Invariants doc | Embedded in ARCHITECTURE | Explicit `INVARIANTS.md` |
| Fixture inventory | `fixtures/dod-compiler/README.md` table | Deepen `FIXTURE-PLAN.md` with FX IDs |

## Hard invariants

1. **PROOF ≠ LAYER B AUTHORITY** — proof never promotes claims / concepts.
2. **EVIDENCE CLASS MATCH** — fixture receipts cannot satisfy `authentic_pilot`.
3. **NO INVENTED PASS** — missing evidence ⇒ INCOMPLETE; never silent PASS.
4. **LLM ≠ AUTHORITY** — model prose never satisfies a criterion.
5. **FIXTURE ≠ RELEASE CERT** — fixture PASS ≠ 2.1 release / authentic PILOT.
6. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/knowledge_compiler.py` or Core authority
- Not shipped package-data schema promotion
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of `contracts/dod-compiler/` or `fixtures/dod-compiler/` base stubs

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core authority runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base DoD fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, LLM authority, or Layer B promotion

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
