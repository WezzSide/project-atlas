# ATLAS-DEVQ-0002-E2-R2: pre-dispatch feasibility gate under Option B (2026-10-03)

> **Version note.** This gate was run against **v3** (work seal `a5c75996…f205`, package `3143db2f…d758`). Item 2 below describes the v3 repair instruction and is the reason the owner authorized the v4 re-seal. For v4 see `REVIEW.md` ("v4 evidence"): the detach rule is now in the executor-visible contract and a blind trial from the v4 prompt alone produced a result with no reachable Location data.

Independent session, scratch only (detached worktrees at `aee2026b`, since removed). Nothing here is the real result; no scratch byte was committed. Python 3.13.16 and 3.12.3 on Linux; Windows not run.

## Gate result for the three owner-named cases: `FINAL_REPAIR_FEASIBILITY_PASS`

**Reachability demonstrated on the failed E2 candidate `aee2026b`** (real urllib, local servers):

| Case | Raised | Location reachable at |
|---|---|---|
| redirect loop limit | `AdapterError … -> 302` | `__cause__.headers`, `.url`, `.filename`, `.fp.url` |
| POST 307 (and 308) | `AdapterError … -> 307` | `__cause__.headers` |
| same-origin redirect then 500 (and 403) | `AdapterError … -> 500` | `__cause__.url`, `.filename`, `.fp.url` |
| non-http Location scheme | `AdapterError … -> 302` | `str`, `repr`, `.headers`, `.url`, rendered traceback |
| invalid / unbalanced bracketed host | raw `ValueError` | no attribute; not an `AdapterError` from `_request` |

The token was not reachable through any attribute of any chained exception.

**Regression tests encoding Option B** (appended to the existing redirects test file; walk the whole `__cause__`/`__context__` chain and its attributes): 8 fail on `aee2026b`, 86 pass.

**Minimal in-scope repair** (inside `GitHubRestPort._request` only, +14/−3 lines): an `HTTPError` with no Location header and `exc.url == request URL` keeps `from exc` exactly as on the base; any other `HTTPError` (a redirect was involved) is recorded, closed, and re-raised after the except block as a bare `AdapterError` with the same `-> <status>` message; a `ValueError` from `opener.open` becomes an `AdapterError` raised after the except block; JSON parsing is moved after the try. With it: 94 passed on the three acceptance test files; `ruff check`, `ruff format --check` and `mypy` clean; 404 mapping, messages, URLError and timeout causes, same-origin following, cross-origin refusal and the artifact path preserved. No existing test conflicts.

**Contract analysis for `_request`:** no conflict with any sealed statement sentence or acceptance entry when read under the owner's interpretation.

## Two things the gate does not settle

1. **`read_json_artifact` has the same kind of exposure and is outside the sealed repair scope.** On `aee2026b` an untrusted artifact redirect raises `AdapterError` whose `__cause__.headers` holds the Location, and a failed blob fetch can chain an `HTTPError` whose `.url` is the signed blob URL. The sealed statement says `read_json_artifact` keeps its existing redirect model unchanged and the RESOLVE entry says "change only `GitHubRestPort._request`". The owner's repair scope names "the authenticated generic GitHub API request path", which is `_request`. Read that way this is out of scope and the gate passes; if Option B is meant to cover every `AdapterError` from this port, the sealed instruction cannot satisfy it.

2. **Feasible is not the same as likely (v3 prompt).** The executor sees only the sealed task prompt. It will not see Option B. The RESOLVE entry scopes the repair to redirects urllib rejects "before the handler runs" and says to keep 4xx/5xx unchanged; none of the three same-origin cases fits that description. The feasibility reviewer's estimate is that a faithful result leaves at least one of the three cases reachable about 90–95% of the time (redirect-then-500 about 90%; loop limit and POST 307 are fixed only if the executor happens to discriminate on status code or Location header, about 40–50%). Under Option B such a result fails final verification with no bounded attempt left.

These are judgement estimates, not measurements.
