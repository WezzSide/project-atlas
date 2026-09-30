# 06 — Production Web Specification (Evidence Desk)

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Status:** truth-state layer `IMPLEMENTED`; shell and IA `SPECIFIED`
**Direction:** Evidence Desk (`04` §3)

> `UI != CANONICAL`. Every surface below is a read projection. No component in this spec
> writes to a vault, and none may present an owner-gated fact as an actionable control.
> This spec does not claim `WEB APPLICATION ACCEPTED` — that boundary from ADR-010 stands.

---

## 1. Shell

```
┌──────────────────────────────────────────────────────────────────────┐
│ Atlas    ◆ LIVE · atlas-main            ⌘K            ⚿ 2 owner-gated │  status strip
├────────────┬─────────────────────────────────────────────────────────┤
│ Home     4 │                                                          │
│ Projects   │   <main id="main">                                       │
│ Ask        │                                                          │
│ Activity 2 │                                       ┌─ evidence ────┐  │
│ Estate     │                                       │  (drawer)     │  │
└────────────┴───────────────────────────────────────┴───────────────┴──┘
```

Three permanent regions, per Standing Watch's spatial-constancy rule:

- **Status strip** — read plane (`TruthChip`), vault identity, palette affordance,
  owner-gate count. Never scrolls away. This is where `DEMO != LIVE` lives ambiently, so
  it cannot be missed by scrolling past a banner.
- **Area rail** — the five job-areas from `02` §3, with counts. Replaces the flat 15-item
  `PROD_LINKS` (A-1).
- **Content + evidence drawer** — drawer pinned at ≥1600px, overlay below.

### Landmarks

`<header role="banner">` (strip) · `<nav aria-label="Areas">` (rail) · `<main id="main">` ·
`<aside aria-label="Evidence">` (drawer) · skip link to `#main` (already present) ·
`TruthAnnouncer` live regions (**implemented**).

## 2. Routing — additive, nothing removed

All 15 existing routes keep working. The five areas are new paths; existing routes redirect
into them, preserving `?project=`:

| Existing | Redirects to |
|---|---|
| `/knowledge` | `/projects/:id#knows` |
| `/intelligence` | `/projects/:id#questions` |
| `/time-machine` | `/projects/:id#change` |
| `/context`, `/workspace` | `/projects/:id#handoff` |
| `/discovery`, `/source-health` | `/estate/sources` |
| `/ops` | `/estate/system` |
| `/mission-control` | `/activity` |
| `/graph` | `/projects/:id#knows` + evidence drawer open |
| `/command-center` | `/` with the palette open |
| `/roadmap` | `/projects/:id#change` |

Redirects, not deletions — external links, docs and receipts referencing these paths
continue to resolve. Backlog `AX-001`.

## 3. Components

### 3.1 `TruthChip` — **IMPLEMENTED**

`src/components/TruthChip.tsx`. Renders glyph + label + optional detail; `data-truth-state`
for testability; a `<span>` with no handler, so an `OWNER REQUIRED` chip can never be
mistaken for a control. See `05` §4.

### 3.2 `TruthAnnouncer` — **IMPLEMENTED**

`src/components/TruthAnnouncer.tsx`, mounted unconditionally by `ProdShell`. Two
permanently present live regions (`role="status"` polite, `role="alert"` assertive), both
`aria-atomic`. Fed by `src/lib/announce.ts`. Fixes A-5. See `05` §4.

### 3.3 `CommandPalette` — `SPECIFIED` (`AX-010`)

The answer to Atlas's breadth (R-3). Opens on `Ctrl/Cmd+K`.

**ARIA contract**, stated precisely because the audit measured `aria-activedescendant` at 0
and this widget is the one place a mistake is costly:

```html
<div role="dialog" aria-modal="true" aria-label="Command palette">
  <input role="combobox" aria-expanded="true" aria-controls="cp-list"
         aria-activedescendant="cp-opt-3" aria-autocomplete="list" />
  <ul role="listbox" id="cp-list">
    <li role="option" id="cp-opt-3" aria-selected="true">…</li>
  </ul>
</div>
```

- **Focus stays in the input.** Arrow keys move `aria-activedescendant`, never DOM focus.
- **Focus is trapped while open and restored to the trigger on close.**
- **Rows are typed by authority** and the type is in the accessible name, not colour alone:
  `read` (default), `owner-gated`, `cli-only`. Owner-gated rows render as
  `⚿ OWNER REQUIRED` and are **not activatable** — selecting one navigates to the surface
  that explains the gate. `cli-only` rows (the write pipeline, J-11) display the command to
  copy; they do not execute it.
- Result count announced politely on filter change.

Content: 15 routes + 41 endpoints + 67 commands, grouped by the six areas from `07` §3, so
palette and CLI share one taxonomy.

### 3.4 `EvidenceDrawer` — `SPECIFIED` (`AX-011`)

Opens from any claim (J-5). Shows source path, ingest time, provenance hash, authority
precedence, and — for `CONTESTED` — every competing source with none marked the winner.
Provenance, never popularity: no score, no ranking, no "most used" signal.

### 3.5 `ClaimText` — `SPECIFIED` (`AX-012`)

Dossier's reading surface. Renders a claim as prose with an inline citation. **A claim with
no source renders `UNKNOWN` inline in the sentence where the fact belongs** — it is never
silently omitted. Prototyped and test-covered on `/design-lab/evidence-desk`.

## 4. States every surface must implement

| State | Requirement |
|---|---|
| Loading | Skeleton, not a spinner-only screen; announced politely on resolve |
| Empty | Distinguish "no results" from `UNKNOWN` — they are different facts |
| Error | Message names the next action (existing SEC-009 message is the standard); announced assertively |
| API unavailable | Fail closed. Never silently substitute fixture data |
| Demo fallback | Strip shows `◇ DEMO FIXTURE`; announced **assertively** |
| `UNKNOWN` | Amber chip, `?` glyph, never aggregated into a healthy rollup |
| `CONTESTED` | Both sides rendered; no display winner; resolution command shown |
| `STALE` | Greyed with its validity window stated |
| `OWNER_REQUIRED` | Labelled, never actionable |

## 5. Density (fixes A-3)

Split the single `--atlas-max: 42rem` into two tokens:

```css
--atlas-max-prose: 42rem;  /* records, briefs, answers */
--atlas-max-data: 96rem;   /* tables, kdiff, conflict sets, source health */
```

Tables scroll inside their own `overflow-x: auto` container; the page never scrolls
horizontally (verified at four viewports). Backlog `AX-007`.

## 6. Copy (fixes A-2)

Governance identifiers belong in evidence, not nav copy.

| Now | Becomes |
|---|---|
| `AS-WEB-MISSION-001 stub — UI≠canonical; ACCEPTED=YES.` | `Live view of autonomous work.` |
| `OBS/sample consume — unknown ≠ healthy.` | `Source and system health. Unknown is not healthy.` |
| Chrome badge `AS-WEB-ACCEPT · UI≠canonical · read-only` | Strip: `◆ LIVE · atlas-main` + `Read-only · UI is not canonical` |

The invariants stay visible — they move from acronyms to sentences. Work-package IDs move
to a `Build info` disclosure in `Estate → System`, where they remain fully accessible for
governance. Backlog `AX-006`.

## 7. Implementation status in this lane

| Component | Status |
|---|---|
| `truthState.ts` vocabulary + invariants | **IMPLEMENTED** |
| `TruthChip` | **IMPLEMENTED** |
| `TruthAnnouncer` + `announce.ts` | **IMPLEMENTED** |
| Truth tokens, contrast-verified | **IMPLEMENTED** |
| `useReadStatus` announcements | **IMPLEMENTED** |
| `ReadStatusPanel` on the chip system | **IMPLEMENTED** |
| Evidence Desk prototype route | **PROTOTYPED** (`/design-lab/evidence-desk`) |
| Shell, rail, status strip | `SPECIFIED` — `AX-001` |
| `CommandPalette` | `SPECIFIED` — `AX-010` |
| `EvidenceDrawer` | `SPECIFIED` — `AX-011` |
| `ClaimText` | `SPECIFIED` — `AX-012` |
| Density tokens | `SPECIFIED` — `AX-007` |
| Copy revision | `SPECIFIED` — `AX-006` |
