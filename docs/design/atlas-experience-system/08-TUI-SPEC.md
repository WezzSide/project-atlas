# 08 — TUI Specification

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Status:** `SPECIFIED` — **no first-party TUI exists.** Backlog `AX-009`.

> **Truth boundary, stated first because it is the one most easily blurred.** The audit
> searched for `textual`, `urwid`, `curses`, `blessed`, `ratatui` and `bubbletea` across
> `src/` and `pyproject.toml` and found **zero** matches. Atlas has no TUI. This document
> specifies one. Nothing in it may be read, quoted or summarised as describing existing
> functionality.

---

## 1. Should this be built at all?

Recorded honestly, because the directive asks for implementation-ready specification
"rather than falsely claiming existing functionality" — and the prior question is whether
it is warranted.

**For:** Atlas's users are terminal-resident. The CLI already returns rich structured data
that a one-shot command renders poorly — conflict sets, kdiff comparisons, source-health
matrices. R-5's persistent multi-panel pattern fits that data far better than repeated
invocations. And the Evidence Desk shell (`04`) ports onto it almost directly.

**Against:** it is a new surface with a new dependency, new tests and a new maintenance
burden, on a project whose web surface has a P0 accessibility gap and a 67-command CLI
with no grouping. `AX-003` and `AX-008` deliver more value per unit of risk.

**Recommendation: build `AX-008` (CLI areas) first, then reassess.** A TUI over a
well-grouped CLI is a much smaller job than a TUI over 67 flat commands, because the areas
become the panes. This spec is therefore written to be *ready*, not to be urgent — and
prioritised P3 in the backlog accordingly.

## 2. Layout — drill-down stack with a persistent provenance line

R-5 offers three patterns. Atlas's data is hierarchical (estate → project → claim →
evidence), which is the drill-down stack's exact shape (k9s), so that is the base — with
the persistent-panel rule layered on: **panes never rearrange without explicit user
action, because spatial memory is the navigation.**

```
┌ atlas ─────────────────────────────────────────── ◆ LIVE · atlas-main ┐
│ AREAS          │ PROJECT: atlas                                        │
│ ▸ Needs you  4 │ ┌─ Open questions ───────────────────────────────────┐│
│   Projects  12 │ │ ? UNKNOWN     deployment target                    ││
│   Ask          │ │ ⇄ CONTESTED   minimum python version    2 sources  ││
│   Activity   2 │ │ ⧗ STALE       index freshness    valid to 08-30    ││
│   Estate       │ │ ⚿ OWNER REQ.  promote AS-OPT-GATE-001              ││
│                │ └────────────────────────────────────────────────────┘│
│                │ ┌─ Evidence ─────────────────────────────────────────┐│
│                │ │ pyproject.toml        3.11    ingested 2026-08-14  ││
│                │ │ docs/plan.md          3.12    ingested 2026-07-02  ││
│                │ │ neither is authoritative — atlas review decide     ││
│                │ └────────────────────────────────────────────────────┘│
├───────────────────────────────────────────────────────────────────────┤
│ ◆ LIVE  vault=atlas-main  UI≠canonical  read-only                     │
│ ↑↓ move  ⏎ open  : jump  / filter  e evidence  ? help  q quit         │
└───────────────────────────────────────────────────────────────────────┘
```

Three fixed regions: **area rail** (left, the six areas from `07` §3), **content stack**
(right, drills down in place), and **the provenance line + keybar** (bottom, always).

## 3. The provenance line — a pane role the reference TUIs do not have

The second-to-last row is permanent and always states the read plane:

```
◆ LIVE  vault=atlas-main  UI≠canonical  read-only
◇ DEMO FIXTURE  isolated sample data — not a live vault
```

This is the TUI equivalent of the Evidence Desk status strip, and it exists because no TUI
in the reference set has to render *"this pane is showing you fixture data"* (`01` R-5).
It is not scrollable and not dismissible. If Atlas cannot determine the plane, it renders
`? UNKNOWN plane` — never `LIVE`.

## 4. Keybindings — three-tier disclosure

Per R-5, and reusing the CLI areas so one mental model covers both surfaces.

**Tier 1 — always visible in the keybar:** `↑↓` move · `⏎` open · `:` jump · `/` filter ·
`e` evidence · `?` help · `q` quit.

**Tier 2 — the `?` overlay:** the full table, grouped by the same six areas.

**Tier 3 — `:` jump:** the TUI's answer to the 67-command surface, and the direct
counterpart of the web command palette. `:unknown`, `:kdiff`, `:source-health` jump
straight to a view. Like the palette, **rows are typed by authority**, and an owner-gated
entry is displayed as a state, never as an executable jump.

## 5. Constraints that are non-negotiable

- **Monochrome-correct.** Every state renders glyph + label (`05`). The test is R-5's:
  strip all colour and the TUI must remain fully usable. Given Atlas's `NO_COLOR` posture
  and the CLI spec, this is a hard requirement, not an aspiration.
- **Read-only.** The TUI observes. It may *print* the command that would act
  (`atlas review decide …`) but must not execute a write. `READ_ONLY_UI != EXECUTION_AUTHORITY`
  applies to a terminal surface exactly as it does to a browser one.
- **Async everything** (R-5). A keypress during a load cancels or supersedes it. A slow
  LIVE_API read must never block input.
- **No invented aggregation.** If a rollup cannot be computed, the pane shows
  `? UNKNOWN`, never an optimistic default.

## 6. Technology

**Recommendation: [Textual](https://textual.textualize.io/).** Atlas is a Python project
with a Python CLI; a Python TUI shares the `web_api` read layer and domain models directly
and adds no second toolchain. The alternative — a Go/Rust TUI over the JSON API — buys
startup speed at the cost of a second language in the build, which is a poor trade for a
P3 surface.

Package as an optional extra so the core install is unchanged:

```toml
[project.optional-dependencies]
tui = ["textual>=0.80"]
```

Entry point `atlas tui`, which prints an actionable install hint if the extra is absent —
matching the error standard in `07` §6.

## 7. Acceptance criteria for `AX-009`

1. `atlas tui` launches and renders the three fixed regions.
2. The provenance line is present in every view and states the plane, or `UNKNOWN`.
3. All 13 truth states render with glyph + label and are legible with colour disabled.
4. `?` lists every binding; `:` jumps to at least the six areas.
5. No code path performs a vault write; verified by a test asserting the read-only API
   surface is the only one imported.
6. Panes do not rearrange without explicit user action.
7. Terminals down to 80×24 render without clipping the provenance line.
