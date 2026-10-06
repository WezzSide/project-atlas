# AS-2.2-COMPAT-PIN-DEEPEN-PREP-001 — Compatibility pin deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-COMPAT-PIN-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-COMPAT-PIN-001` runtime |
| Tip audited | `b431494dc8860f4f1db3f327c9ccf991699ccfc5` |
| Tree | `26a59cd76bd9df410912b4552ddd907f7a160588` |
| Scope | `docs/atlas-2.2/compat-pin/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `compat_anchor.py` / release anchors | **do not mutate / do not publish** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-2 compatibility-pin PREP **beyond** the base expectation /
scenario stubs already landed under `docs/atlas-2.2/compat-pin/` (PR
[#196](https://github.com/WezzSide/project-atlas/pull/196)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/compat-pin/**` for:

- explicit fail-closed forbidden-action vocabulary (release-cert stamp, PILOT
  invent, premature 2.1 anchor publish, runtime mutation, future-pin-as-live),
- negative rehearsal payloads that document expected rejections with
  fixture-only evidence walls,
- a deepen ADR that freezes the PREP ≠ ANCHOR boundary,

without publishing `docs/releases/2.1.0/`, without mutating
`compat_anchor.py`, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base compat-pin PREP | `AS-2.2-COMPAT-PIN-PREP-001` → `compat-pin/` | Expectation + scenario stubs (peer; do not dual-own) |
| Base fixtures | `compat-pin/fixtures/` | Positive inventory + base negatives (peer) |
| Base ADR | `adr/ADR-2.2-COMPAT-PIN-001-2.1-anchor-prep.md` | Prep boundary (peer) |
| 1.0 pattern | `AS-2.0-COMPAT-001` / `atlas-1.0.0-compat` | Live consumer pin (read-only) |
| Future slot | `AS-2.2-COMPAT-PIN-001` | Post-unlock production path |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Anchor layers (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | PREP≠ANCHOR / no invent / future pin only |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Base + deepen fixture inventory |
| [`contracts/compat-pin-forbidden-action.schema.json`](contracts/compat-pin-forbidden-action.schema.json) | Forbidden-action JSON Schema stub |
| [`fixtures/`](fixtures/) | Deepen negative rehearsal payloads |
| [`adr/ADR-2.2-COMPAT-PIN-001-2.1-anchor-deepen-prep.md`](adr/ADR-2.2-COMPAT-PIN-001-2.1-anchor-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`AS-2.2-COMPAT-PIN-PREP-001.md`](AS-2.2-COMPAT-PIN-PREP-001.md).
Index ownership stays with the 2.2 prep-index lane; this deepen card is the
deepen entry.

## Deepen delta vs base compat-pin PREP

| Concern | base compat-pin (#196) | This deepen PREP |
|---|---|---|
| Expectation + scenario stubs | Two schemas under `contracts/` | Peer reference only |
| Positive fixtures | `compat-expectation.fixture.json` | Peer reference only |
| Base negatives | Release-cert / PILOT invent sketches | Peer; not relocated |
| Fail-closed ops | Error keys in INVARIANTS | Forbidden-action vocabulary + deepen negatives |
| Deepen ADR | — | Explicit deepen boundary ADR |

## Hard invariants

1. **PREP ≠ ANCHOR** — fixtures declare future `atlas-2.1.0-compat`; no published 2.1 anchor.
2. **NO 2.1 RELEASE STAMP** — `release_certified=false`, `pilot_pass=false`.
3. **FUTURE PIN ONLY** — live consumer remains `atlas-1.0.0-compat` until cert.
4. **NO PILOT INVENT** — `authentic_estate=false`, `evidence_class=fixture-only`.
5. **NO RUNTIME MUTATION** — do not edit `compat_anchor.py` on this tip.
6. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/compat_anchor.py`
- Not publication of `docs/releases/2.1.0/compatibility-anchor.json`
- Not shipped package-data schema promotion
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of base expectation stubs or base negatives

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or `docs/releases/2.1.0/`
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Relabeling base fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots or set `release_certified=true` / `pilot_pass=true`

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock and anchor publication remain blocked
until `ATLAS_2_1_RELEASE_CERTIFIED=YES` and
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
