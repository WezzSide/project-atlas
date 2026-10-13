# AS-DEMO-2.1-001 — DEMO EVIDENCE

| Field | Value |
|---|---|
| Tip | `77f450f97c923e7c1e9f6d8e12600dabef38fae0` |
| Orphan root | `D:\project-atlas-orphans\atlas-2.1-productionization-001\` |
| Closeout folder | `demo-closeout-023\` |
| Remedi PR | [#251](https://github.com/WezzSide/project-atlas/pull/251) |

## Honesty

`DEMO_FIXTURE` · **NOT RELEASE CERTIFIED** · **NOT AUTHENTIC PILOT** · PILOT **DORMANT_BLOCKED**

## Pointers (local orphans)

| Artifact | Path |
|---|---|
| Full pytest | `demo-closeout-023/pytest-tip-full.log` |
| Ruff (post-#251) | `demo-closeout-023/ruff-after-fix.log` |
| Mypy | `demo-closeout-023/mypy-tip.log` |
| Web `tsc -b` / build / smoke | `web-tsc-b-rerun.log`, `web-build-retry.log`, `web-smoke-retry.log` |
| ADV pytest | `demo-closeout-023/adv-pytest.log` |
| ADV certify JSON | `demo-closeout-023/adv-certify.json` |
| Browser package tests | `demo-closeout-023/browser-e2e-tests.log` |
| API meta / ops / mcp tools | `api-meta.json`, `api-ops-receipts.json`, `api-mcp-tools.json` |
| API projects / knowledge (claim vault) | `api-projects-final.json`, `api-knowledge-final.json` |
| MCP projects / knowledge | `mcp-projects-final.json`, `mcp-knowledge-final.json` |
| Live Ask known / unknown / conflict | `live-ask-known.json`, `live-ask-unknown.json`, `live-ask-conflict.json` |
| Core conflict index | `conflicts-index-claim.json` |
| Temporal unresolved query | `ask-conflict-temporal-full.json` |
| Claim-vault state claims | `state-claims-project-a.json` |
| Prior tip disposition (superseded) | `AS-DEMO-2.1-001-TERMINAL-DISPOSITION.json` (tip `d7c4d79`) |
| Browser receipt (refreshed) | `AS-DEMO-2.1-001-BROWSER-E2E-MISSING.receipt.json` |

## Vault materialization (throwaway)

| Item | Value |
|---|---|
| Estate | `fixtures/demo/estate` |
| Vault | `.tmp/demo-vault-claim` (worktree-local; not committed) |
| Conflict id | `conflict-36d1c4f79dbd74d55ecc` |
| Demo answer lens files | `generated/answers/ans-postgres-conflict.json`, `ans-project-b-requires.json` (operator lens over conflict/claims; **≠ Layer B authority**) |

## In-repo package evidence

| Surface | Path |
|---|---|
| Charter | `docs/demo/AS-DEMO-2.1-001.md` |
| Isolated browser-E2E package | `docs/demo/browser-e2e/` |
| Hero estate (claim-extractable) | `fixtures/demo/estate/` |
| Narrative estate (harbor-*) | `tests/fixtures/demo/estate/` |

## Transport consistency (recorded)

```text
API projects  == MCP atlas.projects.list.read   → project-a, project-b, project-c
API knowledge == MCP atlas.knowledge.query.read → ans-postgres-conflict, ans-project-b-requires
```

## Browser charter path

```text
status = BROWSER_E2E_MISSING
path_a_chips_observed = false
isolated_package_on_tip = true (docs/demo/browser-e2e/)
release_certified = false
pilot_pass = false
```
