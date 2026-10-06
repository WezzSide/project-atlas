# AS-2.2-MEM-GOV-DEEPEN-PREP-001 — Governed agent memory deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-MEM-GOV-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-MEM-GOV-001` runtime |
| Tip audited | `d9949530ca023647c6e6c9c76e3ba6864265e8c7` |
| Tree | `d9949530ca023647c6e6c9c76e3ba6864265e8c7` |
| Scope | `docs/atlas-2.2/mem-gov/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `knowledge_compiler` / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-1 governed memory PREP **beyond** the base record / axis stubs
already landed under `docs/atlas-2.2/contracts/mem-gov/` and
`docs/atlas-2.2/fixtures/mem-gov/` (PR [#169](https://github.com/WezzSide/project-atlas/pull/169)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/mem-gov/**` for:

- explicit fail-closed forbidden-action vocabulary (Layer B promotion, LLM
  authority, dual-active forks),
- hard invariants and fixture-plan inventory aligned with wave-2 sibling depth,
- negative rehearsal payloads that document expected rejections,

without reopening AS-INT-011 receipt revocation ownership, without shipping
package-data schemas, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base memory PREP | `AS-2.2-MEM-GOV-001` → `contracts/mem-gov/` | Record + axis stubs (peer; do not dual-own) |
| Base fixtures | `fixtures/mem-gov/` | Positive axis rehearsal (peer) |
| Base ADR | `adr/ADR-2.2-MEM-GOV-001-governed-agent-memory.md` | Prep boundary (peer) |
| Receipt revocation | AS-INT-011 | Pattern cousin; **do not dual-own** indexes |
| Context compiler | AS-2.0-CTX-001 / CTX PREP | Consumer of active memory pointers |
| Agent OS | AS-2.0-AGENTOS-001 | Session envelope supplies `session_id` |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layers, governance axes (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | Memory≠authority / provenance / no dual-active |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Deepen fixture family inventory |
| [`contracts/`](contracts/) | Forbidden-action JSON Schema stub (docs-owned) |
| [`fixtures/`](fixtures/) | Negative rehearsal payloads |
| [`adr/ADR-2.2-MEM-GOV-001-governed-agent-memory-deepen-prep.md`](adr/ADR-2.2-MEM-GOV-001-governed-agent-memory-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`README.md`](README.md). Index ownership stays with
the 2.2 prep-index lane; this deepen card is the deepen entry.

## Deepen delta vs base mem-gov PREP

| Concern | base mem-gov (#169) | This deepen PREP |
|---|---|---|
| Record + axis stubs | Six schemas under `contracts/mem-gov/` | Peer reference only |
| Positive fixtures | Ten axis samples under `fixtures/mem-gov/` | Peer reference only |
| Fail-closed ops | Truth-boundary strings in records | Forbidden-action vocabulary + negatives |
| Invariants doc | Embedded in ARCHITECTURE | Explicit `INVARIANTS.md` |
| Fixture inventory | `fixtures/mem-gov/README.md` table | `FIXTURE-PLAN.md` with FX IDs |

## Hard invariants

1. **MEMORY ≠ LAYER B AUTHORITY** — memory never promotes claims / concepts.
2. **PROVENANCE REQUIRED** — no write without receipt + session + content hash.
3. **NO DUAL-ACTIVE** — two actives for one `memory_key` without supersession ⇒ reject.
4. **INT-011 ≠ DUAL OWN** — memory revocation indexes stay distinct from receipt revocation.
5. **LLM ≠ AUTHORITY** — no `llm_authority=true`; no trust scores.
6. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/knowledge_compiler.py` or Core authority
- Not shipped package-data schema promotion
- Not dual-ownership of AS-INT-011 receipt revocation indexes
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of `contracts/mem-gov/` or `fixtures/mem-gov/` base stubs

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core authority runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base mem-gov fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, LLM authority, or Layer B promotion

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
