# ATLAS-DEVQ-0002-E2-R2: independent package reviews

Fresh sessions, separate from the preparer; read-only; nothing dispatched. Each verdict applies only to the commit and package hash named.

| Version | Commit | package_sha256 | Result |
|---|---|---|---|
| v1 | `8298beb9` | `ef1d8bd6accbb10e4d0d5afc3341e588fcfc948ba95a7443afe2d58371d7bc8a` | P0 0 / **P1 1** / P2 7. Integrity exact; the RESOLVE text prescribed `from None`, which leaves the Location reachable through `__context__`. **Superseded, never dispatched** |
| **v2 (current)** | **`8703cf77`** | **`ba388db38537ba1f9b3012131f7bdf14e1be91fd9c261e97835b50081b9ee095`** | **P0 0 / P1 0 / P2 8** |

## v2 evidence (reviewer's own recomputation, builder on `main` `ac08248`)
- E2 parent rebuilds to seal `ac1cc81f…bfff` and package `b8035f9b…62fe`, byte-identical.
- Result, verification-request and verdict records load and their seals and cross-bindings verify; `classify_finding` → `REPAIRABLE_WITHIN_AUTHORITY`; `materialize_repair` → `REPAIR`, equal to `repair_work_item.json` (seal `a5c75996…f205`).
- Lineage root, repository, authority ref, role, allowed/forbidden paths, expected outputs and ceiling equal the parent's; contract = parent's six entries + one `RESOLVE:` entry (483 chars); statement and four commands byte-identical to E2's; attempt 3/3.
- Package, `workflow_inputs_sha256` `fc87f8f8…370a`, task prompt and every MANIFEST digest reproduce; rendering deterministic.
- `refs/heads/atlas/agent-37023482722-1` resolves to `aee2026b…`; `verify_checkout_ref` passes for that sha only.
- **Literal-compliance experiment** (scratch, never committed): implementing the RESOLVE text as written, changing only `_request`, makes 20 new regression cases fail on `aee2026b` and pass on the fix, with no exception chain; all four acceptance commands pass; same-origin, cross-origin refusal, 404, 4xx/5xx, URLError, timeout, JSONDecodeError and artifact behaviour preserved.

## Residual risks (P2)
1. A broad `ValueError` catch can relabel unrelated failures (invalid UTF-8 body) as a refused redirect unless the JSON parse sits outside the guarded region.
2. The RESOLVE scope ("before the handler runs") does not cover loop-limit or same-origin POST 307/308 errors, whose chained `HTTPError` holds the Location in `.headers`/`.url` but not in str/repr/traceback. The verifier standard for attribute reachability should be fixed before the result arrives.
3. The unbalanced-bracket case has no visible leak on the base; a test fails on the base only if it asserts the error type or the absence of a chain.
4. The inherited statement is stale relative to the repair base (pinned by the parent's `instructions_sha256`).
5. Turn budget: about 10 turns minimum against a cap of 20; reviewer estimate 65–75% to finish inside it. Over the cap, the run is red and the candidate is salvage-only, with no attempt left.
6. The workflow on `main` does not assert the checked-out revision and the source branch is unprotected. PR #1057 adds that assertion; if it merges first this package is re-rendered (work seal unchanged, package hash changes) and re-checked.
7. The verdict record was authored by the preparing session from the independent verdict on PR #1050; seals give integrity, not authentication.
8. Commits `fdb177e7` and `40fcf512` carry v1 bytes under misleading messages. Bind review and dispatch to `8703cf77` / `ba388db3…`, never to the branch name.
