# Project Atlas — Coder Alpha North Star

| Field | Value |
|---|---|
| Directive | `D-PROJECT-ATLAS-CODER-ALPHA-DOC-ANCHOR-037` (supplements D-035 / D-036) |
| Status | **CURRENT PRODUCT DIRECTION** |
| Audience | AI-assisted / vibe coders + Atlas maintainers |
| Precedence | Owner directives > runtime/`main` truth > stable invariants > historical roadmaps |

This document is the durable **Atlas 2.x / Coder Alpha** product anchor.
Historical planning docs remain evidence; they do **not** override this
direction.

**D-191 successor program:** Atlas 3.0 (`docs/atlas-3/NORTH-STAR.md`) extends
the Coder Alpha promise (“never explain your project to an AI twice”) into a
verifiable shared-reality / digital-twin program. Coder Alpha is not erased.
Atlas 3 must reuse, not rebuild, the foundations this document describes.

---

## 1. User problem

AI-native builders re-explain the same project to every new Cursor / Claude /
Codex / ChatGPT session. Decisions, unknowns, and “what changed” live in chat
scrollback, READMEs, and half-remembered notes. When the session ends, the
memory dies.

**Primary promise:** Never explain your project to an AI twice.  
**Secondary promise:** Never lose what you learned, decided, or built.

---

## 2. Target user

**AI-assisted / vibe coder** — a human who ships with coding agents daily and
needs a persistent project brain that:

- answers “what is this / what changed / what matters / what is unknown”
- hands portable context to the next agent without tribal ritual
- keeps humans in Obsidian/Web without making Atlas an Obsidian clone

---

## 3. Product definition

**PROJECT ATLAS = THE PERSISTENT BRAIN FOR AI-NATIVE PROJECTS.**

Three pillars:

| Pillar | Role |
|---|---|
| **ATLAS KNOWLEDGE** | Human-facing persistent project memory via Obsidian + Web |
| **ATLAS CONTEXT** | Portable, current project context for Cursor / Claude / Codex / ChatGPT / other agents |
| **ATLAS TRUTH** | Evidence, provenance, authority, time, conflicts, and UNKNOWN under both |

Division of labor:

- **Atlas owns** intelligence + truth + structure.
- **Obsidian owns** human editing / browsing / PKM interface.
- **Web** is a read lens over Truth Core (UI != canonical).
- **Agents** consume Context packs / MCP / handoffs; they never become authority.

---

## 4. Human journey (Coder Alpha)

```text
Fresh project
  -> atlas connect .
  -> Atlas understands project (Truth Core compile)
  -> Human opens Atlas Knowledge / Obsidian
  -> What is this project?
  -> What changed?
  -> What decisions matter?
  -> What is unknown / conflicting?
  -> What should I do next?
```

## 5. Agent journey (Coder Alpha)

```text
Cursor (or other agent) receives Atlas Context
  -> coding session
  -> automatic / default meaningful session capture
  -> atlas handoff create
  -> different agent resumes
  -> human knowledge updates (protected regions / review promote)
  -> same Truth Core underneath
```

---

## 6. Surface roles

| Surface | Role | Non-role |
|---|---|---|
| **Obsidian** | Browse/edit vault Markdown; protected human regions | Not an Atlas clone; not authority |
| **Web** | Knowledge / Time Machine / conflicts read UX | Not canonical truth; not inventing claims |
| **CLI** | `connect`, overview/state/changed lenses, ask2, kdiff, handoff, context export | Not a chat product |
| **MCP / bridges** | Read-only agent access to vault projections | Not write authority; DEMO_FIXTURE bridges != production |
| **Control plane** | Governed session/receipt lifecycle for this repo | Sibling deliverable; not Core Truth |

---

## 7. Dogfood contract

**Canonical estate:** the Project Atlas repository itself.

**Success criterion:** Owner starts a fresh Cursor/Claude/Codex/ChatGPT session
without manually re-explaining Project Atlas.

**Primary acceptance:** “I would notice immediately if Atlas disappeared from
my coding workflow.”

### Metrics (define now; instrument over time)

| Metric | Intent |
|---|---|
| `TIME_TO_USEFUL_CONTEXT` | Time from `atlas connect` / resume to useful overview/context |
| `REEXPLANATION_RATE` | Sessions where owner re-pastes project explanation |
| `HANDOFF_SUCCESS_RATE` | Handoff resumes with correct identity + open decisions |
| `CONTEXT_ACCURACY` | Context claims match Truth Core |
| `STALE_CONTEXT_RATE` | Packs citing superseded claims / missing recent work |
| `UNKNOWN_HONESTY` | Unknown/conflict surfaced instead of invented answers |
| `MEANINGFUL_CHANGES_CAPTURED` | Sessions with real capture receipts |
| `USER_CORRECTIONS_REQUIRED` | Owner edits needed after a session |
| `MISTAKES_PREVENTED` | Cases where Atlas blocked stale/wrong guidance |

---

## 8. Explicit non-goals

- Obsidian clone / PKM feature race
- Automatically waking **Atlas-OPT**, AutoLab, Prime, or RL
- Inventing authentic pilot estates / closing **INT-013** without owner roots
- Treating DEMO_FIXTURE E2E as AUTHENTIC_PILOT or RELEASE
- Merging historical owner-held tip branches by narrative alone
- Letting model output, UI, or graph become authority
- Large documentation rewrites that block product slices

---

## 9. Relationship to existing Atlas architecture

Coder Alpha **productizes** what already exists; it does not rebuild:

- Truth Core (`discover` → `ingest` → indexes → validate)
- OKF / Obsidian projection
- Provenance, claims, conflicts, UNKNOWN
- Bitemporal knowledge + KDiff / Time Machine
- Ask Atlas 2 + Context Compiler
- MCP, ChatGPT bridge (honesty: demo paths remain DEMO_FIXTURE where stamped)
- Web read lenses
- Agent-event / receipt infrastructure (`atlas-vault-documentation/`)

Shipped Coder Alpha entry packages (runtime truth on `main`):

- `AS-CODER-ALPHA-CONNECT-001` — `atlas connect`
- `AS-CODER-ALPHA-OVERVIEW-001` — Project Overview derived lens

Execution backlog: `docs/CODER-ALPHA-035-REBASE.md` + `docs/backlog.md`
(Coder Alpha section).

---

## 10. Relationship to historical packages (reconcile, do not erase)

Classification: `KEEP` | `REFRAME` | `SUPERSEDE` | `DEFER` | `EXTERNAL_BLOCKED`

| Item | Class | Note |
|---|---|---|
| **AS-MVP-001** | **REFRAME** | Portfolio/stale/conflict telemetry = Knowledge/Truth health signals; receipt paperwork is not the north star |
| **INT-013** | **EXTERNAL_BLOCKED** | Needs owner authentic roots; agents must not invent pilots |
| **AS-GH-002** | **DEFER** | Repo hygiene; zero leverage on “never explain twice” |
| **AS-OPT-GATE-001** | **KEEP** | Safety boundary; wake gate remains CLOSED |
| **Atlas-OPT / AutoLab / RL / Prime** | **DEFER** | Orthogonal; not overnight Coder Alpha |
| **Authentic pilot** | **EXTERNAL_BLOCKED** | Owner-provisioned only |
| Historical tip-branch “governor required” queues | **SUPERSEDE** | Prefer git ancestry + this north star over stale prose |
| `docs/master-roadmap.md` / `docs/implementation-roadmap.md` | **INPUT** | Level-4 planning; reconcile into Coder Alpha priority, do not execute blindly |

Full product-impact table: `docs/CODER-ALPHA-035-REBASE.md` Phase 1.

---

## 11. Documentation precedence (D-037)

1. Current explicit Project Owner directives (D-035, D-036, D-037, …)
2. Current repository/runtime truth (`origin/main`, tests/CI, README, AGENTS, CLAUDE, WORKLOG)
3. Stable product/truth invariants (`docs/prp.md`, `docs/plan.md`, acceptance, ADRs, security)
4. Historical execution planning (master/implementation roadmaps, backlog, old branches) — **input only**

The global operating foundation (`docs/global/`) is a stable invariant at level 3. It governs *how* work is done (operating contract, style mission, Global Goals) and does not change product direction.

---

## 12. Honesty stamps

- `DEMO_FIXTURE != AUTHENTIC_PILOT`
- `DEMO != RELEASE`
- `UI != CANONICAL TRUTH`
- `MODEL_OUTPUT != AUTHORITY`
- `UNKNOWN != HEALTHY` (honesty, not a green status)
- `ATLAS_OPT_WAKE_GATE = CLOSED`
- `CODEX_VALIDATED = NO`
- `EXTERNAL_SECURITY_REVALIDATION_REQUIRED = YES`
