# AS-2.2-REALITY-LIVE-DEEPEN-PREP-001 — Live Reality Gap collectors deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-REALITY-LIVE-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-REALITY-LIVE-001` runtime |
| Tip audited | `961577c74191ee8e74ae0bcde438673ea041077c` |
| Tree | `a23e3ae1027eaeeaed50e3fb470be8226b1afe29` |
| Scope | `docs/atlas-2.2/reality-live/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `reality_gap.py` / `reality_gap_ui.py` | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-1 live Reality Gap collector PREP **beyond** the base design
and schema drafts already landed under `docs/atlas-2.2/reality-live/` and
`docs/atlas-2.2/contracts/reality-live/` (PR [#167](https://github.com/WezzSide/project-atlas/pull/167)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/reality-live/**` for:

- explicit fail-closed forbidden-action vocabulary (PILOT invent, LLM /
  conversational sole certifier, Layer B promotion, release-cert stamp),
- hard invariants and fixture-plan inventory aligned with wave-2 sibling depth,
- negative rehearsal payloads that document expected rejections,

without reopening AS-2.0-REALITY-GAP-001 inventory ownership, without shipping
package-data schemas, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base live collectors PREP | `AS-2.2-REALITY-LIVE-001` → `reality-live/` | Design + planes + positive fixtures (peer; do not dual-own) |
| Base schema drafts | `contracts/reality-live/` | Planes + gap-report drafts (peer) |
| Static gap inventory | `AS-2.0-REALITY-GAP-001` | Predecessor theme inventory (peer) |
| 2.2 gap register PREP | `AS-2.2-REALITY-GAP-PREP-001` → `reality-gap/` | Strategy register (peer; do not dual-own) |
| Productionization posture | `atlas-2.1-productionization-001` | Read-only honesty reference |
| Evidence | `D:\project-atlas-orphans\atlas-2.1-productionization-001\` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layers, deepen delta, truth boundaries |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) + deepen schema |
| [`INVARIANTS.md`](INVARIANTS.md) | Collectors≠authority / planes / unknown≠healthy |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Deepen fixture family inventory |
| [`contracts/`](contracts/) | Forbidden-action JSON Schema stub (docs-owned) |
| [`fixtures/`](fixtures/) | Negative rehearsal payloads (peer to base positives) |
| [`adr/ADR-2.2-REALITY-LIVE-001-live-collectors-deepen-prep.md`](adr/ADR-2.2-REALITY-LIVE-001-live-collectors-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`README.md`](README.md) and
[`../../AS-2.2-REALITY-LIVE-001.md`](../../AS-2.2-REALITY-LIVE-001.md). Index
ownership stays with the 2.2 prep-index lane; this deepen card is the deepen entry.

## Deepen delta vs base reality-live PREP

| Concern | base reality-live (#167) | This deepen PREP |
|---|---|---|
| Planes + collectors design | `PLANES.md` + `COLLECTORS-DESIGN.md` | Peer reference only |
| Positive fixtures | `fixtures/planes.fixture.json`, `collectors.fixture.json` | Peer reference only |
| Schema drafts | `contracts/reality-live/*.draft.json` | Peer reference only |
| Fail-closed ops | Truth-boundary strings in gap report draft | Forbidden-action vocabulary + negatives |
| Invariants doc | Embedded in design docs | Explicit `INVARIANTS.md` |
| Fixture inventory | `fixtures/README.md` table | `FIXTURE-PLAN.md` with FX IDs |

## Hard invariants

1. **COLLECTORS ≠ LAYER B AUTHORITY** — gap reports never promote claims / concepts.
2. **CONVERSATIONAL ≠ SOLE CERTIFIER** — dialogue evidence never stamps
   `LIVE_PRODUCTION` alone.
3. **UNKNOWN ≠ HEALTHY** — empty ops / missing evidence stays `UNKNOWN`, not invented healthy.
4. **REALITY-GAP-001 ≠ DUAL OWN** — static inventory indexes stay distinct from live collectors.
5. **LLM ≠ AUTHORITY** — no `llm_authority=true`; no trust scores.
6. **PILOT INVENT FORBIDDEN** — `pilot_roots=0`, `invent_pilot_roots=false` always.
7. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/reality_gap.py` or Core authority
- Not shipped package-data schema promotion
- Not dual-ownership of AS-2.0-REALITY-GAP-001 inventory or `reality-gap/` register
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of `contracts/reality-live/` or base positive fixtures

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core authority runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, LLM authority, or Layer B promotion

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
