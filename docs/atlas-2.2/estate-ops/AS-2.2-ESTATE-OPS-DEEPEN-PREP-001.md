# AS-2.2-ESTATE-OPS-DEEPEN-PREP-001 — Estate operations deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-ESTATE-OPS-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-ESTATE-OPS-001` runtime |
| Tip audited | `b431494dc8860f4f1db3f327c9ccf991699ccfc5` |
| Tree | `26a59cd76bd9df410912b4552ddd907f7a160588` |
| Scope | `docs/atlas-2.2/estate-ops/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `ops_health` / `ops_events` / Layer B | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-2 estate-ops PREP **beyond** the base cockpit / lens / action
stubs already landed under `docs/atlas-2.2/estate-ops/` (PR
[#197](https://github.com/WezzSide/project-atlas/pull/197)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/estate-ops/**` for:

- explicit fail-closed forbidden-action vocabulary (unknown-as-healthy, UI
  canonical write, PILOT invent, ops runtime mutation, LLM authority),
- deepen negative rehearsal payloads with fixture-only evidence walls,
- a deepen ADR that freezes unknown≠healthy / UI≠canonical,

without mutating `ops_health` / `ops_events`, without elevating UI receipts to
Layer B, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base estate-ops PREP | `AS-2.2-ESTATE-OPS-PREP-001` → `estate-ops/` | Cockpit + lens stubs (peer; do not dual-own) |
| Base fixtures | `estate-ops/fixtures/` | Positive samples + base negatives (peer) |
| Base ADR | `adr/ADR-2.2-ESTATE-OPS-001-estate-ops-lens-prep.md` | Prep boundary (peer) |
| Ops health | `AS-OBS-001` → `project_atlas.ops_health` | unknown≠healthy semantics (read-only) |
| Cross-project fabric | `AS-2.2-XPROJ-*` | Estate slice scope (peer) |
| Future slot | `AS-2.2-ESTATE-OPS-001` | Post-unlock production path |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own the shared fixture family, and does
**not** edit `src/project_atlas/**` or `apps/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layers / truth boundaries (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index (peer) |
| [`INVARIANTS.md`](INVARIANTS.md) | unknown≠healthy / UI≠canonical / no PILOT invent |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Base + deepen fixture inventory |
| [`contracts/estate-ops-forbidden-action.schema.json`](contracts/estate-ops-forbidden-action.schema.json) | Forbidden-action JSON Schema stub |
| [`fixtures/`](fixtures/) | Deepen negative rehearsal payloads |
| [`adr/ADR-2.2-ESTATE-OPS-001-estate-ops-lens-deepen-prep.md`](adr/ADR-2.2-ESTATE-OPS-001-estate-ops-lens-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`AS-2.2-ESTATE-OPS-PREP-001.md`](AS-2.2-ESTATE-OPS-PREP-001.md).
**No README.md** in this tree (index ownership stays with the 2.2 prep-index
lane; this deepen card is the deepen entry).

## Deepen delta vs base estate-ops PREP

| Concern | base estate-ops (#197) | This deepen PREP |
|---|---|---|
| Cockpit / lens / receipt stubs | Four schemas under `contracts/` | Peer reference only |
| Positive fixtures | FX-2.2-EO-001..003 | Peer reference only |
| Base action negatives | FX-2.2-EO-004..006 via `estate-ops-action` | Peer; not relocated |
| Fail-closed ops | Action stub enum | Dedicated forbidden-action vocabulary + deepen negatives |
| Deepen ADR | — | Explicit deepen boundary ADR |

## Hard invariants

1. **unknown ≠ healthy** — missing ops evidence never maps to healthy / PASS / READY.
2. **UI ≠ canonical** — Mission Control / Workspace / Ops Health never write Layer B.
3. **LLM ≠ authority** — derived envelopes only; no subjective trust scores.
4. **NO OPS RUNTIME MUTATION** — do not edit `ops_health` / `ops_events` in this PREP.
5. **NO PILOT INVENT** — `pilot_pass=false`, `authentic_estate=false`,
   `evidence_class=fixture-only`, `canonical_writes=false`,
   `release_certified=false`.
6. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/project_atlas/ops_health.py` or `ops_events.py`
- Not shipped package-data schema promotion
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not relocation of base cockpit stubs or base action negatives
- Not `apps/web` lens productization

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or existing ops runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Creating or editing `README.md` under this tree
- Relabeling DEMO_FIXTURE estate smoke success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots or treat unknown as healthy

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
