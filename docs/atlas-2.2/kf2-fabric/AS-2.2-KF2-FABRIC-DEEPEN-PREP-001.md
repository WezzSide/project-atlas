# AS-2.2-KF2-FABRIC-DEEPEN-PREP-001 — Estate KF fabric deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-KF2-FABRIC-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-KF2-FABRIC-001` runtime |
| Tip audited | `7c2100dcda8a7c516f360b025da538eed085a971` |
| Scope | `docs/atlas-2.2/kf2-fabric/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `kf2_fabric` / `kf2_inventory` / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |
| Demo VERIFIED | ≠ release unlock / ≠ authentic PILOT |

## Purpose

Deepen the wave-2 KF2 fabric PREP **beyond** the base inventory / projection
stubs already landed under `docs/atlas-2.2/kf2-fabric/` (PR
[#186](https://github.com/WezzSide/project-atlas/pull/186)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/kf2-fabric/**` for:

- explicit fail-closed forbidden-action vocabulary (authority elevate,
  cross-promote, projection write, Layer B write, release-cert stamp, PILOT
  invent, LLM authority, KF2 runtime mutation),
- deepen negative rehearsal payloads with fixture-only evidence walls,
- a deepen ADR that freezes KF2 ≠ AUTHORITY / NO CROSS PROMOTE /

without mutating `kf2_fabric` / `kf2_inventory`, without elevating fabric rows
to Layer B, and without claiming 2.1 release credit.

Peer depth target: wave-5 deepen cards (`conflict-ux` / `compat-pin` /
`estate-ops`) and fabric sibling `AS-2.2-XPROJ-DEEPEN-PREP-001` (#233).

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base KF2 fabric PREP | `AS-2.2-KF2-FABRIC-PREP-001` → `kf2-fabric/` | Inventory / projection stubs (peer; do not dual-own) |
| Base fixtures | `kf2-fabric/fixtures/` | Positive samples + base negatives (peer) |
| Base ADR | `adr/ADR-2.2-KF2-FABRIC-001-estate-fabric-prep.md` | Prep boundary (peer) |
| AS-KF2-* substrate | `AS-KF2-NS/ENTITY/REL/002` | Conceptual read-only citations |
| XPROJ deepen peer | `AS-2.2-XPROJ-DEEPEN-PREP-001` | Fabric-sibling forbidden-action shape |
| Future slot | `AS-2.2-KF2-FABRIC-001` | Post-unlock production path |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`AS-2.2-KF2-FABRIC-DEEPEN-PREP-001.md`](AS-2.2-KF2-FABRIC-DEEPEN-PREP-001.md) | This deepen package card |
| [`INVARIANTS.md`](INVARIANTS.md) | Base walls + deepen certification notes |
| [`DEEPEN-FIXTURE-PLAN.md`](DEEPEN-FIXTURE-PLAN.md) | Deepen negative fixture inventory |
| [`contracts/kf2-fabric-forbidden-action.schema.json`](contracts/kf2-fabric-forbidden-action.schema.json) | Forbidden-action vocabulary (docs-owned) |
| [`fixtures/`](fixtures/) | Deepen negatives |
| [`adr/ADR-2.2-KF2-FABRIC-002-deepen-prep.md`](adr/ADR-2.2-KF2-FABRIC-002-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`AS-2.2-KF2-FABRIC-PREP-001.md`](AS-2.2-KF2-FABRIC-PREP-001.md).
**Do not edit** package `README.md` or `docs/atlas-2.2/README.md` (index
ownership stays with the 2.2 prep-index lane).

## Deepen delta vs base KF2 fabric PREP

| Concern | base kf2-fabric (#186) | This deepen PREP |
|---|---|---|
| Inventory / projection stubs | Three schemas under `contracts/` | Peer reference only — **do not relocate** |
| Base negatives | FX-2.2-KF2-006..008 (cross-promote / authority / projection-write) | Peer; not relocated |
| Forbidden-action schema | Missing | `kf2-fabric-forbidden-action.schema.json` |
| Certification / PILOT / LLM negatives | Missing | FX-2.2-KF2-DEEPEN-101..108 |
| Fixture inventory | `FIXTURE-PLAN.md` base scenarios | `DEEPEN-FIXTURE-PLAN.md` with deepen FX IDs |
| Deepen package card / ADR | Missing | This card + ADR-002 |

## Hard invariants

1. **KF2 ≠ AUTHORITY** — namespace / entity / relationship / inventory remain derived.
2. **NO CROSS PROMOTE** — estate projection never sets `cross_promote: true`.
3. **PROJECTION ≠ MUTATION** — never writes `generated/kf2/` or `generated/ops/kf2/`.
4. **LLM ≠ AUTHORITY** — no `llm_authority=true`; no trust scores.
5. **NO PILOT INVENT** — `pilot_roots=0`, `authentic_estate=false`, `pilot_pass=false`.
6. **FIXTURE ≠ RELEASE CERT** — fabric rehearsal never stamps release certified.
7. **NO KF2 RUNTIME MUTATION** — do not edit `kf2_fabric.py` / `kf2_inventory.py`.
8. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/kf2_fabric.py` or `kf2_inventory.py`
- Not shipped package-data schema promotion
- Not relocation / dual-ownership of existing kf2-fabric schemas
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not dual-ownership of AS-KF2 / AS-XPROJ emit trees

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or existing KF2 runtime paths
- Editing `docs/atlas-2.2/README.md` or package `README.md`
- Relocating or rewriting base inventory / projection stubs
- Relabeling base kf2-fabric fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, set `cross_promote: true`, or stamp
  release certified

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
