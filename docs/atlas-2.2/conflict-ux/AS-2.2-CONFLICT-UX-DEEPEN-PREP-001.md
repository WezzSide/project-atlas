# AS-2.2-CONFLICT-UX-DEEPEN-PREP-001 — Conflict UX deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-CONFLICT-UX-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-CONFLICT-UX-001` runtime |
| Tip audited | `b431494dc8860f4f1db3f327c9ccf991699ccfc5` |
| Tree | `26a59cd76bd9df410912b4552ddd907f7a160588` |
| Scope | `docs/atlas-2.2/conflict-ux/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `conflict_projections` / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-1 conflict projection cockpit PREP **beyond** the base stubs
already landed under `docs/atlas-2.2/conflict-ux/` (PR
[#181](https://github.com/WezzSide/project-atlas/pull/181) / base
`AS-2.2-CONFLICT-UX-PREP-001`).

This PREP owns a **unique deepen delta** under `docs/atlas-2.2/conflict-ux/**`
for:

- explicit fail-closed **forbidden-action** vocabulary (release-cert stamp,
  PILOT invent, LLM authority) alongside the base disposition wall,
- deepen fixture-plan inventory aligned with mem-gov / research / DoD peers,
- negative rehearsal payloads for certification / PILOT / LLM authority gaps,

without relocating or dual-owning existing conflict schemas
(`disposition-action`, cockpit / card / queue stubs), without shipping
package-data schemas, and without claiming 2.1 release credit.

**DEMO VERIFIED ≠ release / PILOT.** Fixture cockpit rehearsal and any demo
walkthrough grant **no** `ATLAS_2_1_RELEASE_CERTIFIED`, authentic-estate PILOT
PASS, WEB ACCEPTED, or 2.2 unlock credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base conflict UX PREP | `AS-2.2-CONFLICT-UX-PREP-001` → `conflict-ux/` | Cockpit / disposition stubs (peer; do not dual-own) |
| Base disposition negatives | `fixtures/negative-{auto-resolve,ui-write,authority-elevation}.expect.json` | Peer; remain on `disposition-action` schema |
| Core conflict projections | `AS-CORE2-008` → `project_atlas.conflict_projections` | Consume-only honesty helpers |
| Research / Ask Atlas 2 | `AS-2.2-RESEARCH-*` / `ask-atlas-2/` | Soft consumers of conflict cards |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own disposition / cockpit schemas, and
does **not** edit `src/project_atlas/**` or `apps/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`AS-2.2-CONFLICT-UX-DEEPEN-PREP-001.md`](AS-2.2-CONFLICT-UX-DEEPEN-PREP-001.md) | This deepen package card |
| [`INVARIANTS.md`](INVARIANTS.md) | Base walls + deepen certification notes |
| [`DEEPEN-FIXTURE-PLAN.md`](DEEPEN-FIXTURE-PLAN.md) | Deepen negative fixture inventory |
| [`contracts/conflict-ux-forbidden-action.schema.json`](contracts/conflict-ux-forbidden-action.schema.json) | Forbidden-action vocabulary (docs-owned) |
| [`fixtures/`](fixtures/) | Deepen negatives (release-cert / pilot-invent / llm-authority) |
| [`adr/ADR-2.2-CONFLICT-UX-002-deepen-prep.md`](adr/ADR-2.2-CONFLICT-UX-002-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`AS-2.2-CONFLICT-UX-PREP-001.md`](AS-2.2-CONFLICT-UX-PREP-001.md).
**No `README.md`** in this tree (index ownership stays with the 2.2 prep-index
lane).

## Deepen delta vs base conflict-ux PREP

| Concern | base conflict-ux | This deepen PREP |
|---|---|---|
| Cockpit / card / queue stubs | Four schemas under `contracts/` | Peer reference only — **do not relocate** |
| Disposition vocabulary | `disposition-action.schema.json` + FX-004..006 | Peer reference only |
| Forbidden-action schema | Missing | `conflict-ux-forbidden-action.schema.json` |
| Certification / PILOT negatives | Missing | FX-2.2-CUX-101..103 |
| Fixture inventory | `FIXTURE-PLAN.md` base scenarios | `DEEPEN-FIXTURE-PLAN.md` with FX IDs |
| Deepen package card / ADR | Missing | This card + ADR-002 |

## Hard invariants

1. **COCKPIT ≠ AUTO-RESOLVE** — open conflicts stay visible; no silent winner.
2. **UI ≠ CANONICAL** — disposition / forbidden actions never write Layer B.
3. **LLM ≠ AUTHORITY** — no `llm_authority=true`; no trust scores; no LLM winner.
4. **NO PILOT INVENT** — `pilot_roots=0`, `authentic_estate=false`, `pilot_pass=false`.
5. **FIXTURE ≠ RELEASE CERT** — cockpit rehearsal never stamps release certified.
6. **ONE REVIEW QUEUE ROOT** — no second durable queue beside Core review roots.
7. Fixture rehearsal ≠ DEMO VERIFIED-as-release ≠ authentic estate PILOT PASS ≠
   WEB ACCEPTED ≠ 2.1 RELEASE CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/conflict_projections.py` or
  `knowledge_compiler.py`
- Not shipped package-data schema promotion
- Not relocation / dual-ownership of existing conflict-ux schemas
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not DEMO VERIFIED as release or PILOT credit

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core conflict runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relocating or rewriting base disposition / cockpit / card / queue stubs
- Relabeling base conflict-ux fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, silent winners, or release stamps

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
