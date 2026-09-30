# 00 — Live Surface Audit (Atlas Experience System)

**Work package:** `D-CLAUDE-CONTINUE-CODEX-PRODUCT-DESIGN-LANE`
**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Predecessor session:** `AS-20260903T124744Z-generic-project-atlas-ad915d6d` (Codex; execution-limit reached)
**Audited tree:** `origin/main` @ `7cb927f7a56e4fc0b137447511d89b5121d2dd14` (tree `48c7c445…`)
**Method:** direct repository measurement in an isolated worktree. Every number below is
reproducible with the command shown. No number is inherited from narrative.

> Truth boundary for this document: this is an audit of **product surfaces**, not a
> statement of Atlas authority. `UI != CANONICAL`. Nothing here promotes a surface
> to Layer B authority.

---

## 1. Surface census

| Surface | Exists | Measured value | How measured |
|---|---|---|---|
| Production web routes | YES | **15** | `src/App.tsx` route list, minus lab + catch-all |
| Design-lab web routes | YES | **4** | `ls src/pages/design-lab` |
| LIVE_API read endpoints | YES | **41** | distinct `"/v1/…"` literals in `web_api/` + `api_server.py` |
| CLI top-level commands | YES | **67** | distinct `subparsers.add_parser` names |
| CLI total parsers | YES | **134** | `grep -c 'add_parser('` on `src/project_atlas/cli.py` |
| CLI `add_argument_group` calls | — | **0** | `grep -c 'add_argument_group'` |
| `cli.py` file size | — | **5,855 lines** | `wc -l src/project_atlas/cli.py` |
| First-party TUI | **NO** | 0 hits for `textual`/`urwid`/`curses`/`blessed`/`ratatui`/`bubbletea` | grep over `src/`, `pyproject.toml` |
| First-party desktop app | **NO** | 0 hits for `electron`/`tauri`/`pywebview` | grep over `package.json`, `*.toml` |
| First-party mobile app | **NO** | no RN / Capacitor / `.xcodeproj`; `apps/` holds only `web/` | `ls apps/` |

**Reconciled against the Codex checkpoint.** Codex recorded ~15 routes, 4 lab directions,
"CLI parser ~2,711 lines", "very broad" CLI, and no TUI/desktop/mobile. All of that holds.
Two figures are sharpened: the 2,711 figure is the *parser-builder function*, not the file
(5,855 lines); and "very broad" is now quantified as **67 top-level commands with zero
argument groups**.

## 2. The 15 production routes, as the user meets them

Nav order from `PROD_LINKS` in `src/components/ProdNav.tsx`:

`Home · Projects · Discovery · Knowledge · Intelligence · Context · Ask · Time Machine ·
Roadmap · Graph · Ops · Command Center · Mission Control · Workspace · Source Health`

### Finding A-1 — navigation is a flat list of subsystem names (P0)

All 15 destinations sit at one level with no grouping. At least 8 of the 15 labels are
Atlas *internal* names rather than user goals: `Context`, `Graph`, `Ops`, `Command Center`,
`Mission Control`, `Workspace`, `Source Health`, `Intelligence`. A user must already know
Atlas's subsystem decomposition to guess where a task lives.

Three label clusters are not separable from the label alone:

- `Command Center` vs `Mission Control` vs `Workspace`
- `Knowledge` vs `Intelligence` vs `Graph`
- `Ops` vs `Source Health`

**The same duplication exists one layer down**, which shows this is structural rather
than cosmetic: the CLI exposes 7 `<lens>` / `<lens>-status` pairs (`overview`, `state`,
`changed`, `decisions`, `unknown`, `roadmap`, `next`) and the API mirrors them
(`/v1/overview-status`, `/v1/state-status`, …). The information architecture is a
projection of the module list at all three layers.

### Finding A-2 — internal work-package IDs are in user-facing copy (P1)

`src/pages/HomePage.tsx` ships these as the product description of a destination:

- `"AS-WEB-MISSION-001 stub — UI≠canonical; ACCEPTED=YES."`
- `"AS-WEB-WORKSPACE-001 stub — UI≠canonical; ACCEPTED=YES."`
- `"OBS/sample consume — unknown ≠ healthy."`

`ProdNav` renders the persistent chrome badge `AS-WEB-ACCEPT · UI≠canonical · read-only`.

Governance identifiers are correct and must stay *in evidence*. As nav copy they cost the
reader the one thing a label is for. Note also that `ACCEPTED=YES` in page copy sits
uncomfortably beside ADR-010, which explicitly does **not** claim web acceptance — the
string refers to a route-stub acceptance, but nothing in the UI says so.

### Finding A-3 — reading width fights the content (P1)

`--atlas-max: 42rem` in `src/tokens.css`. 42rem (≈672px) is a prose measure. The pages it
wraps are operator tables: `IntelligencePage` (405 lines), `KnowledgePage` (377),
`TimeMachinePage` (296), `SourceHealthPage` (287). Only `signal-rack` widens it (56rem).
A conflict list, a kdiff T1→T2 comparison and a source-health matrix are each being
served a blog measure.

## 3. Truth-state presentation

This is Atlas's actual differentiator, so it was measured directly. Occurrences across
`apps/web/src`:

| Token | Occurrences | Verdict |
|---|---|---|
| `unknown` / `UNKNOWN` / `Unknown` | 182 / 85 / 9 | present, but in three casings |
| `live` / `LIVE` | 52 / 28 | present |
| `demo` / `DEMO` / `Demo` | 44 / 30 / 5 | present |
| `fixture` / `FIXTURE` | 21 / 14 | present |
| `failed` | 15 | present |
| `contested` / `CONTESTED` | 4 / 6 | **thin** |
| `stale` / `STALE` | 3 / 4 | **thin** |
| `unresolved` / `UNRESOLVED` / `Unresolved` | 2 / 1 / 3 | **thin** |
| `ready` | 1 | **not a system** |
| `blocked` | 1 | **not a system** |
| `owner_required` | 0 | **missing entirely** |
| `running` | 0 | **missing entirely** |

### Finding A-4 — the truth vocabulary is string literals, not a system (P0)

Atlas's core product claim is honest epistemic state. In the UI that claim is carried by
ad-hoc string literals in three casings, with no shared component and no shared token set.
`ReadStatusPanel.tsx` hand-rolls the only real treatment
(`className={isDemo ? "banner warn" : "banner"}`) plus a raw flag dump
(`ui_canonical=false · graph_authority=false · unknown_equals_healthy=false · …`).

Three consequences follow directly:

1. **The states Atlas most needs are the least implemented.** `CONTESTED`, `STALE`,
   `BLOCKED` are thin; `OWNER_REQUIRED` and `RUNNING` do not exist in the web layer at all.
   Atlas can compute an owner-gated state that its UI has no way to say.
2. **The `unknown != healthy` invariant has no structural home.** There is no single place
   that decides how `UNKNOWN` renders, so the invariant is defended per-file by reviewer
   vigilance rather than by the type system.
3. **The flag dump is right information in the wrong register.** It tells an operator what
   is *not* authoritative. It never tells them what they *may* rely on.

This is the highest-leverage finding in the audit: it is exactly where a design system can
convert a governance invariant into something the code enforces structurally.

## 4. Accessibility baseline

Counted across `apps/web/src`:

| Primitive | Count | Note |
|---|---|---|
| `<main` | 19 | semantic main present |
| `<nav` | 5 | semantic nav present |
| `skip-link` | 3 | present in `ProdShell` |
| `aria-label` | 63 | used well on sections |
| `aria-current` | 1 | otherwise delegated to `NavLink` |
| `<label` | 7 | for the entire app |
| **`aria-live`** | **0** | — |
| **`role="status"`** | **0** | — |
| **`role="alert"`** | **0** | — |
| **`aria-describedby`** | **0** | — |
| **`aria-invalid`** | **0** | — |
| `aria-expanded` | 0 | no disclosure widgets exist |
| `aria-activedescendant` | 0 | no combobox exists |

### Finding A-5 — WCAG 2.2 SC 4.1.3 (Status Messages) is unimplemented (P0, AA)

There are **12** live-data hooks (`src/hooks/useLive*.ts` plus `useReadStatus`,
`useOpsReceipts`, `useEstateDiscovery`) and **zero** live regions. Every one of these
transitions is therefore silent to assistive technology:

- loading → loaded, and loading → error
- LIVE_API preferred but unreachable → demo-stub fallback
- an answer arriving on `AskPage`
- a health rollup resolving to `unknown`

The second and fourth matter beyond compliance. `HomePage.tsx` correctly computes
`liveFellBackToDemo` and then renders it **purely visually** — so a screen-reader user can
read fixture data believing it is live. That is a **truth-boundary failure reached through
an accessibility gap**, not merely a missing attribute. It is the strongest argument in
this audit that accessibility and Atlas's governance model are the same problem.

### Finding A-6 — form semantics are unwired (P1, AA)

`AskPage.tsx` is the product's primary input surface. With `aria-describedby`,
`aria-invalid` and `role="alert"` all at 0 app-wide, a question that errors cannot
associate its error with its field and cannot announce it.

## 5. Responsive baseline

### Finding A-7 — one breakpoint for the whole console (P1)

`src/styles.css` contains exactly **1** media query: `@media (max-width: 720px)`.

`.prod-nav` is `display:flex; flex-wrap:wrap; font-size:0.85rem`. With 15 links plus a
badge, no priority order and no overflow affordance, narrow viewports get a multi-row
wrapped wall of subsystem names above every page. There is no `<=480px` treatment, and the
operator tables named in A-3 have no defined narrow-viewport behaviour — no column
priority, no card fallback, no scroll-container contract.

## 6. What is genuinely good (preserve)

Honest engineering worth carrying into any redesign:

- **The read-only boundary is real.** `liveApi.ts` + `web_api/` are read projections; no UI
  writer exists. The firewall is structural, not documentary.
- **The demo fallback is honestly computed.** `HomePage` derives `liveFellBackToDemo` from
  `livePreferred && dataSource === "demo_stub" && liveError !== null` rather than silently
  labelling stub data live. The *logic* is right; only its expression is invisible (A-5).
- **`unknown` is not coerced to healthy** in the read-status rollup, and `--atlas-unknown`
  is a deliberate amber token, distinct from `--atlas-ok`.
- **Design-lab is preserved by policy** — ADR-010 sets a consume/preserve rule, never delete.
- **Tokens already exist** (`tokens.css`, ADR-009) with theme remapping via `[data-theme]`.
  There is a real foundation to extend; a new framework is not needed.
- **`<main>`, `<nav>` and a skip link** are already in the shell.

## 7. Audit → node status

| Finding | Severity | Becomes |
|---|---|---|
| A-1 flat subsystem nav | P0 | `02-JOB-ORIENTED-IA.md`; backlog `AX-001` |
| A-2 governance IDs in nav copy | P1 | backlog `AX-006` |
| A-3 42rem measure for operator tables | P1 | backlog `AX-007` |
| A-4 truth vocabulary is not a system | P0 | `05-TRUTH-STATE-LANGUAGE.md`; backlog `AX-002` (**implemented**) |
| A-5 SC 4.1.3 unimplemented | P0 | backlog `AX-003` (**implemented**) |
| A-6 form semantics unwired | P1 | backlog `AX-004` |
| A-7 single breakpoint | P1 | backlog `AX-005` |
| CLI: 67 flat commands, 0 groups | P0 | `07-CLI-SPEC.md`; backlog `AX-008` |
| No TUI / desktop / mobile | — | specified only (`08`, `09`); **not** claimed as existing |
