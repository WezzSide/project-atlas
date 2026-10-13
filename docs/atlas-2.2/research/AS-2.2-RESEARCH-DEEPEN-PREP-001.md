# AS-2.2-RESEARCH-DEEPEN-PREP-001 — Research workspace deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-RESEARCH-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-RESEARCH-001` runtime |
| Tip audited | `d9949530ca023647c6e6c9c76e3ba6864265e8c7` |
| Tree | `d9949530ca023647c6e6c9c76e3ba6864265e8c7` |
| Scope | `docs/atlas-2.2/research/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `knowledge_compiler` / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-1 research workspace PREP **beyond** the base pipeline stubs
already landed under `docs/atlas-2.2/contracts/research/` and
`docs/atlas-2.2/fixtures/research/` (PR [#171](https://github.com/WezzSide/project-atlas/pull/171)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/research/**` for:

- explicit fail-closed forbidden-action vocabulary (hypothesis promotion, silent
  conflict winners, LLM authority, evidence-class mismatch),
- hard invariants and fixture-plan inventory aligned with wave-2 sibling depth,
- negative rehearsal payloads mapped to `THREAT-ROWS.md`,

without dual-owning Ask Atlas 2 deepen (`ask-atlas-2/**`), without shipping
package-data schemas, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base research PREP | `AS-2.2-RESEARCH-001` → `contracts/research/` | Pipeline stubs (peer; do not dual-own) |
| Base fixtures | `fixtures/research/` | Positive chain rehearsal (peer) |
| Base ADR | `docs/adr/ADR-025-research-workspace-prep.md` | Prep boundary (peer) |
| Ask Atlas 2 deepen | `AS-2.2-ASK2-DEEPEN-PREP-001` → `ask-atlas-2/**` | Answer lens deepen (peer; do not dual-own) |
| Conflict UX | `AS-2.2-CONFLICT-UX-PREP-001` | Conflict projection cards for synthesis |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Pipeline sketch (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | Hypothesis≠winner / pack≠authority / LLM≠authority |
| [`DEEPEN-FIXTURE-PLAN.md`](DEEPEN-FIXTURE-PLAN.md) | Deepen fixture family inventory |
| [`contracts/`](contracts/) | Forbidden-action JSON Schema stub (docs-owned) |
| [`fixtures/`](fixtures/) | Negative rehearsal payloads |
| [`adr/ADR-2.2-RESEARCH-001-workspace-deepen-prep.md`](adr/ADR-2.2-RESEARCH-001-workspace-deepen-prep.md) | Deepen boundary ADR |

Base package entry remains [`README.md`](README.md). Index ownership stays with
the 2.2 prep-index lane; this deepen card is the deepen entry.

## Deepen delta vs base research PREP

| Concern | base research (#171) | This deepen PREP |
|---|---|---|
| Pipeline stubs | Seven schemas under `contracts/research/` | Peer reference only |
| Positive fixtures | Chain / pack / answer samples under `fixtures/research/` | Peer reference only |
| Fail-closed ops | Truth-boundary strings in THREAT-ROWS | Forbidden-action vocabulary + negatives |
| Invariants doc | Embedded in ARCHITECTURE | Explicit `INVARIANTS.md` |
| Fixture inventory | `FIXTURE-PLAN.md` base scenarios | `DEEPEN-FIXTURE-PLAN.md` with FX IDs |

## Hard invariants

1. **HYPOTHESIS ≠ LAYER B WINNER** — hypotheses never auto-promote to claims.
2. **CONFLICT RETENTION** — material incompatibilities stay visible; no silent pick.
3. **PACK ≠ AUTHORITY** — evidence packs never write Layer B or stamp winners.
4. **ASK2 DEEPEN ≠ DUAL OWN** — answer lens deepen stays under `ask-atlas-2/`.
5. **LLM ≠ AUTHORITY** — no `llm_authority=true`; no trust scores.
6. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/knowledge_compiler.py` or Core authority
- Not shipped package-data schema promotion
- Not dual-ownership of `ask-atlas-2/**` or `contracts/research/` base stubs
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of `fixtures/research/` positive payloads

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core authority runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base research fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, LLM authority, or Layer B promotion

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
