# AS-DEMO-2.1-001 — DEMO TEST REPORT

| Field | Value |
|---|---|
| Package | **AS-DEMO-2.1-001** |
| Tip (`origin/main`) | `77f450f97c923e7c1e9f6d8e12600dabef38fae0` |
| `docs/atlas-2.2` tree | `7c8dea813a1d3a6aa15db6e95f264d9ea6e3033c` |
| Recorded (local) | 2026-08-10 |
| Evidence orphan | `D:\project-atlas-orphans\atlas-2.1-productionization-001\demo-closeout-023\` |
| Worktree | `D:\atlas-worktrees\as-demo-2.1-001-closeout` |

## Honesty banner

```text
TECHNICAL DEMO — VERIFIED candidate (closeout-023)
DEMO_FIXTURE only — NOT AUTHENTIC PILOT
NOT RELEASE CERTIFIED — ATLAS_2_1_RELEASE_CERTIFIED = NO
PILOT = DORMANT_BLOCKED
```

## Verdict

| Flag | Value |
|---|---|
| **TECHNICAL_DEMO_VERIFIED** | **YES** |
| `ATLAS_2_1_RELEASE_CERTIFIED` | **NO** |
| Authentic estate PILOT | **DORMANT_BLOCKED** |
| Browser Path A chips observed | **NO** (charter path: `BROWSER_E2E_MISSING`) |

Remedi merge used for tip green: [#251](https://github.com/WezzSide/project-atlas/pull/251) (ruff closeout).

## Gate matrix (empirical)

| Gate | Result | Evidence / notes |
|---|---|---|
| CLEAN_CLONE_BOOTSTRAP | **PASS** | Tip worktree + `pip`/import + `atlas version`; disposable vault under `.tmp/` |
| DEMO_FIXTURE pipeline | **PASS** | `fixtures/demo/estate` → `.tmp/demo-vault-claim` (init/discover/ingest/build-indexes/validate exit 0); 3 projects, 16 sources |
| BACKEND_FULL_SUITE | **PASS** | `1919 passed, 1 skipped, 1 xfailed` (`pytest-tip-full.log`) |
| RUFF | **PASS** | `All checks passed!` after #251 (`ruff-after-fix.log`) |
| MYPY | **PASS** | `Success: no issues found in 155 source files` |
| WEB_TYPECHECK | **PASS** | `apps/web`: local `typescript` `tsc -b` exit 0 |
| WEB_BUILD | **PASS** | `vite build` ✓ (`web-build-retry.log`; Windows rollup/esbuild native deps resolved locally) |
| WEB_SMOKE | **PASS** | `npm run smoke` — LIVE/DEMO/FIXTURE modes visible |
| API_E2E | **PASS** | `GET /v1/meta`, `/v1/projects`, `/v1/knowledge`, `/v1/ops/receipts`, `/v1/mcp/tools`, `/v1/ask` on `127.0.0.1:8765` |
| MCP_E2E | **PASS** | Allow-listed reads: `atlas.projects.list.read`, `atlas.ops.health.read`, `atlas.knowledge.query.read`, `atlas.explain.receipt.read`; write `atlas.vault.write` → `mcp-tool-denied` |
| TRANSPORT_CONSISTENCY | **PASS** | API vs MCP project IDs and knowledge `answer_id`s identical on claim vault |
| HERO_SCENARIO | **PASS** | `fixtures/demo/estate` unresolved conflict `conflict-36d1c4f79dbd74d55ecc` — PostgreSQL 15 vs 16 on `doc:harbor-database`/`deployment`; temporal query `temporal_status=unresolved` |
| ASK_KNOWN | **PASS** | `GET /v1/ask?q=project-b` → project + knowledge match (`live-ask-known.json`) |
| ASK_UNKNOWN | **PASS** | `GET /v1/ask?q=xyzzy-unknown-term-999` → empty matches, no fabricated answer |
| ASK_CONFLICT | **PASS** | Core conflict index + `GET /v1/ask?q=harbor-database` → `ans-postgres-conflict` (null value / conflict lens); no invented winner |
| BROWSER_E2E | **BROWSER_E2E_MISSING** | Charter-valid: isolated package `docs/demo/browser-e2e/` + receipt; package tests `5 passed`; Path A chips **not** visually attested |
| ADV | **PASS** | pytest `-k adv/ADV/threat`: 246 passed; `atlas adv certify`: 7/7 pass, `release_certified=false` |
| CRITICAL findings | **0** | See `DEMO-FINDINGS.md` |
| HIGH findings | **0** | See `DEMO-FINDINGS.md` |

## Fixture note

Hero / Ask conflict evidence uses claim-extractable **`fixtures/demo/estate`** (project-a/b/c).  
Narrative twin prose under `tests/fixtures/demo/estate` (harbor-*) validates pipeline discovery but does **not** emit the PostgreSQL conflict without claim markers — see MEDIUM finding in `DEMO-FINDINGS.md`.

## Non-claims

- This report does **not** set `ATLAS_2_1_RELEASE_CERTIFIED=YES`.
- This report does **not** wake authentic PILOT.
- Demo success ≠ release certification.
