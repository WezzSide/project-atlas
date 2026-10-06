# AS-2.2-TIME-MACHINE-DEEPEN-PREP-001 — Knowledge Time Machine deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-TIME-MACHINE-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-TIME-MACHINE-001` runtime |
| Tip audited | `961577c74191ee8e74ae0bcde438673ea041077c` |
| Tree | `961577c74191ee8e74ae0bcde438673ea041077c` |
| Scope | `docs/atlas-2.2/time-machine/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `bitemporal` / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |
| Evidence root | `D:\project-atlas-orphans\atlas-2.1-productionization-001\` |

## Purpose

Deepen the wave-1 Knowledge Time Machine PREP **beyond** the base as-of / diff
stubs already landed under `docs/atlas-2.2/time-machine/` (PR
[#168](https://github.com/WezzSide/project-atlas/pull/168)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/time-machine/**` for:

- explicit fail-closed forbidden-action vocabulary (Layer B promotion, LLM
  authority, silent overlap winners, wall-clock as-of, graph-as-authority,
  fixture-as-pilot, release-cert stamp),
- hard invariants and fixture-plan inventory aligned with wave-2 sibling depth,
- negative rehearsal payloads that document expected rejections,

without reopening AS-2.0-TEMPORAL-001 single-subject as-of ownership, without
shipping package-data schemas, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base Time Machine PREP | `AS-2.2-TIME-MACHINE-001` → `time-machine/` | As-of + diff stubs (peer; do not dual-own) |
| Base fixtures | `time-machine/fixtures/` | Positive FX-001..007 rehearsal (peer) |
| Temporal substrate | AS-2.0-TEMPORAL-001 / `bitemporal` | Single subject/field as-of (peer) |
| Conflict / review | AS-CORE-003 | Decision diff substrate (peer) |
| Graph projections | AS-GRAPH-* | Derived graph slot (peer) |
| Temporal UX PREP | `AS-2.2-TEMPORAL-UX-PREP-001` | UX lens (peer; do not dual-own) |
| Productionization posture | `atlas-2.1-productionization-001` | Read-only honesty reference |
| Evidence | `D:\project-atlas-orphans\atlas-2.1-productionization-001\` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | As-of + T1–T2 layers (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | As-of≠authority / diff≠mutation / no silent winners |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Deepen fixture family inventory |
| [`contracts/`](contracts/) | Forbidden-action JSON Schema stub (docs-owned) |
| [`fixtures/`](fixtures/) | Negative rehearsal payloads (peer to base positives) |
| [`adr/ADR-2.2-TIME-MACHINE-001-time-machine-deepen-prep.md`](adr/ADR-2.2-TIME-MACHINE-001-time-machine-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`README.md`](README.md) and
[`../AS-2.2-TIME-MACHINE-001.md`](../AS-2.2-TIME-MACHINE-001.md). Index
ownership stays with the 2.2 prep-index lane; this deepen card is the deepen
entry.

## Deepen delta vs base time-machine PREP

| Concern | base time-machine (#168) | This deepen PREP |
|---|---|---|
| As-of + diff stubs | Five schemas under `contracts/` | Peer reference only |
| Positive fixtures | FX-001..007 under `fixtures/` | Peer reference only |
| Fail-closed ops | Truth-boundary strings in snapshots | Forbidden-action vocabulary + negatives |
| Invariants doc | Embedded in ARCHITECTURE | Explicit `INVARIANTS.md` |
| Fixture inventory | Base `FIXTURE-PLAN.md` FX-001..007 | Deepen FX-101..105 negative inventory |

## Hard invariants

1. **AS-OF ≠ LAYER B AUTHORITY** — snapshots never promote claims / concepts.
2. **DIFF ≠ MUTATION** — T1–T2 deltas are derived read lenses only.
3. **NO SILENT OVERLAP WINNER** — overlapping validity → `unresolved_overlap`.
4. **WALL-CLOCK ≠ VALID-TIME** — `now` / `today` rejected as as-of input.
5. **GRAPH ≠ AUTHORITY** — graph diff units stay `authority.level=derived`.
6. **TEMPORAL-001 ≠ DUAL OWN** — single-subject as-of stays on Core substrate.
7. **LLM ≠ AUTHORITY** — no LLM similarity / prose as diff winner.
8. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/bitemporal.py` or Core authority
- Not shipped package-data schema promotion
- Not dual-ownership of AS-2.0-TEMPORAL-001 or temporal UX PREP
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of base positive fixtures or schema stubs

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core authority runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, LLM authority, or Layer B promotion

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
