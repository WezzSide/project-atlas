## ATLAS-DEVQ-0002-E2: execution record and candidate evidence

**State:** `SALVAGED_CANDIDATE_EVIDENCED: NOT INTEGRATION_READY (owner adoption decision required)`

### Dispatch (owner single-dispatch grant, consumed)
- **Run 37023482722**, `workflow_dispatch` on `main` @ sealed base `64195b71`, created 2026-10-02T14:57:05Z, attempt 1. Bound uniquely: one run in the dispatch window.
- Package `b8035f9b…` (work seal `ac1cc81f…`, inputs `c1c91333…`). Dispatched through the API with the package's exact `workflow_inputs` (HTTP 204).
- **Infra delay:** queued for 1h28m. The VPS2 standing transport grant had expired at 09:15:33Z; the owner issued `grant-github-transport-e2-20261002` and restarted the controller once. The job started at 16:25:29Z on `atlas-ex-d42fa5a53a3f41e8`. This was infrastructure recovery, not a new attempt.

### Execution (OBSERVED)
- **Model result:** `success`, `is_error=false`, `completed`, `end_turn`, 0 errors, **21 turns > `--max-turns 20`**.
- **Workflow:** **red**, and it stays red.
- **#1043 salvage** (first live use): candidate `aee2026b1d8bf7a1293a0f758bc43100b9bed720`, TREE `40cb45361fceb77b715d3e5245c48613e4e3d013`, branch `atlas/agent-37023482722-1`. Labels: `ENVELOPE_REJECTED_MODEL_SUCCESS` / `TURN_BUDGET_EXCEEDED`, `NOT_VERIFIED`, `NOT_AUTHORIZED`.
- **Artifacts:** salvage 11237929982 (`b407c17d…`), SDK diagnostic 11238940632 (`c31ae599…`).

### Evidence, by dimension (kept separate)

| Dimension | Result |
|---|---|
| Identity / scope | Single commit on the sealed base; merge-base = base; 2 paths, both in `allowed_paths`; nothing forbidden |
| Acceptance (package commands, exact) | 4/4 pass (pytest 83, ruff check, ruff format --check, mypy) |
| Base-fail / head-pass | On the base, the 7 cross-origin and redaction tests fail (`DID NOT RAISE AdapterError`: the token leaked) and the 9 preservation tests pass. On the candidate, 16/16 pass |
| GitHub CI (V2) | Run 37034861425 on `aee2026b`: control-plane, ubuntu 3.12, ubuntu 3.13, windows 3.12 all **success** |
| Independent candidate review (V4) | Separate session: **P0 0 / P1 0 / P2 4**. 9 of 10 mutants caught. Full suite 6506 passed / 0 real failures. `mypy src` strict clean. Empirical: on the base the other origin received the Bearer token; on the candidate it received 0 requests |
| Automatic fabric verifier (V1) | Run 37034552994: **`REJECTED`** on precondition `evidence_fragment_present=FAIL`. The red run skipped the evidence upload, so no code was examined. This is not a code verdict and not `VERIFIED` |
| Crosswalk lineage (local replay only) | WORK, DISPATCH and RUN bound. **RESULT refused, fail-closed**: `ingest_report` requires `workflow_conclusion == "success"` |

### Why this is not `INTEGRATION_READY`
- The package's verification policy requires V1 `VERIFIED` plus the required checks. V1 cannot verify a salvaged red run with the current workflow.
- No supported code path adopts a salvaged candidate into the result lineage. That is planned as PR-D in the operational slice; it does not exist yet.
- Adoption therefore requires an explicit **owner decision**. Merging this PR remains the owner's boundary.

### P2 follow-ups (not applied: changing candidate bytes would be a repair)
1. No test pins the scheme as part of the origin (an https→http downgrade mutant survives; the code itself refuses the downgrade).
2. An explicit `:0` port is treated as the default port.
3. POST 307/308 now reports "refused cross-origin redirect" instead of `-> 307/308`.
4. Slow server teardown (about 1 s per test).

### Accounting
E2 = 1 physical dispatch, implementation attempt **2/3**, bounded repairs 0. **One** implementation-bearing attempt remains.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
