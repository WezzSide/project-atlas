# AS-2.2-CHATGPT-LIVE-DEEPEN-PREP-001 — Live ChatGPT bridge deepen (SAFE prep)

| Field | Value |
|---|---|
| Package | **AS-2.2-CHATGPT-LIVE-DEEPEN-PREP-001** |
| Class | **PREP ONLY** (contracts / fixtures / ADR) |
| Unlock target | Post-`v2.1.0` → feeds optional `AS-2.2-CHATGPT-LIVE-001` |
| Tip audited | `103ca52529c4204d856fec3b2a3b23de36c708ba` |
| Tree | `21e3bdd3dc38c9998ac0b74cfc9c9a287ff7d273` |
| Scope | `docs/atlas-2.2/chatgpt-live/**` deepen lane (+ unique unit test) |
| Production mutation | **NONE** |
| `chatgpt_bridge` / `chatgpt_capture` | **do not mutate** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED` | **NO** |

## Purpose

Deepen the wave-1 quarantine-first live ChatGPT bridge PREP **beyond** the base
architecture / stub schemas already landed under
`docs/atlas-2.2/chatgpt-live/` (PR
[#194](https://github.com/WezzSide/project-atlas/pull/194)).

This PREP owns a **unique deepen path** under `docs/atlas-2.2/chatgpt-live/**` for:

- peer-depth fail-closed forbidden-action vocabulary covering **env** force-live,
  **billing** without opt-in, and **quarantine** bypass, plus release-cert stamp,
- hard invariants and fixture-plan inventory aligned with wave-2/3 sibling depth,
- negative rehearsal payloads that document expected rejections,

without reopening Ask Atlas 2 deepen ownership (`ask-atlas-2/**`), without
shipping package-data schemas, and without claiming 2.1 release credit.

## Conceptual reference (read-only)

| Surface | Package / path | Role in this PREP |
|---|---|---|
| Base live bridge PREP | `AS-2.2-CHATGPT-LIVE-PREP-001` → `chatgpt-live/` | Quarantine-first stubs + base negatives (peer; do not dual-own) |
| Export bridge | `AS-2.1-CHATGPT-BRIDGE-001` → `chatgpt_bridge.py` | On-disk export → quarantine; **do not mutate** |
| Capture receipt | `AS-2.0-CHATGPT-CAPTURE-001` → `chatgpt_capture.py` | Fixture capture; `live_api` forbidden |
| Ask Atlas 2 deepen | `AS-2.2-ASK2-DEEPEN-PREP-001` → `ask-atlas-2/**` | Answer lens peer; **do not dual-own** |
| Gap register | `GAP-NS-007` | Live API absent; export-path today |

This PREP package **references** those contracts conceptually. It does **not**
relocate base stubs, does **not** dual-own `ask-atlas-2/**`, and does **not**
edit `src/project_atlas/**`.

## Deliverables in this PREP

| Doc | Role |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Layers + deepen delta (peer to base PREP) |
| [`CONTRACT.md`](CONTRACT.md) | Base stub index + deepen forbidden schema |
| [`INVARIANTS.md`](INVARIANTS.md) | Quarantine / env / billing / release walls |
| [`FIXTURE-PLAN.md`](FIXTURE-PLAN.md) | Base + deepen fixture family inventory |
| [`contracts/`](contracts/) | Deepen forbidden-action JSON Schema stub (docs-owned) |
| [`fixtures/`](fixtures/) | Deepen negative rehearsal payloads |
| [`adr/ADR-2.2-CHATGPT-LIVE-001-quarantine-first-live-bridge-deepen-prep.md`](adr/ADR-2.2-CHATGPT-LIVE-001-quarantine-first-live-bridge-deepen-prep.md) | Deepen boundary ADR |

Base package card remains [`AS-2.2-CHATGPT-LIVE-PREP-001.md`](AS-2.2-CHATGPT-LIVE-PREP-001.md).
Index ownership stays with the 2.2 prep-index lane; this deepen card is the
deepen entry. **No `README.md`** in this tree.

## Deepen delta vs base chatgpt-live PREP

| Concern | base chatgpt-live (#194) | This deepen PREP |
|---|---|---|
| Quarantine-first stubs | Request / envelope / receipt schemas | Peer reference only |
| Base forbidden vocabulary | Bypass / Layer B / LLM / default-on / pilot | Peer reference only |
| Peer-depth fail-closed | Quarantine covered; env / billing / release-cert absent | Env force-live + billing-without-opt-in + quarantine reaffirm + release-cert |
| Fixture inventory | FX-2.2-CGL-001..008 | Deepen FX-101..104 negatives |
| Deepen package card / ADR | Absent | This card + deepen ADR |

## Hard invariants

1. **QUARANTINE-FIRST** — live API payloads never skip provider/PROV quarantine.
2. **ENV ≠ OPT-IN** — environment variables alone must not force `live_chatgpt_api`.
3. **BILLING ≠ SILENT** — paid/live network spend requires explicit operator opt-in.
4. **LLM ≠ AUTHORITY** — live turns never stamp Layer B or elevate `authority.level`.
5. **LIVE DEFAULT OFF** — `live_chatgpt_api` remains opt-in; export bridge stays default.
6. **NO `chatgpt_bridge` MUTATION** — this PREP must not edit runtime bridge modules.
7. **ASK2 ≠ DUAL OWN** — answer-lens deepen stays under `ask-atlas-2/`.
8. Fixture rehearsal ≠ authentic estate PILOT PASS ≠ WEB ACCEPTED ≠ 2.1 RELEASE
   CERTIFIED ≠ 2.2 unlock. **DEMO ≠ RELEASE.**

## Explicit non-claims

- Not a mutation of `src/project_atlas/chatgpt_bridge.py` or `chatgpt_capture.py`
- Not shipped package-data schema promotion
- Not dual-ownership of `docs/atlas-2.2/ask-atlas-2/**`
- Not `ATLAS_2_1_RELEASE_CERTIFIED = YES`
- Not `ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`
- Not authentic estate PILOT evidence
- Not a default-on live ChatGPT network client
- Not relocation of base FX-001..008 stubs

## Forbidden in this package

- Edits under `src/`, shipped `schemas/`, `apps/`, or existing chatgpt runtime paths
- Editing `docs/atlas-2.2/README.md` (index owned by sibling harvest worker)
- Editing `docs/atlas-2.2/ask-atlas-2/**` (owned by Ask Atlas 2 deepen peer)
- Relabeling export-bridge or base fixture success as live-API or 2.1/2.2 release credit
- Fixture payloads that invent PILOT roots, force live via env, bill without opt-in,
  bypass quarantine, or stamp release certification

## Exit (PREP)

PREP is complete when this deepen tree lands via PR with docs/fixtures/ADR +
unit presence tests only. Runtime unlock remains blocked until
`ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED`.
