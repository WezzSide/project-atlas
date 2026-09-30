# 02 — Job-Oriented Information Architecture

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Replaces (as navigation):** the flat 15-item `PROD_LINKS` list (audit finding A-1)
**Does not replace:** any route, endpoint or command. This is an IA, not a deletion plan.

> `UI != CANONICAL`. An IA reorganises *access to* evidence. It confers no authority on
> any surface, and no job below is permitted to imply that reading a screen settles a
> question that Atlas records as unknown, contested, or owner-gated.

---

## 1. The rule this IA follows

Every job maps through five columns, in this order:

```
USER_JOB  →  REQUIRED_EVIDENCE  →  ATLAS_CAPABILITY  →  UI_SURFACE  →  AUTHORITY_BOUNDARY
```

The chain must be complete. If a job has no `ATLAS_CAPABILITY`, it is **not** in this IA —
it is a backlog item or a product idea, and it is marked as such. This is the discipline
that keeps the IA from becoming invented marketing: **every job below is backed by a
command or endpoint that exists in `origin/main` @ `7cb927f7`.**

## 2. Jobs, derived from repository capability

### J-1 — "What does Atlas actually know about this project?"

| Column | Value |
|---|---|
| **Required evidence** | compiled project record, claim set, source citations |
| **Atlas capability** | `atlas overview`, `atlas brief`, `atlas state`; `/v1/overview-status`, `/v1/brief`, `/v1/project-state`, `/v1/knowledge` |
| **UI surface** | Project → **Knows** |
| **Authority boundary** | Truth Core is canonical; the page is a projection. Every claim shows its source or is marked `UNKNOWN`. |

### J-2 — "What is uncertain, and where do sources disagree?"

| Column | Value |
|---|---|
| **Required evidence** | unknown set, conflict set, authority precedence, competing sources |
| **Atlas capability** | `atlas unknown`, `atlas ask2` (conflict path); `/v1/unknown-status`, `/v1/conflicts`, `/v1/intelligence/conflicts` |
| **UI surface** | Project → **Open questions** |
| **Authority boundary** | `UNKNOWN stays UNKNOWN`. The UI must never resolve a conflict by picking a winner for display; conflict resolution is `atlas review decide`, a human act. |

This is Atlas's flagship job (R-4 differentiator) and today it is spread across `Knowledge`,
`Intelligence` and `Graph` with no single entry point.

### J-3 — "What changed, and what did it change from?"

| Column | Value |
|---|---|
| **Required evidence** | document-declared valid-time, bitemporal catalog, T1→T2 diff |
| **Atlas capability** | `atlas changed`, `atlas kdiff --as-of / --from --to`; `/v1/changed-status`, `/v1/kdiff`, `/v1/bitemporal-status` |
| **UI surface** | Project → **Change** |
| **Authority boundary** | Diffs are derived from declared valid-time, not wall-clock. `GRAPH != AUTHORITY`. |

### J-4 — "Ask a governed question and get an honest answer."

| Column | Value |
|---|---|
| **Required evidence** | project-scoped hybrid retrieval, compiled read-only context, citations |
| **Atlas capability** | `atlas ask2`; `/v1/ask`, `/v1/intelligence/query`, `/v1/intelligence/explain`, `/v1/intelligence/evidence` |
| **UI surface** | **Ask** (global, project-scoped) |
| **Authority boundary** | `MODEL OUTPUT != AUTHORITY`. Three outcomes are equally valid renders: answered-with-citations, `UNKNOWN`, `CONTESTED`. An answer with no citation is a defect, not a result. |

### J-5 — "Why should I believe this particular claim?"

| Column | Value |
|---|---|
| **Required evidence** | source lineage, provenance hash, authority precedence, ingest path |
| **Atlas capability** | `atlas query`, lineage registry, provenance validation; `/v1/intelligence/evidence`, `/v1/intelligence/explain`, `/v1/graph` |
| **UI surface** | **Evidence drawer** — inline on any claim, on every surface |
| **Authority boundary** | Provenance, not popularity. `no subjective trust scores`. |

Per R-4 this is deliberately **not** a destination. It is a drawer reachable from any
rendered claim, which is why `Graph` leaves the top-level nav.

### J-6 — "Is the source material healthy, or am I reading stale input?"

| Column | Value |
|---|---|
| **Required evidence** | source inventory, hash drift, last-seen, index freshness |
| **Atlas capability** | `atlas source-health`, `atlas discover`, `atlas validate`, `atlas index-status`; `/v1/source-health`, `/v1/discovery`, `/v1/index-status` |
| **UI surface** | **Estate → Sources** |
| **Authority boundary** | `unknown != healthy`. Absent evidence renders `UNKNOWN`, never `OK`. |

### J-7 — "What is autonomous work doing right now?"

| Column | Value |
|---|---|
| **Required evidence** | session receipts, agent events, run state, action log |
| **Atlas capability** | `atlas ops health/events/report`, `atlas capture list`; `/v1/ops/receipts`, `/v1/actions`, `/v1/actions/recent`, `/v1/obs`, `/v1/mission` |
| **UI surface** | **Activity** |
| **Authority boundary** | `READ_ONLY_UI != EXECUTION_AUTHORITY`. Activity observes; it never starts, stops or approves. |

### J-8 — "What is blocked, and what is waiting specifically on me?"

| Column | Value |
|---|---|
| **Required evidence** | attention queue, review queue, owner-gate state, promote-eligibility |
| **Atlas capability** | `atlas attention`, `atlas next`, `atlas review decide`, `atlas revocation status`; `/v1/project-attention`, `/v1/next-status`, `/v1/authz` |
| **UI surface** | **Home → Needs you** |
| **Authority boundary** | The strictest on any surface. `PROMOTE_ELIGIBLE != MERGED/DEPLOYED/AUTHORITATIVE`, and the wake gate stays `CLOSED`. The UI *displays* that a decision is owner-only; it can never take it, and must not present an owner gate as a button that appears actionable. |

Per R-1 this is the highest-value under-served job in the product, and it is the reason
`Home` changes from a link hub into an answer.

### J-9 — "Prepare bounded context for an agent, and hand off."

| Column | Value |
|---|---|
| **Required evidence** | project context pack, handoff record, capture defaults |
| **Atlas capability** | `atlas context`, `atlas context-pack`, `atlas handoff create/resume`, `atlas capture record`; `/v1/workspace` |
| **UI surface** | **Project → Handoff** |
| **Authority boundary** | Export is read-only. A context pack is an input to an agent, never a grant of authority to one. |

### J-10 — "Is the installation itself sound?"

| Column | Value |
|---|---|
| **Required evidence** | environment diagnostics, vault identity, schema compatibility, snapshot state |
| **Atlas capability** | `atlas doctor --json`, `atlas schema compat`, `atlas compat verify`, `atlas snapshot`; `/v1/health`, `/v1/meta`, `/v1/snapshot` |
| **UI surface** | **Estate → System** |
| **Authority boundary** | Operational durability `!=` project authority. A green doctor says the tool works, not that the knowledge is true. |

### J-11 — "Bring a new project under Atlas."

| Column | Value |
|---|---|
| **Required evidence** | discovery manifest, project identity, ingest plan |
| **Atlas capability** | `atlas init`, `atlas connect`, `atlas discover`, `atlas ingest`, `atlas build-indexes`, `atlas build-portfolio` |
| **UI surface** | **CLI-primary.** Web shows state only. |
| **Authority boundary** | These are the pipeline's **write** operations. Per `UI != CANONICAL` and the read-only firewall they are deliberately *not* given a web trigger; the web surface reports what the pipeline produced. |

J-11 is included precisely to record a boundary: it is the one job where the honest answer
is "the web should not do this."

## 3. Resulting navigation

Five destinations, replacing fifteen. Everything else is reached by drill-down, by the
evidence drawer, or by the command palette (R-3).

```
Home          → answers J-8 first ("Needs you"), then J-7 summary
Projects      → project list → project detail, which holds:
                  Knows (J-1) · Open questions (J-2) · Change (J-3) · Handoff (J-9)
Ask           → J-4, project-scoped
Activity      → J-7
Estate        → Sources (J-6) · System (J-10)
```

Plus, on every surface:

- **Evidence drawer** (J-5) — inline from any claim; not a nav item.
- **Command palette** (`Ctrl/Cmd+K`) — the full 67-command / 41-endpoint surface, typed by
  authority. Breadth is preserved here rather than deleted.

### Mapping: no capability is orphaned

| Today's route | Becomes | Note |
|---|---|---|
| Home | Home | reframed: answers J-8 instead of listing links |
| Projects | Projects | kept |
| Knowledge | Project → Knows | J-1 |
| Intelligence | Project → Open questions | J-2; conflict-first framing |
| Time Machine | Project → Change | J-3 |
| Context | Project → Handoff | J-9 |
| Ask | Ask | kept, elevated |
| Discovery | Estate → Sources | J-6 |
| Source Health | Estate → Sources | J-6; merges with Discovery |
| Ops | Estate → System + Activity | J-10 / J-7 split by question |
| Roadmap | Project → Change (derived tab) | `ROADMAP != canonical` retained |
| Graph | **Evidence drawer** | J-5; leaves top-level nav per R-4 |
| Command Center | **Command palette** | R-3; a mode switcher becomes a palette |
| Mission Control | Activity | J-7 |
| Workspace | Project → Handoff | J-9 |

Two of the three unresolvable label clusters from A-1 dissolve because the *questions*
differ even where the modules overlap: `Ops` splits by whether the user is asking "is the
tool sound" (J-10) or "what is running" (J-7); `Discovery` and `Source Health` merge
because they answer one question.

### What this IA does not claim

- It does not claim the 15 routes are removed. Direction and spec work treats them as
  retained URLs; redirect/alias policy is `AX-001` in the backlog.
- It does not claim implementation. `02` is `SPECIFIED`. What is implemented in this lane
  is listed in `11-HANDOFF.md` and is deliberately narrower.
- It does not reduce Atlas's capability surface. That is the point of the palette.
