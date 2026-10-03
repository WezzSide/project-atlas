# ATLAS-DEVQ-0002-E2-R2: execution record and candidate evidence

**State:** `SALVAGED_REPAIR_CANDIDATE_VERIFIED — NOT MERGED` (owner adoption decision required). Positive and negative results are both recorded here.

## Dispatch (owner-performed, single use)
- The coordinating session's own dispatch call was refused by its permission classifier and was not retried; the owner dispatched.
- **Run 37119599227** (run number 12), job 111192985287, `workflow_dispatch` on `main` @ `4b04621f8a7b081f9ad9efd356339d983eb1c6ce`, created 2026-10-03T11:25:19Z, run attempt 1, actor `WezzSide`, runner `atlas-ex-2a1b0af1992e4138`.
- Bound uniquely: the workflow had 11 runs before the dispatch (latest 37023482722) and 12 after; this is the only new run.
- Package `b449999a1801456c9e9bcaa49c40410e85965e316b852ee569e8fd56338a6835`, work seal `04da4ab26b13c2fdda5e0fbeb54ca74987dc4a6114190276115e505720e76b92`. Identity checks passed immediately before the refused call (branch `aee2026b…`, package and prompt hashes). The run's inputs are not readable through the API; the prompt identity is corroborated by the candidate following both RESOLVE entries.

## Execution (OBSERVED)
| Step | Conclusion |
|---|---|
| Assert checked-out HEAD is the sealed base_revision (first live run of this step) | success |
| Create dedicated agent branch | success |
| Run agent | **failure** |
| Commit and push dedicated branch | skipped |
| Persist envelope-rejected result (candidate only) | success |

- **Model result:** `subtype=success`, `is_error=false`, `terminal_reason=completed`, `stop_reason=end_turn`, 0 errors, **24 turns > `--max-turns 20`**, 8 permission denials, model `claude-opus-5-5`.
- **Workflow:** red, and it stays red. No re-run.
- **Artifacts:** salvage 11272747557 (sha256 `bb50b7409abeec136d6dc69e8e74a022b44799a75d147d3a623dad7342597ccf`): `ENVELOPE_REJECTED_MODEL_SUCCESS` / `TURN_BUDGET_EXCEEDED`, acceptance `NOT_RUN`, verification `NOT_VERIFIED`, integration `NOT_AUTHORIZED`. SDK diagnostic 11273206803 (sha256 `23c15853fd9069b41c5b116cc8603779238543275fb8df8f31604e94cba9e327`).

## Attempt accounting
**`MODEL_EXECUTION_CONFIRMED` — attempt 3/3 is consumed.** Evidence: the SDK diagnostic records an init event, 24 turns and model usage. E1 = 1/3, E2 = 2/3, E2-R2 = 3/3. No bounded implementation attempt remains.

## Candidate identity
- Branch `atlas/agent-37119599227-1`, HEAD `1182d0e40b6e5206af36cde07ed09f88c14ddd48`, TREE `0fe5e441108c9eeaff90e39b7d4b01bbb6408bde`; matches the salvage artifact.
- One commit; its only parent is the repair base `aee2026b1d8bf7a1293a0f758bc43100b9bed720` (merge-base = repair base).
- Changed paths vs the repair base: `src/project_atlas/orchestration/autonomy/dev_github_port.py` (+16/−4, all inside `GitHubRestPort._request`) and `tests/unit/test_orchestration_dev_github_port_redirects.py` (additions only). Both are sealed allowed paths. `read_json_artifact`, `_NoRedirect`, `_SameOriginRedirect`, `_origin`, `_guarded` unchanged.

## Evidence by dimension (kept separate)
| Dimension | Result |
|---|---|
| Exact-head CI | Run 37120070769 on `1182d0e4` (draft evidence PR #1058): control-plane, ubuntu 3.12, ubuntu 3.13, windows 3.12 all **success** |
| Independent verification (fresh session, owner standard Option B) | **P0 0 / P1 0 / P2 7** |
| Reachability probe (verifier's own; real urllib; 343 scenarios; Python 3.13.16 and 3.12.3) | Location data reachable through the exception graph in 128 scenarios on `aee2026b`; on the candidate 9, all identical to base and outside the target cases (4 are `read_json_artifact`, out of scope; 2 a non-redirect 5xx with `Content-Location`/`Link`; 3 stubs). Token marker reachable in 0 candidate scenarios. Foreign-origin requests: 0 everywhere |
| Three named detach scenarios | Redirect loop limit, POST 307/308, same-origin redirect then 400/403/500/502: same `-> <status>` message, `__cause__` None, `__context__` None, graph clean, through `_request` and all public methods |
| P1-1 | Non-http Location (file:, ws:, gopher:, mailto:, javascript:) and bracketed/unbalanced hosts: detached `AdapterError`, graph clean |
| Finding tests fail on base / pass on candidate | Candidate's own: 9 fail on `aee2026b`, 28 pass on candidate. Verifier's own: 46 finding tests fail on `aee2026b`, all 67 pass on candidate |
| Preservation tests | Candidate's 3 and the verifier's 21 pass on both base and candidate |
| Four sealed acceptance commands (unchanged) | All exit 0 at `1182d0e4` (pytest 95 passed; `ruff check`; `ruff format --check`; `mypy`) |
| Preservation behaviour | Same-origin following, cross-origin refusal, 404 mapping (direct and after redirect), plain 4xx/5xx with cause kept, URLError, timeout, JSON and UTF-8 errors, 204/empty: identical to base. 154 scenarios identical; every difference is required or permitted |
| Verification note 1 | Non-http Location keeps the `-> <status>` form, detached: accepted |
| Verification note 2 | Applied: finding tests fail on base; preservation tests pass on both |

## P2 findings (non-blocking, not applied: changing candidate bytes would be a new repair)
1. The broad `except ValueError` also relabels non-URL errors as "rejected invalid URL" (a token containing a newline; a non-ASCII request path). It fails closed and removes a base token exposure in that case, but the label is wrong.
2. A non-redirect 4xx/5xx carrying URLs in `Content-Location`, `Link` or `Refresh` keeps its cause. Identical to base; outside the literal Location standard.
3. The header check is case-sensitive for a plain-dict stub; not reachable with real urllib.
4. The candidate's `ftp:` test parameter fails on base only through `__context__ is None`; base exposes no Location there. Whether that counts as "artificially constructed to fail on the base" is the owner's call.
5. One new preservation test leaves an `HTTPError` unclosed (a `ResourceWarning` only under `-W error`, which the repo does not set).
6. Raw `InvalidURL`, `RemoteDisconnected`, `IncompleteRead` and JSON errors still escape `_request` directly, as on base.
7. The redirect test file takes about 25 s.

Outside the standard, reported not counted: traceback frame locals hold the token and Location on both base and candidate.

## Reconciliation with current main (`4b04621f`) — prepared locally, NOT pushed
- Commit `9efa5e8587963b4d1fbb8205c00252de1982ba52` (tree `4df673aff46b78a15e63250356c93672ceb4413f`), parent `main` `4b04621f`, branch name `integration/atlas-devq-0002-e2-r2`.
- Mechanical application of `git diff 64195b71 1182d0e`: patch-id `bf405631cfded2c60092426c71342d4efea2c03b` on both sides (computed by the preparing session); the three sealed paths are blob-identical to `1182d0e4` (confirmed by the verifier). No semantic adaptation; not an implementation attempt.
- Verifier at `9efa5e85`: four acceptance commands exit 0; its 67 tests pass; 716 related tests pass.
- **Pushing this branch was refused by the coordinating session's permission classifier and was not retried.** It therefore has no GitHub CI. Exact-head CI exists for the candidate `1182d0e4`, not for `9efa5e85`.

## Why this is not merged
- The run is red; `ingest_report` requires `workflow_conclusion == "success"`, and no supported path adopts a salvaged candidate. Adoption is an explicit owner decision.
- Merge is not authorized. No second dispatch is authorized or needed.

## Owner adoption, reconciliation push and integration PR (2026-10-03)

This section supersedes the earlier statement that the reconciliation was "prepared locally, NOT pushed".

- OBSERVED: owner decision — candidate `1182d0e40b6e5206af36cde07ed09f88c14ddd48` adopted as SOURCE PATCH only; PR #1058 is evidence-only and must not be merged.
- OBSERVED: owner pushed `integration/atlas-devq-0002-e2-r2` — HEAD `8b975e2cbd8bc4120e1c0e26c323367f9971fc4c`, TREE `4df673aff46b78a15e63250356c93672ceb4413f`, single parent `4b04621f8a7b081f9ad9efd356339d983eb1c6ce` (main at that time).
- PROVEN (remote re-read): exactly two paths differ from parent — `M src/project_atlas/orchestration/autonomy/dev_github_port.py` (blob `1c5c61b4fefe0f8bc1c1c22a7381430d0381b50b`), `A tests/unit/test_orchestration_dev_github_port_redirects.py` (blob `17c1368e522faa84f5a856adbd595ed212aa2209`); unchanged `tests/unit/test_orchestration_dev_github_port.py` = `505726eaba5c27a37a4c12bb83f98776e18437af`. The tree equals the previously independently verified reconciliation tree, so the full scenario verification was not repeated.
- OBSERVED: draft integration PR #1059 (`integration/atlas-devq-0002-e2-r2` -> `main`), head `8b975e2c…`, the sole integration carrier.
- OBSERVED: exact-head CI run `37128248393` (workflow `ci`, event `pull_request`, attempt 1, head `8b975e2cbd8bc4120e1c0e26c323367f9971fc4c`), conclusion `success`:
  - `111217897001` control-plane — success
  - `111217897180` quality (ubuntu-latest, 3.12, full) — success
  - `111217897152` quality (ubuntu-latest, 3.13, compat) — success
  - `111217897130` quality (windows-latest, 3.12, windows) — success
- Run `37120070769` proves candidate head `1182d0e4` only and is not counted as reconciliation CI.
- Result: `RECONCILIATION_EXACT_HEAD_CI_PASS`. MERGE NOT AUTHORIZED; no merge, auto-merge, dispatch or job re-run was performed.

## Final integration outcome (2026-10-03) — DEVQ-0002 CLOSED

| Item | Value |
|---|---|
| Source task | `ATLAS-DEVQ-0002` |
| Repair execution | `ATLAS-DEVQ-0002-E2-R2` |
| Source executor run | `37119599227` (workflow conclusion: failure — red) |
| Attempt accounting | 3/3 consumed; no executor attempts remain |
| Salvaged source candidate | `1182d0e40b6e5206af36cde07ed09f88c14ddd48` |
| Reconciliation head | `8b975e2cbd8bc4120e1c0e26c323367f9971fc4c` |
| Reconciliation tree | `4df673aff46b78a15e63250356c93672ceb4413f` |
| Integration PR | #1059 (merged by owner) |
| Merge commit | `341f94ca95ea35f1ce4eb3154155d388bd9273dc` (parents `4b04621f…`, `8b975e2c…`) |
| Merged main tree | `4df673aff46b78a15e63250356c93672ceb4413f` |
| Candidate exact-head CI | `37120070769` PASS (proves `1182d0e4` only) |
| Reconciliation exact-head CI | `37128248393` PASS |
| Post-merge main CI | `37136880047` PASS (event `push`, head `341f94ca…`, attempt 1) |
| P1-1 | RESOLVED |
| P1-2 | RESOLVED |

Post-merge CI jobs (run `37136880047`): `111243104836` control-plane — success; `111243104839` quality (ubuntu-latest, 3.12, full) — success; `111243104732` quality (ubuntu-latest, 3.13, compat) — success; `111243104769` quality (windows-latest, 3.12, windows) — success. No job was re-run.

**The red executor envelope did not itself merge.** Executor run `37119599227` remains red (model completion used 24 turns against the 20-turn envelope) and is not relabelled. Its salvaged bytes reached `main` only after: independent Option B verification (P0 0 / P1 0), reconciliation onto then-current main (`4b04621f…`), exact-head CI on the reconciliation head, and an owner-authorized merge through #1059.

Residuals (retained, NOT repaired under this lineage):

- Seven P2 notes: broad `except ValueError` mislabels a bad token / non-ASCII path as "rejected invalid URL"; non-redirect errors carrying Content-Location/Link keep their cause (as on base); plain-dict header stub is case-sensitive; one test leaves an `HTTPError` unclosed; raw `InvalidURL`/JSON errors still escape `_request` (as on base); the redirects test file is slow; `read_json_artifact` exposure is outside the sealed scope.
- ftp test observation: the ftp parameter fails on base only via `__context__ is None` — retained as a non-blocking test-semantics note.

Evidence PRs #1058 (candidate `1182d0e4`) and #1050 (attempt-2 candidate `aee2026b`) are superseded and closed unmerged; their branches are preserved.
