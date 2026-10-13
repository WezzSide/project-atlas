# AS-2.2-DOC-CHARTER-DEEPEN-PREP-001 — Charter + maturity matrix deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-DOC-CHARTER-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-DOC-CHARTER-001` |
| Tip audited | `1a69f49e87e8946618f559a42c0781835173fc72` |
| Tree | `bbf2a919aa1a3e17d5b6f584752d72f7f5574b49` |
| Scope | `docs/atlas-2.2/doc-charter/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `CHARTER.md` / matrix draft | **do not relocate / do not dual-own base stubs** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-2 doc-charter PREP **beyond** the base charter / maturity-matrix
stubs already landed under `docs/atlas-2.2/doc-charter/` (PR
[#199](https://github.com/WezzSide/project-atlas/pull/199)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/doc-charter/**` for:

- explicit fail-closed forbidden-action vocabulary (release-cert stamp, 2.2
  unlock stamp, PILOT invent, matrix-cert promotion, runtime mutation, LLM
  authority),
- negative rehearsal payloads that document expected rejections with
  fixture-only evidence walls,
- a deepen ADR that freezes the PREP ≠ CERT / PREP ≠ UNLOCK boundary,

without editing `docs/atlas-2.2/README.md`, without mutating runtime modules,
and without claiming 2.1 release or 2.2 unlock credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base doc-charter PREP | `AS-2.2-DOC-CHARTER-PREP-001` → `doc-charter/` | Charter + matrix stubs (peer; do not dual-own) |
| Base matrix schemas | `contracts/charter-maturity-*.schema.json` | Row + matrix stubs (peer) |
| Base negatives | `fixtures/negative-*.expect.json` | Base rejection sketches (peer) |
| Base ADR | `adr/ADR-2.2-DOC-CHARTER-001-charter-maturity-prep.md` | Prep boundary (peer) |
| 2.1 charter / matrix | `docs/atlas-2.1/CHARTER.md`, `FEATURE-MATURITY-MATRIX.md` | Conceptual reference only |
| Future slot | `AS-2.2-DOC-CHARTER-001` | Post-unlock production path |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**` or `apps/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Charter layers (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | PREP≠CERT / no PILOT invent / no runtime mutation |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Base + deepen fixture inventory |
| [`contracts/doc-charter-forbidden-action.schema.json`](contracts/doc-charter-forbidden-action.schema.json) | Forbidden-action JSON Schema stub (enum) |
| [`fixtures/`](fixtures/) | Deepen negative rehearsal payloads |
| [`adr/ADR-2.2-DOC-CHARTER-001-charter-maturity-deepen-prep.md`](adr/ADR-2.2-DOC-CHARTER-001-charter-maturity-deepen-prep.md) | Deepen boundary ADR |

Base package card remains
[`AS-2.2-DOC-CHARTER-PREP-001.md`](AS-2.2-DOC-CHARTER-PREP-001.md). Index
ownership stays with the 2.2 prep-index lane; this deepen card is the deepen
entry. **No `README.md`** in this tree.

## Deepen delta vs base doc-charter PREP

| Concern | base doc-charter (#199) | This deepen PREP |
|---|---|---|
| Charter + matrix draft | `CHARTER.md` + `FEATURE-MATURITY-MATRIX.md` | Peer reference only |
| Matrix schemas | Two schemas under `contracts/` | Peer; not relocated |
| Base negatives | Release-cert / PILOT invent sketches | Peer; not relocated |
| Fail-closed ops | Error keys in INVARIANTS | Dedicated forbidden-action enum + deepen negatives |
| Honesty walls | Embedded in matrix fixture | Required `evidence_class` / `authentic_estate` / `release_certified` / `pilot_pass` / `canonical_writes` |
| Deepen ADR | — | Explicit deepen boundary ADR |

## Hard invariants

1. **PREP ≠ CERT** — draft matrix rows do not stamp 2.1/2.2 release credit.
2. **NO 2.1 RELEASE STAMP** — `release_certified=false`, `pilot_pass=false`.
3. **NO 2.2 UNLOCK STAMP** — fixtures never claim implementation unlock.
4. **NO PILOT INVENT** — `authentic_estate=false`, `evidence_class=fixture-only`.
5. **NO RUNTIME MUTATION** — do not edit `src/`, shipped schemas, or `apps/`.
6. **LLM ≠ AUTHORITY** — matrix dispositions are audit labels, not trust scores.
7. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/**` or `apps/**`
- Not shipped package-data schema promotion
- Not dual-ownership of base matrix stubs or base negatives
- Not editing `docs/atlas-2.2/README.md`
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of base charter / matrix artifacts

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or Core intelligence paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots or set `release_certified=true` /
  `pilot_pass=true` / `canonical_writes=true` / `authentic_estate=true`

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Production charter refresh
(`AS-2.2-DOC-CHARTER-001`) remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
