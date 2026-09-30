# 03 — Three Design Directions

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`

Three directions that differ in **information architecture, hierarchy, navigation model,
density, interaction philosophy, visual language, truth-state presentation, and
operational mental model** — not in colour. Each takes a different position on one
question:

> **When Atlas does not know something, what should the product do with that fact?**

- **Direction 1 — Dossier** treats it as a *gap in a document*.
- **Direction 2 — Standing Watch** treats it as an *item in a queue*.
- **Direction 3 — Interrogation Room** treats it as a *question under examination*.

All three preserve the existing lab themes (ADR-010 consume/preserve) and all three carry
`UI != CANONICAL`, `GRAPH != AUTHORITY`, `UNKNOWN != HEALTHY`, `DEMO != LIVE`,
`FIXTURE != AUTHENTIC`, `READ_ONLY_UI != EXECUTION_AUTHORITY`.

---

## Direction 1 — **Dossier**

| Field | Value |
|---|---|
| **NAME** | Dossier |
| **DESIGN_THESIS** | Atlas compiles a document about your project. Read it like one. Uncertainty is a visible *hole in the page*, in the place where the answer would have been. |
| **PRIMARY_USER** | The person who must *understand* a project — a lead returning after time away, someone inheriting a codebase, a reviewer forming a judgement. |
| **CORE_METAPHOR** | A compiled case file with redaction marks. |
| **NAVIGATION_MODEL** | Document-spine. A single scrolling project record with a sticky section rail (Knows / Open questions / Change / Handoff). Depth is reached by expanding in place, never by leaving the page. Nav is 5 items; palette carries the rest. |
| **HOME_MODEL** | A written standing summary: "Atlas knows N claims across M projects. K are contested. J need you." Prose first, tiles second. |
| **PROJECT_MODEL** | One long authored-feeling record. Sections are `<section>` landmarks, not tabs — the whole record is printable and linkable at any heading. |
| **KNOWLEDGE_MODEL** | Claims as body text with inline citation superscripts. Clicking a citation opens the evidence drawer beside the text without moving scroll position. |
| **ASK_MODEL** | Ask writes into the document: the answer is appended as a new dated, cited section, so asking *extends the dossier* rather than opening a chat. |
| **AUTONOMY_MODEL** | An appendix — a "work log" section at the end of the record. Deliberately low-prominence: agent activity is history, not headline. |
| **OPERATIONS_MODEL** | A separate thin `Estate` area. Ops is explicitly *not* part of the document. |
| **TRUTH_STATE_MODEL** | **Redaction.** `UNKNOWN` renders as a struck, hatched slot in the sentence where the fact belongs, labelled `UNKNOWN`. `CONTESTED` renders as two stacked variants with both sources shown, neither styled as the winner. `STALE` renders as greyed body text with a valid-until note. Truth state is *typographic*, so it survives loss of colour by construction. |
| **DESKTOP_BEHAVIOR** | Two columns: document + persistent evidence drawer. Drawer is pinned, not modal. |
| **MOBILE_BEHAVIOR** | Single column; the section rail collapses to a sticky progress bar; evidence drawer becomes a bottom sheet. Degrades genuinely well — it is already a document. |
| **CLI_RELATIONSHIP** | `atlas brief` is the same artifact. The web page is the CLI's output, styled. Strongest CLI/web coherence of the three. |
| **TUI_RELATIONSHIP** | Weak. A long-form document is the wrong shape for a TUI; the TUI would have to diverge into a different IA. |
| **STRENGTHS** | Best learnability for newcomers; truth states legible without colour by construction; excellent print/share; smallest new-component count; strong `01`/R-4 alignment (provenance inline, not a destination). |
| **RISKS** | Poor for monitoring — answering "what changed in the last hour" means scrolling a document. Low information density (worsens A-3 if not carefully bounded). Scales badly past ~50 claims per section. |
| **IMPLEMENTATION_COST** | **Low.** Reuses existing tokens and page structure; mostly typographic. |
| **ATLAS_DIFFERENTIATION** | High and unusual — no competitor renders uncertainty as redaction. It makes `no claim without a traceable source` visible at a glance. |

---

## Direction 2 — **Standing Watch**

| Field | Value |
|---|---|
| **NAME** | Standing Watch |
| **DESIGN_THESIS** | Atlas is an instrument that runs continuously. The product's job is to tell you, at a glance, what needs a human — and to be quiet otherwise. Uncertainty is *work*, so it belongs in a queue. |
| **PRIMARY_USER** | The operator running autonomous work — the person who checks Atlas several times a day and needs the delta, not the story. |
| **CORE_METAPHOR** | A watch station / NOC board. |
| **NAVIGATION_MODEL** | Persistent left rail (5 areas) + fixed status strip across the top carrying live/demo plane, vault identity, and open owner-gate count. Content region swaps; chrome never moves. Spatial constancy is the navigation (R-5). |
| **HOME_MODEL** | **The queue.** "Needs you" (J-8) at the top as actionable rows with owner-gate state; then "Changed since your last visit"; then a quiet all-clear state. Home answers a question instead of listing links. |
| **PROJECT_MODEL** | Dense dashboard: claim counts, conflict count, staleness distribution, last-ingest time, source health rollup. Tables with column priority, not prose. |
| **KNOWLEDGE_MODEL** | Filterable virtualised table of claims. Facets on state (`OK`/`UNKNOWN`/`CONTESTED`/`STALE`), source, valid-time. Built for scanning hundreds of rows. |
| **ASK_MODEL** | A panel in the workspace, with results as a rows-with-citations result set rather than prose. Query history persists. |
| **AUTONOMY_MODEL** | **First-class and prominent** — a live Activity area with run state, receipts, and an explicit `RUNNING` / `BLOCKED` / `OWNER_REQUIRED` breakdown. This is the direction that finally implements the two states the audit found missing entirely. |
| **OPERATIONS_MODEL** | Native. Estate → Sources / System are peers of everything else. |
| **TRUTH_STATE_MODEL** | **Chips + a persistent plane indicator.** Every value carries a compact chip (glyph + short label + token colour). The top strip always states the read plane (`LIVE` vs `DEMO FIXTURE`), so plane is ambient rather than per-page. `UNKNOWN` uses amber and a `?` glyph and is never aggregated into a green rollup. |
| **DESKTOP_BEHAVIOR** | Excellent — rail + wide table + optional right drawer. Wants ≥1200px. |
| **MOBILE_BEHAVIOR** | Rail collapses to a bottom tab bar; tables switch to a card list with a declared column-priority order. Requires real work, and is the direction most at risk on small screens. |
| **CLI_RELATIONSHIP** | Good — maps to `atlas attention`, `atlas next`, `atlas ops health`. Web and CLI answer the same question in the same shape. |
| **TUI_RELATIONSHIP** | **Strongest of the three.** The persistent multi-panel pattern (R-5) is a direct port; the rail becomes panes and the status strip becomes the footer. |
| **STRENGTHS** | Directly serves R-1 ("the dashboard is the product") and the most under-served job (J-8). Highest information density. Best fit for Atlas's autonomous-work reality. Cleanest cross-surface story with the TUI. |
| **RISKS** | Highest implementation cost. A dense board can imply *control* over work it can only observe — must be defended hard against `READ_ONLY_UI != EXECUTION_AUTHORITY`. Newcomers get numbers before understanding. Risks looking like every other ops console (weakest differentiation). |
| **IMPLEMENTATION_COST** | **High.** New shell, virtualised tables, responsive table strategy, live status strip. |
| **ATLAS_DIFFERENTIATION** | Medium. The *shape* is conventional; only the truth-state chips and owner-gate row are distinctly Atlas. |

---

## Direction 3 — **Interrogation Room**

| Field | Value |
|---|---|
| **NAME** | Interrogation Room |
| **DESIGN_THESIS** | The unit of the product is not a page or a row — it is a **question**. Atlas exists to be asked things and to answer with evidence or an honest refusal. Everything else is scaffolding around one question at a time. |
| **PRIMARY_USER** | The investigator — someone with a specific doubt to resolve, and agents that need bounded, cited context. |
| **CORE_METAPHOR** | A deposition: a question, an answer, the evidence behind it, and an explicit record when the witness does not know. |
| **NAVIGATION_MODEL** | **Palette-first.** There is barely a nav. `Ctrl/Cmd+K` is the primary interface (R-3); the visible chrome is a breadcrumb of the current line of inquiry. Navigation is *asking*, not clicking. |
| **HOME_MODEL** | A single input, centred, with the open questions Atlas already holds listed beneath it — Atlas's own unknowns as suggested starting points. |
| **PROJECT_MODEL** | A project is a *scope filter* on questioning, not a destination with tabs. Selecting a project narrows the palette and the answer scope. |
| **KNOWLEDGE_MODEL** | Knowledge is only ever seen as the answer to a question. There is no browse-all view by default — you must ask, though "what do you know about X" is itself a question. |
| **ASK_MODEL** | **The entire product.** Answer is a three-part response, always: `CLAIM` · `EVIDENCE` · `CONFIDENCE-STATE`. An answer without citations renders as `UNKNOWN`, structurally — the component cannot render a bare claim. |
| **AUTONOMY_MODEL** | Agent work appears as *questions Atlas is currently answering for itself*, plus questions it has escalated to the owner. Elegant reframing of J-7/J-8 into one model. |
| **OPERATIONS_MODEL** | Weakest. Ops is a question you can ask ("is the system healthy") but there is no natural monitoring surface. |
| **TRUTH_STATE_MODEL** | **The answer's verdict line.** Each response carries one of `ANSWERED (n sources)`, `UNKNOWN (no source)`, `CONTESTED (n competing sources)`, `STALE (valid until T)`, `OWNER_REQUIRED`. The state is the *headline of the response*, not a chip on a value — it is impossible to read an answer without reading its epistemic status. |
| **DESKTOP_BEHAVIOR** | Centred column with an evidence panel that slides in per answer. |
| **MOBILE_BEHAVIOR** | **Best of the three.** One input and one answer is inherently a phone shape. |
| **CLI_RELATIONSHIP** | Excellent conceptually — `atlas ask2` is the same object, and the palette is `atlas <command>` with discoverability. |
| **TUI_RELATIONSHIP** | Medium — maps to a REPL, which is a real TUI shape but not the panel shape R-5 recommends for hierarchical data. |
| **STRENGTHS** | The most *honest* direction — makes `MODEL OUTPUT != AUTHORITY` and `UNKNOWN stays UNKNOWN` structurally unavoidable rather than a rule to remember. Highest differentiation. Solves the 67-command breadth problem directly. Best mobile. |
| **RISKS** | **Discoverability is the core risk**: a user who does not know what to ask is stranded, and Atlas's value is partly in showing what you did not know to ask. Poor for monitoring and for browsing. Palette-only nav is a real accessibility burden if the combobox is not implemented exactly (R-3/R-6). Weakest for J-6/J-7/J-10. |
| **IMPLEMENTATION_COST** | **Medium.** Few surfaces, but the palette and the answer component must both be excellent, and the palette must be a correct ARIA combobox. |
| **ATLAS_DIFFERENTIATION** | **Highest.** Nothing in the researched market makes epistemic status the headline of every response. |

---

## Why these three are genuinely distinct

| Axis | Dossier | Standing Watch | Interrogation Room |
|---|---|---|---|
| Unit of product | the record | the queue item | the question |
| Nav model | document spine | persistent rail + strip | palette / no nav |
| Density | low | high | low but deep |
| Truth state lives in | typography (redaction) | chips + ambient strip | the answer's verdict line |
| Primary user | understander | operator | investigator |
| Reading mode | linear | scanning | iterative |
| Best job | J-1 | J-8 / J-7 | J-2 / J-4 |
| Worst job | J-7 | J-1 | J-6 |
| TUI fit | weak | strongest | medium |
| Mobile fit | good | hardest | best |
| Cost | low | high | medium |
| Differentiation | high | medium | highest |

They disagree about the product, not about the palette. Adopting one instead of another
changes which job Atlas is best at — which is the property the directive asked for.
