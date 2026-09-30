# 01 — Market & Pattern Research

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Research date:** 2026-09-03
**Recovery note:** the Codex specialist research lanes
(`AS-20260903T124913Z-codex-market-research-…`,
`AS-20260903T124919Z-accessibility-form-research-…`) registered governed sessions but
recorded **no findings** — their receipts contain a `session-start` event only, with
`normalized: 0, routed: 0, verified: 0`. Classification: **NOT_RECOVERABLE**. The research
below was therefore reconstructed, not duplicated.

> Every influence is separated into `OBSERVED_PATTERN` (what the market does),
> `ATLAS_ADAPTATION` (what Atlas should take) and `ATLAS_DIFFERENTIATOR` (what Atlas must
> do that the market does not). This is a design input, not a competitor catalogue.

---

## R-1 — Agent orchestration & operator trust

**OBSERVED_PATTERN.** The 2026 consensus is that the hard parts of orchestration are not
the agent loops but *memory propagation, retry semantics, observability, and
human-in-the-loop gating* — and that "a trace dashboard you actually use beats a fifth
agent every time." Enterprise agent-platform architectures converge on a named set of
planes including **human-in-the-loop gates**, **evaluation loops**, and an explicit
**audit plane**. The recurring advice on gates is to build them before you need them:
the first time an agent does something you wish it hadn't, you want the gate already there.

**ATLAS_ADAPTATION.** Atlas already *has* the audit plane (receipts, provenance, session
evidence) and the owner gate (`ATLAS_OPT_WAKE_GATE`, promote-eligibility). What it lacks is
the *operator-facing view* of them. The market says the dashboard is the product; Atlas
has built the substrate and skipped the dashboard. This directly motivates a first-class
"what is waiting on me" surface (see `02` job J-8) rather than a `Mission Control` /
`Command Center` / `Workspace` triple that names planes without answering the question.

**ATLAS_DIFFERENTIATOR.** Every product in this category presents agent output as
*results*. Atlas's model is that model output is **not** authority
(`MODEL OUTPUT != AUTHORITY`, `PROMOTE_ELIGIBLE != MERGED/AUTHORITATIVE`). No mainstream
orchestration console has a visual language for "the agent produced this and it is still
not true." That gap is Atlas's opening, and it is a **design** opening, not an engineering
one — the backend already computes the distinction.

## R-2 — CLI design: grouping and discoverability

**OBSERVED_PATTERN.** The `clig.dev` guidelines and Microsoft's `System.CommandLine`
guidance agree on one structural rule: *if a command has subcommands, the parent should be
an **area** or grouping identifier, not an action* — and defining areas exists specifically
to organise help output. Discoverability comes from a consistent naming scheme plus
per-command help. Mature CLI design is described as four layers: **Contract** (commands,
flags, exit codes, I/O), **UX** (help, errors, feedback, discoverability, tone), **UI**
(colour, spacing, terminal components), and **Documentation**.

**ATLAS_ADAPTATION.** Atlas's CLI violates the one structural rule at scale: **67
top-level commands and 0 `add_argument_group` calls** (audit §1). Many of those 67 are
actions that should be verbs under an area — the 7 `<lens>-status` commands are the
clearest case, since `overview-status` is `overview` in another output mode, not a
different command. `07-CLI-SPEC.md` applies the area rule with a strict backward-compatible
aliasing contract, because Atlas's existing commands are referenced by governance
documents and cannot simply be renamed.

**ATLAS_DIFFERENTIATOR.** The four-layer model has no layer for *epistemic status of
output*. Atlas needs a fifth: exit codes and output must distinguish "I answered" from "I
do not know" from "sources conflict" — and `UNKNOWN` must not be an error. A conventional
CLI has two outcomes; Atlas has at least four.

## R-3 — Command palette as the answer to breadth

**OBSERVED_PATTERN.** `Cmd/Ctrl+K` is now standard for power-user products — Linear,
Vercel, GitHub, Slack, Raycast. It is specifically recommended for products with *a large
number of features, complex navigation structures, and keyboard-first users*. Palettes
surface keyboard shortcuts inline so users learn them, and commonly show recent commands
first. Structurally, a command palette *is* the ARIA **combobox** pattern: a text input
owning a `listbox` popup, input retains focus, and `aria-activedescendant` points at the
highlighted row.

**ATLAS_ADAPTATION.** This is the single highest-value pattern for Atlas, because Atlas's
breadth is real and should not be reduced by deletion. 15 routes + 41 endpoints + 67
commands is precisely the profile the pattern exists for. The palette lets the visible nav
shrink to a small job-oriented set (`02`) **without** hiding capability: everything stays
one keystroke away, and the palette becomes the place where breadth is a strength instead
of a wall. Note the audit measured `aria-activedescendant` at 0 — the correct
implementation contract is specified in `06-WEB-SPEC.md` rather than assumed.

**ATLAS_DIFFERENTIATOR.** A palette row in Atlas must carry its **authority boundary**,
not just its label — a read lens and an owner-gated action cannot look alike in the same
list. Mainstream palettes rank by recency and fuzzy score; Atlas must additionally never
let an owner-gated or write-adjacent entry be *accidentally* activated by muscle memory.
Palette rows are typed by authority, and read-only rows are the default.

## R-4 — Provenance, staleness and trust signalling

**OBSERVED_PATTERN.** The data-catalog field has converged on attaching quality signals
*to lineage nodes*, so that tracing upstream shows "which datasets are failing assertions
right now, which are stale, and which have active incidents, all in the lineage view."
The field also draws a distinction Atlas should adopt verbatim: **lineage** organises
history as a path through systems and transformations; **provenance** organises it as a
record of *origin, custody, context and trust*. Lineage cannot tell you whether a source
was authoritative or whether you should trust it for the use at hand — "those questions
belong to provenance." A stale catalog actively misleads: it points users at the wrong
asset and leaves AI systems without the context to interpret data responsibly.

**ATLAS_ADAPTATION.** This validates Atlas's `Graph != authority` boundary from an
independent direction — and it condemns the current `Graph` route as a *navigation
destination*. A graph is a lineage view; the operator's question is a provenance question.
So provenance belongs **inline, attached to each claim**, not parked behind a top-level
`Graph` link. Staleness must be a first-class rendered state (audit found `stale` at 3–4
occurrences and no token).

**ATLAS_DIFFERENTIATOR.** Catalog tools show trust as *aggregate social signal* — usage
counts, steward reviews, popularity. Atlas explicitly rejects that: `no subjective trust
scores` (`AGENTS.md`), `no claim without a traceable source`. Atlas therefore needs a trust
presentation built from **evidence and authority precedence** rather than popularity. That
is a genuinely different visual problem: not a 0–100 score badge, but a citation the
operator can open, plus an explicit conflict state when sources disagree. This is the
sharpest differentiator found in the research.

## R-5 — TUI patterns (for the specified, not-yet-existing TUI)

**OBSERVED_PATTERN.** Three named layouts dominate: **persistent multi-panel** (lazygit,
btop) where everything is visible in fixed positions and *panels never rearrange without
explicit user action, because the user's spatial memory is the navigation*;
**Miller columns** (yazi, ranger) for hierarchical data — parent / current / preview;
and **drill-down stack** (k9s) — cluster → namespace → pod → logs, with `:resource` jumps
for power users. Keybinding discoverability is solved by **three-tier progressive
disclosure**: a footer showing the 3–5 most important keys always visible, a `?` overlay
with the full reference on demand, and full docs beyond that. Two 2026 rules: *async
everything* — animation must never delay input, and a keypress mid-transition cancels it;
and *semantic colour* — if the app becomes unusable stripped of colour, the design is
broken; colour reinforces a hierarchy already carried by layout, type and symbols.

**ATLAS_ADAPTATION.** The semantic-colour rule is the governing constraint for Atlas's
truth states and it applies to **every** surface, not just the TUI: `UNKNOWN` and
`CONTESTED` must be distinguishable without colour. That is why `05-TRUTH-STATE-LANGUAGE.md`
gives every state a **glyph and a text label** as well as a token, and why the web chips
carry the label rather than colour alone. The drill-down stack maps cleanly onto Atlas's
real hierarchy (estate → project → claim → evidence), so `08-TUI-SPEC.md` adopts it with a
`:` jump for the 67-command surface.

**ATLAS_DIFFERENTIATOR.** No TUI in the reference set has to render *"this pane is showing
you fixture data"* or *"this value is contested"*. Atlas's TUI needs a persistent
provenance line, which is a genuinely new pane role.

## R-6 — Accessible status messaging (reconstructing the lost a11y lane)

**OBSERVED_PATTERN.** WCAG 2.2 (Oct 2023) is the current benchmark; **SC 4.1.3 Status
Messages** is the relevant criterion. `aria-live="polite"` / `role="status"` for
non-urgent updates; `aria-live="assertive"` / `role="alert"` for critical, time-sensitive
ones. The defining requirement is that a status message be announced **without moving
keyboard focus**, so the user continues their task while being informed. The critical
implementation detail: *the live region must already exist in the DOM before the content
is inserted* — adding `aria-live` at the same moment as the message does not reliably
announce.

**ATLAS_ADAPTATION.** This maps onto audit finding A-5 exactly, and the "region must
pre-exist" detail dictates the implementation: a permanently mounted announcer in the
shell, not a conditionally rendered banner. That is what `TruthAnnouncer` in
`06-WEB-SPEC.md` implements — mounted unconditionally by `ProdShell`, written to on
transition.

**ATLAS_DIFFERENTIATOR.** The research explicitly returned *no* established accessible
pattern for communicating **data provenance and uncertainty**. So Atlas is designing into
open space here, and must be conservative: state the epistemic status in text inside the
live region (`"Now showing DEMO FIXTURE data — live vault unreachable"`), because there is
no convention a screen-reader user could be expected to already know. The severity mapping
follows from Atlas's own invariants rather than from a precedent: a LIVE→DEMO fallback is
`role="alert"` (assertive) because it changes what the data *means*, while a routine
load completing is `role="status"` (polite).

---

## Synthesis: four inputs that shaped the directions

1. **Breadth is not the bug; flatness is** (R-2, R-3). Atlas should keep 67 commands and
   41 endpoints, and stop making the user navigate them as a flat list. This is why no
   direction in `03` proposes deleting capability.
2. **The audit plane is the product** (R-1). Atlas's rarest asset — receipts, provenance,
   owner gates — is the thing its UI most under-serves.
3. **Provenance is inline, not a destination** (R-4). This retires `Graph` as a top-level
   nav item and attaches evidence to claims.
4. **Truth state must survive the loss of colour, and must reach assistive tech** (R-5,
   R-6). Glyph + label + token, and a pre-mounted live region. Accessibility here *is*
   truth-boundary enforcement, not a parallel concern.

## Sources

- [AI Agent Orchestration Patterns — Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Enterprise AI Agent Platform Architecture Patterns 2026 — vdf.ai](https://vdf.ai/blog/enterprise-ai-agent-platform-architecture-patterns-2026/)
- [AI Agent Orchestration in 2026: Patterns, Tools, Architecture — amux](https://amux.io/guides/ai-agent-orchestration-2026/)
- [AI Agent Orchestration: Patterns and Architecture — Mastra](https://mastra.ai/articles/ai-agent-orchestration)
- [Command Line Interface Guidelines (clig.dev) — cli-guidelines/cli-guidelines](https://github.com/cli-guidelines/cli-guidelines)
- [Command-line design guidance for System.CommandLine — Microsoft Learn](https://learn.microsoft.com/en-us/dotnet/standard/commandline/design-guidance)
- [cli-design-system — fvena/cli-design-system](https://github.com/fvena/cli-design-system)
- [Command Palette Pattern — UX Patterns for Developers](https://uxpatterns.dev/patterns/advanced/command-palette)
- [Command Palette UI Design: best practices & variants — Mobbin](https://mobbin.com/glossary/command-palette)
- [Build a Command Palette: Cmd+K like Linear and Vercel — techinterview](https://www.techinterview.org/post/3233475212/build-command-palette-cmd-k/)
- [Data Lineage vs Data Provenance — DataHub](https://datahub.com/blog/data-lineage-vs-data-provenance/)
- [Data Catalog: Modern Guide to Discovery, Governance & AI Readiness — Snowflake](https://www.snowflake.com/en/data-governance/data-catalog/)
- [Data Lineage for AI: How Tracing Data Origins Improves Accuracy — Alation](https://www.alation.com/blog/data-lineage-ai-model-accuracy/)
- [The Terminal Renaissance: Designing Beautiful TUIs in the Age of AI — Hyperbliss](https://hyperbliss.tech/blog/2026.04.04_terminal-renaissance/)
- [tui-design-skill — gfargo/tui-design-skill](https://github.com/gfargo/tui-design-skill)
- [WCAG 2.2: 4.1.3 Status Messages — Calling All Minds](https://callingallminds.com/resources/wcag/4.1.3-status-messages)
- [Use ARIA to announce updates and messaging — Centre for Excellence in Universal Design](https://universaldesign.ie/communications-digital/web-and-mobile-accessibility/web-accessibility-techniques/developers-introduction-and-index/use-aria-appropriately/use-aria-to-announce-updates-and-messaging)
- [Accessible error messages: the complete guide — AIOps Group](https://aiopsgroup.com/accessible-error-messages/)
