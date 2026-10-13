# AS-2.2-ROADMAP-CROSSWALK-DEEPEN-PREP-001 — Roadmap crosswalk deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-ROADMAP-CROSSWALK-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Tip audited | `5e2559f096ce6e1a906ee69ded45f8393ff2310b` |
| Tree | `2768267fa1923a3a1108f84fff9d77eae7e794fc` (`docs/atlas-2.2`) |
| Scope | `docs/atlas-2.2/roadmap-crosswalk/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-3 roadmap-crosswalk PREP **beyond** the base mapping table and
fixture stub landed under `docs/atlas-2.2/roadmap-crosswalk/` (PR
[#206](https://github.com/WezzSide/project-atlas/pull/206)).

This PREP owns a **unique deepen path** under
`docs/atlas-2.2/roadmap-crosswalk/**` for:

- explicit fail-closed forbidden-action vocabulary (unlock claim, production
  ready claim, release-cert stamp, PILOT invent, runtime mutation, LLM
  authority, fixture-as-certification),
- deepen negative rehearsal payloads with fixture-only evidence walls,
- a deepen ADR that freezes CROSSWALK ≠ UNLOCK / PREP ≠ PRODUCTION,

without relocating `CROSSWALK.md` or `fixtures/crosswalk.fixture.json`, without
mutating `src/` or `apps/`, and without claiming unlock or release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base crosswalk PREP | `AS-2.2-ROADMAP-CROSSWALK-PREP-001` | Mapping charter + table (peer; do not dual-own) |
| Base table | `CROSSWALK.md` | Authoritative PREP → slot rows (peer) |
| Base fixture | `fixtures/crosswalk.fixture.json` | Machine-readable rehearsal stub (peer) |
| Strategy DAG | `docs/strategy/ATLAS-2.2-EXECUTABLE-ROADMAP.md` | Post-unlock production slots (read-only) |
| Indexes | `../README.md`, `../PREP-STATUS.md` | Harvest-owned index lanes (do not edit here) |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**` or `apps/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`INVARIANTS.md`](INVARIANTS.md) | CROSSWALK ≠ UNLOCK / PREP ≠ PRODUCTION walls |
| [`DEEPEN-FIXTURE-PLAN.md`](DEEPEN-FIXTURE-PLAN.md) | Deepen negative fixture inventory |
| [`contracts/roadmap-crosswalk-forbidden-action.schema.json`](contracts/roadmap-crosswalk-forbidden-action.schema.json) | Forbidden-action JSON Schema stub |
| [`fixtures/`](fixtures/) | Deepen negative rehearsal payloads |
| [`adr/ADR-2.2-ROADMAP-CROSSWALK-001-deepen-prep.md`](adr/ADR-2.2-ROADMAP-CROSSWALK-001-deepen-prep.md) | Deepen boundary ADR |

Base package card remains
[`AS-2.2-ROADMAP-CROSSWALK-PREP-001.md`](AS-2.2-ROADMAP-CROSSWALK-PREP-001.md).
**No `docs/atlas-2.2/README.md` / PREP-STATUS edit** in this package (index
owned by sibling harvest worker).

## Deepen delta vs base crosswalk PREP

| Concern | base crosswalk (#206) | This deepen PREP |
|---|---|---|
| Mapping table | `CROSSWALK.md` + fixture rows | Peer reference only |
| Honesty walls | Inline in package charter | Dedicated `INVARIANTS.md` |
| Fail-closed ops | Narrative non-claims | Forbidden-action vocabulary + deepen negatives |
| Deepen ADR | — | Explicit deepen boundary ADR |

## Hard invariants

1. **CROSSWALK ≠ UNLOCK** — mapping rows grant no implementation unlock.
2. **PREP ≠ PRODUCTION** — every row remains PREP until unlock + production
   package execution.
3. **NO RUNTIME MUTATION** — no `src/project_atlas/` or `apps/` changes.
4. **NO PILOT INVENT** — `pilot_pass=false`, `authentic_estate=false`,
   `evidence_class=fixture-only`.
5. **NO RELEASE CERT STAMP** — `release_certified=false`.
6. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not `ATLAS_2_1_RELEASE_CERTIFIED=YES`
- Not promotion of stub schemas to package data
- Not authentic estate PILOT / not `v2.1.0` / not `v2.2.0` certification
- Not relocation of `CROSSWALK.md` rows or the base fixture
- Crosswalk completeness ≠ production readiness of any mapped slot

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, or `apps/`
- Editing `docs/atlas-2.2/README.md` or `docs/atlas-2.2/PREP-STATUS.md`
- Relabeling base fixture success as unlock / release credit
- Fixture payloads that invent PILOT roots or set `release_certified=true` /
  `pilot_pass=true`

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Unlock and release remain blocked.
