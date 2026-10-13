# AS-2.2-INTEL-SLICE-DEEPEN-PREP-001 — Estate intelligence slice deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-INTEL-SLICE-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds future `AS-2.2-INTEL-SLICE-001` runtime |
| Tip audited | `2fe504914eadef7d453b773fa4d96e3bb4175f47` |
| Tree | `3d82fa7552280afd82d68f8313dde5bfdaa30d9d` |
| Scope | `docs/atlas-2.2/intel-slice/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| Layer B / KF / review roots | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-1 estate intelligence slice PREP **beyond** the base
architecture + fixture sketches already landed under
`docs/atlas-2.2/intel-slice/` (PR
[#189](https://github.com/WezzSide/project-atlas/pull/189) / base
`AS-2.2-INTEL-SLICE-PREP-001`).

This PREP owns a **unique deepen delta** under `docs/atlas-2.2/intel-slice/**`
for:

- explicit fail-closed **forbidden-action** vocabulary (release-cert stamp,
  PILOT invent, LLM authority) alongside the base authority / silent-resolve /
  canonical-write walls,
- deepen fixture-plan inventory aligned with conflict-ux / mem-gov / research
  peers,
- negative rehearsal payloads for certification / PILOT / LLM authority gaps
  with fixture-only evidence walls,

without relocating or dual-owning existing base sample / negative fixtures,
without shipping package-data schemas, and without claiming 2.1 release credit.

**DEMO VERIFIED ≠ release / PILOT.** Fixture slice rehearsal and any demo
walkthrough grant **no** `ATLAS_2_1_RELEASE_CERTIFIED`, authentic-estate PILOT
PASS, WEB ACCEPTED, or 2.2 unlock credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base intel-slice PREP | `AS-2.2-INTEL-SLICE-PREP-001` → `intel-slice/` | Architecture + sample envelopes (peer; do not dual-own) |
| Base negatives | `fixtures/negative-{authority-elevation,silent-conflict-resolve,llm-authority,canonical-write}.expect.json` | Peer; remain base package_id |
| KF fabric / RET / TEMPORAL / CONFLICT-UX | Sibling 2.2 PREP lanes | Cite-only composition inputs |
| Soft consumer | Ask Atlas 2 / Estate-Ops / MCP lenses | Consume-only slice readers |
| Evidence | `atlas-2.1-productionization-001` | Read-only posture reference |

This PREP package **references** those lanes conceptually. It does **not**
relocate base fixtures, does **not** dual-own upstream emit trees, and does
**not** edit `src/project_atlas/**` or `apps/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`AS-2.2-INTEL-SLICE-DEEPEN-PREP-001.md`](AS-2.2-INTEL-SLICE-DEEPEN-PREP-001.md) | This deepen package card |
| [`INVARIANTS.md`](INVARIANTS.md) | Base walls + deepen certification notes |
| [`DEEPEN-FIXTURE-PLAN.md`](DEEPEN-FIXTURE-PLAN.md) | Deepen negative fixture inventory |
| [`contracts/intel-slice-forbidden-action.schema.json`](contracts/intel-slice-forbidden-action.schema.json) | Forbidden-action vocabulary (docs-owned) |
| [`fixtures/`](fixtures/) | Deepen negatives (release-cert / pilot-invent / llm-authority-stamp) |
| [`adr/ADR-2.2-INTEL-SLICE-001-deepen-prep.md`](adr/ADR-2.2-INTEL-SLICE-001-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`AS-2.2-INTEL-SLICE-PREP-001.md`](AS-2.2-INTEL-SLICE-PREP-001.md).
**No `README.md`** in this tree (index ownership stays with the 2.2 prep-index
lane).

## Deepen delta vs base intel-slice PREP

| Concern | base intel-slice | This deepen PREP |
|---|---|---|
| Architecture / samples | `ARCHITECTURE.md` + FX-001..003 | Peer reference only — **do not relocate** |
| Base negatives | FX-004..007 informal expect JSON | Peer reference only |
| Forbidden-action schema | Missing | `intel-slice-forbidden-action.schema.json` |
| Certification / PILOT negatives | Missing | FX-2.2-ISL-101..103 |
| Fixture inventory | `FIXTURE-PLAN.md` base scenarios | `DEEPEN-FIXTURE-PLAN.md` with FX IDs |
| Deepen package card / ADR | Missing | This card + ADR-001 deepen |

## Hard invariants

1. **INTEL SLICE ≠ AUTHORITY** — envelope `authority.level = derived` always.
2. **COMPOSITION ≠ MUTATION** — cites upstream ids only; never writes Layer B / KF / review roots.
3. **NO SILENT CONFLICT WINNER** — open conflicts remain visible in the slice.
4. **LLM ≠ AUTHORITY** — no `llm_authority_stamp`; no trust scores; no LLM winner.
5. **NO PILOT INVENT** — `pilot_roots=0`, `authentic_estate=false`, `pilot_pass=false`.
6. **FIXTURE ≠ RELEASE CERT** — slice rehearsal never stamps release certified.
7. Fixture rehearsal ≠ DEMO VERIFIED-as-release ≠ authentic estate PILOT PASS ≠
   WEB ACCEPTED ≠ 2.1 RELEASE CERTIFIED ≠ 2.2 unlock.

## Explicit non-claims

- Not a mutation of `src/`, `apps/`, `api_server`, or `mcp_server`
- Not shipped package-data schema promotion
- Not relocation / dual-ownership of base sample / negative fixtures
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not DEMO VERIFIED as release or PILOT credit
- Not dual-ownership of KF2 / RET / TEMPORAL / CONFLICT-UX emit paths

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or existing Core runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Adding `README.md` under `intel-slice/`
- Relocating or rewriting base sample / negative fixtures
- Relabeling base intel-slice fixture success as 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, silent winners, or release stamps

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
