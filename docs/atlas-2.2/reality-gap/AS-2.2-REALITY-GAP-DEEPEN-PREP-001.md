# AS-2.2-REALITY-GAP-DEEPEN-PREP-001 — Reality Gap fail-closed deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-REALITY-GAP-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-REALITY-GAP-001` |
| Tip audited | `18cbe23e7f19b014b63aa1d0639d21a8f9ebbb5f` |
| Tree | `6e25ebb5ea1fc499f79ed1608b92c654b3ab0e91` |
| Scope | `docs/atlas-2.2/reality-gap/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| Base stubs | **do not dual-own / do not relocate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the reality-gap PREP **beyond** the base inventory / scenario stubs already
landed under `docs/atlas-2.2/reality-gap/` (PR
[#172](https://github.com/WezzSide/project-atlas/pull/172)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/reality-gap/**` for:

- explicit fail-closed forbidden-action vocabulary (unknown-as-healthy, UI
  canonical write, release-cert stamp, unlock stamp, PILOT invent, runtime
  mutation, LLM authority),
- negative rehearsal payloads with fixture-only evidence walls,
- a deepen ADR that freezes PREP ≠ CERT / PREP ≠ UNLOCK / PREP ≠ PILOT,

without editing `docs/atlas-2.2/README.md`, without mutating runtime modules,
and without claiming 2.1 release or 2.2 unlock credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base reality-gap PREP | `AS-2.2-REALITY-GAP-PREP-001` → `reality-gap/` | Inventory stubs (peer; do not dual-own) |
| Base schemas | `contracts/reality-gap-prep-*.schema.json` | Peer stubs |
| Base negatives | `fixtures/negative-*.fixture.json` | Peer sketches |
| Future slot | `AS-2.2-REALITY-GAP-001` | Post-unlock production path |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**` or `apps/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Gap layers (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | unknown≠healthy / UI≠canonical / no PILOT invent |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Base fixture inventory (peer) |
| [`DEEPEN-FIXTURE-PLAN.md`](DEEPEN-FIXTURE-PLAN.md) | Deepen negative inventory |
| [`contracts/reality-gap-forbidden-action.schema.json`](contracts/reality-gap-forbidden-action.schema.json) | Forbidden-action JSON Schema stub |
| [`fixtures/negative-deepen-*.expect.json`](fixtures/) | Deepen negative rehearsal payloads |
| [`adr/ADR-2.2-REALITY-GAP-001-deepen-prep.md`](adr/ADR-2.2-REALITY-GAP-001-deepen-prep.md) | Deepen boundary ADR |

Base package card remains
[`AS-2.2-REALITY-GAP-PREP-001.md`](AS-2.2-REALITY-GAP-PREP-001.md). Index
ownership stays with the 2.2 prep-index lane. Existing package `README.md` is
base peer documentation — deepen does not dual-own it.

## Hard invariants

1. **PREP ≠ CERT** — gap fixtures do not stamp 2.1/2.2 release credit.
2. **NO 2.1 RELEASE STAMP** — `release_certified=false`, `pilot_pass=false`.
3. **NO 2.2 UNLOCK STAMP** — `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED=NO`.
4. **NO PILOT INVENT** — `pilot_roots=0`, `authentic_estate=false`.
5. **unknown ≠ healthy** — unknown inventory status never coerces to healthy.
6. **UI ≠ canonical** — gap UI cannot write Layer B / claims.
7. **NO LLM AUTHORITY** — model output is never gap health truth.
8. **NO RUNTIME MUTATION** — docs/contracts/fixtures only.
9. Demo VERIFIED ≠ release unlock / ≠ authentic PILOT PASS.

## Explicit non-claims

- Not editing `docs/atlas-2.2/README.md` from this package branch
- Not `ATLAS_2_1_RELEASE_CERTIFIED=YES`
- Not authentic estate PILOT / not `v2.1.0` / not `v2.2.0` certification
- Fixture PASS ≠ authentic PILOT PASS
