# ATLAS-DEVQ-0002-E2-R2: independent package reviews

Fresh sessions, separate from the preparer; read-only; nothing dispatched. Each verdict applies only to the commit and package hash named.

| Version | Commit | package_sha256 | Result |
|---|---|---|---|
| v1 | `8298beb9` | `ef1d8bd6accbb10e4d0d5afc3341e588fcfc948ba95a7443afe2d58371d7bc8a` | P0 0 / **P1 1** / P2 7. Integrity exact; the RESOLVE text prescribed `from None`, which leaves the Location reachable through `__context__`. **Superseded, never dispatched** |
| v2 | `8703cf77` | `ba388db38537ba1f9b3012131f7bdf14e1be91fd9c261e97835b50081b9ee095` | P0 0 / P1 0 / P2 8. Superseded by v3, never dispatched |
| **v3 (current)** | `c400675b` | **`3143db2f7ed71ec641bd6599a51cef3d65d63040f4b68f851b7d7386cf2ed758`** | **Sealed bytes: P0 0 / P1 0.** Fresh independent review of the exact v3 bytes (see below). Two P1s were raised against the unsealed documents beside the package and are fixed in the following commit; P2 7 |

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
6. Closed for v3 when dispatched exactly as recorded: #1057 is on `main` and v3 carries `base_revision`. The source branch is still unprotected; a moved branch now fails the run before the agent starts instead of running on the wrong revision. The assertion step has never run on a real runner.
7. The verdict record was authored by the preparing session from the independent verdict on PR #1050; seals give integrity, not authentication.
8. Commits `fdb177e7` and `40fcf512` carry v1 bytes under misleading messages. Bind review and dispatch to the v3 package `3143db2f7ed71ec641bd6599a51cef3d65d63040f4b68f851b7d7386cf2ed758` (commit `c400675b` or later on this branch with the same package bytes), never to the branch name and never to v1/v2: their inputs carry no `base_revision`, which would silently skip the workflow assertion.

## v3 evidence (fresh reviewer; builder and workflow on `main` `495d5e8f`, tree `a8e1e094`)
- #1057 is merged; its merge commit is `main`.
- Materialized repair WorkItem seal `a5c75996bd6276cc3836f96daad6796dcdb3299400bc87b3186e390399f0f205` reproduced from the E2 spec through `classify_finding` and `materialize_repair`; equals `repair_work_item.json`, `build_work(load_spec(spec))`, and the package's `work_seal` and `expected_work_seal`.
- Package renders byte-identically on `main`; `package_sha256` `3143db2f…d758`, `workflow_inputs_sha256` `123f7ea1f5c2c727ac82d9b36345446f5fd5fe6cf30fafab56a6e1c375cd2d85`; 20 shuffled-key renders identical; every MANIFEST digest recomputes.
- v2 → v3: spec, task prompt and all five records byte-identical. Package JSON changes are exactly four, all from the builder: `workflow_inputs.base_revision` added; `workflow_inputs_sha256`; one sentence in `checkout.rule`; one abort condition inserted. No unexplained drift.
- Lineage, attempt 3/3, authority ref, allowed paths (the three sealed product paths), forbidden paths, expected outputs, ceiling, contract (parent's six entries + one RESOLVE) and repair base all hold.
- `refs/heads/atlas/agent-37023482722-1` resolves to `aee2026b…`; `verify_checkout_ref` passes for that sha only and refuses a package whose `base_revision` is removed, altered or emptied, even after re-hashing.
- Workflow assertion step, extracted from the YAML on `main` and executed: HEAD `aee2026b` with the sealed value exits 0; HEAD at `495d5e8f` or `64195b71` exits 4; malformed values exit 2 with nothing executed. The package's four input keys are exactly the four declared workflow inputs.
- `secrets` asserts nothing; `grant_required` is one workflow dispatch grant, not issued; latest execute run is still 37023482722.

## v3 findings
- **P1 (documents only, fixed):** (1) `VERIFICATION-STANDARD.md` carried a preparer-written "Consequence" paragraph that went beyond the owner's words and conflicted with the RESOLVE scope; it is now an explicit open owner decision. (2) Residual item 8 above pointed dispatch at v2; corrected to v3.
- **P2:** stale inherited statement ("new file", "from None"); turn budget (cap 20; E1 25, E2 21); assertion never run on a real runner and source branch unprotected; `FabricAdapter.dispatch` does not send `base_revision`, so only a dispatch of the recorded inputs gets the assertion; this storage branch predates #1057, so render only from `main`; verdict record authored by the preparing session; "Re-run jobs" on a failed run would start the model again without a new dispatch.

## Attempt accounting evidence
Owner rule: attempt 3 is consumed only when model execution starts. A run that fails at the assertion step has "Run agent" `skipped`, no `atlas/agent-<run>-<attempt>` branch and no SDK diagnostic artifact. A run where the model started has "Run agent" `success` (evidence: the step conclusion and the pushed agent branch; no diagnostic artifact is produced on success) or `failure` (evidence: the SDK diagnostic artifact with `num_turns` > 0 and non-empty `model_usage`). Grey case needing an owner ruling: the agent step failing before any model call (auth or action setup).
