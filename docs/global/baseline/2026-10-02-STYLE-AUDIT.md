# Studio / Mission Control Style Audit — 2026-10-02

| Field | Value |
|---|---|
| Token | `ATLAS_GLOBAL_STYLE_AUDIT_COMPLETE` |
| Observed at | `main` HEAD `7373092598d2c0ee94b9b976736b735f375b926e` (`apps/web`) |
| Measured against | [../ATLAS-GLOBAL-STYLE-MISSION.md](../ATLAS-GLOBAL-STYLE-MISSION.md) |
| Method | Read-only source audit plus `tsc -b`, `vite build`, `scripts/smoke.mjs`, `scripts/test-source-health-web.mjs` and `scripts/test-agent-context-markdown.mjs` (all pass) in an isolated copy. Playwright e2e not run |

Line numbers refer to the observed HEAD.

## Scope reality

- `apps/web` is Vite 6 + React 19 with `HashRouter`. Styling is plain CSS: one `--atlas-*`
  token set (`src/tokens.css`) with four themes. Production is forced to Ledger Desk.
- There are 15 production routes and 4 design-lab routes. **No Studio, fleet, execution or
  verification views exist** (Studio is tracked as issue #746). Reality-Gap and Web-Surface
  exist only as Python catalogs.
- CI (`.github/workflows/ci.yml`) runs no web/npm step. Web gates are pytest source checks
  plus node scripts.

## What already conforms

- Hooks fail closed with no silent demo substitution (`useLiveMissionWorkspace.ts:89-116`;
  `IntelligencePage.tsx:167-171`; `TimeMachinePage.tsx:143-149`).
- Source health keeps its state opaque and treats UNKNOWN/UNREADABLE as not healthy
  (`SourceHealthPage.tsx:16-18,172-178`). Ops forces `unknown` when unavailable
  (`OpsHealthPage.tsx:18-19`).
- LIVE/DEMO/FIXTURE modes are visible with `aria-pressed` (`LensModeSwitcher.tsx:47-58`).
- No confidence-score theatre (`IntelligencePage.tsx:205,273`).
- Project-mismatch guards on four lenses. A working skip link (`ProdShell.tsx:24-27`).

## Principal findings

| # | Mission ref | Finding | Evidence |
|---|---|---|---|
| 1 | SM-SEPARATION | **Selection rendered as green.** In production, `--atlas-ok` is used only for the active LIVE mode button, so it stays green while LIVE has failed | `styles.css:106-110`; failure state exercised in `e2e/mission-control.acceptance.spec.ts:50-60` |
| 2 | SM-SEPARATION | Reachability becomes truth: `classifyIntelligenceTruth` returns `LIVE` whenever the source is `live_api` and no honesty class is present; `OBSERVED` maps to LIVE | `useLiveIntelligence.ts:98-103` |
| 3 | SM-SEPARATION | CSS collapses truth states: `.truth-live` = `.truth-derived`; `.truth-stale` = `.truth-http_failure` (freshness looks like connectivity failure); `.truth-valid_empty` = unknown | `styles.css:431-453` |
| 4 | SM-TRUTH | Missing data rendered as zero or clean, in 8 places. Examples: Knowledge counts `?? 0` with "No pending reviews recorded" when `truth` is undefined; Roadmap "No derived blockers" when data is null; Ops claims "no ops receipts on disk" on fetch error; ReadStatusPanel defaults a missing `data_source` to LIVE_API | `KnowledgePage.tsx:225-233,254-256,271-273`; `RoadmapPage.tsx:142-143,185-186`; `OpsHealthPage.tsx:88-91`; `ReadStatusPanel.tsx:10,18-21`; `DiscoveryPage.tsx:89-91`; `MissionControlPage.tsx:93,97`; `useLiveMissionWorkspace.ts:102-104` |
| 5 | SM-TRUTH | Contradictory hard-coded acceptance claims ("not WEB ACCEPTED" vs "WEB APPLICATION ACCEPTED = YES") | `HomePage.tsx:40,45,79-80,103`; `CommandCenterPage.tsx:59`; `MissionControlPage.tsx:107`; `WorkspacePage.tsx:114`; `OpsHealthPage.tsx:129` |
| 6 | SM-PROVENANCE | No fetched-at / as-of / freshness rendered anywhere. API fields dropped (`freshness`, `authority_role`, `verified`). Project pickers on LIVE lenses filled from the demo stub without a DEMO label | `useLiveTimeMachine.ts:39-40`; `useLiveBrief.ts:47`; `useReadStatus.ts:68-78` |
| 7 | SM-SYSTEM | Five unshared status vocabularies; no shared status component; project selector copy-pasted 6×; three tab implementations; 39 inline style objects; `.theme-hub` reused as a data list | `types.ts:1`; `useLiveIntelligence.ts:14-26`; `CommandCenterPage.tsx:65-72` |
| 8 | Character | Marketing hero on operator pages; a 42rem single column; the invariant boilerplate repeated on 12 pages; `banner warn` (97 uses) used for both disclaimers and real failures | `styles.css:153-158`; `tokens.css:28`; `MissionControlPage.tsx:37-71` |
| 9 | Navigation | 15 flat nav links; the Home hub lists 8 of 14 production routes; README route table stale; three different project-binding policies; no per-route titles | `ProdNav.tsx:3-19`; `HomePage.tsx:29-62`; `apps/web/README.md:29-37`; `index.html:6` |
| 10 | Accessibility | Ledger `--atlas-unknown` is 4.06–4.43:1 (below AA) on paper and hero; warn and unknown hues only 11–16° apart; no `:focus-visible` beyond the skip link; Command Center and Intelligence tabs lack `aria-pressed`/`aria-current` | `tokens.css`; `styles.css:78`; `IntelligencePage.tsx:149-157` |

## Ranked Style WorkItems

| ID | Title | Outcome | Size | Risk |
|---|---|---|---|---|
| STYLE-001 | LIVE selected ≠ green | No `--atlas-ok` for selection or "listed"; smoke and pytest gates | S | Very low — **implemented as a separate draft PR** |
| STYLE-002 | Missing ≠ zero/clean sweep | Missing renders UNKNOWN; error/null renders "unavailable"; browser-forced constants labelled as UI policy | S–M | Low (check pinned strings) |
| STYLE-003 | Shared status vocabulary + `StatusChip` | One module with separate axes (connectivity, freshness, liveness, health, execution, verification, certification, authorization), each with UNKNOWN/STALE/BLOCKED | M | Low–Med |
| STYLE-004 | Provenance strip | Per-lens source, endpoint, fetched-at, binding, mode; render the dropped fields; label demo-filled pickers | M | Low |
| STYLE-005 | One truthful acceptance claim | A single sourced statement that cites the governor sign-off | S code / M governance | Med (pinned strings) |
| STYLE-006 | Hierarchy and boilerplate consolidation | Persistent truth-boundary bar; warn reserved for real degradation; data first | M | Med |
| STYLE-007 | Navigation and project-binding coherence | Grouped nav (Observe / Understand / Operate / Lab), shared `ProjectSelector`, one binding policy, route titles | M | Low |
| STYLE-008 | Contrast and focus tokens | AA on every surface; clearly separated warn/unknown hues; `:focus-visible` | S | Low |

STYLE-003 is the structural change that makes SM-SEPARATION enforceable. STYLE-001, 002 and
008 are the cheapest truthfulness wins.
