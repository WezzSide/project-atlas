# AS-DEMO-2.1-001 — DEMO FINDINGS

| Field | Value |
|---|---|
| Tip | `77f450f97c923e7c1e9f6d8e12600dabef38fae0` |
| Closeout | demo-closeout-023 |
| CRITICAL open | **0** |
| HIGH open | **0** |

## Honesty

Findings here are **demo / TECHNICAL PREVIEW** issues. They do **not** authorize `ATLAS_2_1_RELEASE_CERTIFIED=YES` or authentic PILOT PASS.

## Blocking rule

CRITICAL or HIGH open items **block** `TECHNICAL_DEMO_VERIFIED=YES`.

## Findings

### DEMO-FINDING-001 — MEDIUM — Live knowledge plane not auto-emitted by Core pipeline

| Field | Value |
|---|---|
| Severity | **MEDIUM** (non-blocking for demo label) |
| Surface | `generated/answers/` · `/v1/knowledge` · Ask live matcher |
| Observation | After `discover → ingest → build-indexes → validate`, `generated/answers/` is absent. AppService/MCP knowledge lists are empty until operator materializes answer JSON. Ask live matches only `answer_id`/`subject`/`field`/`path` (no title/summary in listing rows), so natural-language “PostgreSQL” queries miss even when answers exist. |
| Impact | Audience Ask prompts need either seeded demo answer lens files or subject-token queries (`harbor-database`, `project-b`). |
| Mitigation used | Throwaway vault seeded with conflict/dependency answer lens files derived from conflict index + claims (**null value**, conflict status; not an invented winner). |
| Partial remediations | Listing exposes `title`/`summary`/`value_text`; Ask live + MCP NL match use those fields when answer files exist. Pipeline still does **not** auto-emit `generated/answers/` (operator/demo seed remains required). |
| Release note | Does not unblock release; authentic pilot still required for RC. |

### DEMO-FINDING-002 — MEDIUM — Dual DEMO_FIXTURE estates diverge on conflict extraction

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Surface | `tests/fixtures/demo/estate` (harbor-*) vs `fixtures/demo/estate` (project-a/b/c) |
| Observation | Harbor prose estate validates inventory/pipeline but yields empty `conflicts.json`. Claim-extractable `fixtures/demo/estate` yields unresolved PostgreSQL 15 vs 16 conflict. |
| Mitigation used | Hero / ASK_CONFLICT closeout executed against `fixtures/demo/estate`. |
| Follow-up | Optional operator doc clarity only — **not** a 2.2 PREP package under owner stop-churn directive. |

### DEMO-FINDING-003 — LOW — Windows web native optional deps

| Field | Value |
|---|---|
| Severity | **LOW** |
| Surface | `apps/web` build on win32 |
| Observation | Fresh `npm install` needed `@rollup/rollup-win32-x64-msvc` and esbuild script approval before `vite build` succeeded. |
| Mitigation | Local install retry; build then PASS. |

### DEMO-FINDING-004 — INFO — BROWSER_E2E_MISSING (charter path)

| Field | Value |
|---|---|
| Severity | **INFO** (charter-allowed) |
| Surface | Path A chip walkthrough |
| Observation | No in-repo Playwright/Cypress harness; Path A chips not visually attested this closeout. |
| Charter disposition | `BROWSER_E2E_MISSING` + isolated package `docs/demo/browser-e2e/` (**PASS** alternative). |

## Remediated this closeout

| Item | Disposition |
|---|---|
| Ruff F841 / E501 / I001 on tip | Fixed and merged [#251](https://github.com/WezzSide/project-atlas/pull/251) |

## Verdict linkage

With CRITICAL=0 and HIGH=0 after empirical gates in `DEMO-TEST-REPORT.md`:

```text
TECHNICAL_DEMO_VERIFIED = YES
ATLAS_2_1_RELEASE_CERTIFIED = NO
PILOT = DORMANT_BLOCKED
```
