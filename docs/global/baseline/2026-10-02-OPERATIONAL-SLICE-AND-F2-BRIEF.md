# Operational Slice, F2 Decision Brief and E2 Runbook — 2026-10-02

| Field | Value |
|---|---|
| Observed at | `main` `9e22fdfc8ea45b582598ad3823bad868c212ea00` (#1043 merged); #1048 candidate `708158e2` (unmerged) |
| Status | `PLANNED` only. Nothing here is implemented, deployed or authorized |
| Related | [frontier](./2026-10-02-AUTONOMY-FRONTIER.md), [authority note](./2026-10-02-AUTHORITY-COMPATIBILITY.md) |

`A/` = `src/project_atlas/orchestration/autonomy/`.

## 1. Where the dev loop actually stands

| Dimension | State |
|---|---|
| Components merged | `Planner`, `FabricAdapter`, `Crosswalk`, `SpoolTransport`, `GitHubRestPort` |
| Wired together | Only in `tests/unit/test_orchestration_dev_fabric_adapter.py`. No CLI or entrypoint constructs them |
| Durable | Crosswalk (append-only, fsync'd JSONL, torn-tail heal) and the adapter's pending dir. **Not** the planner (in memory, `A/dev_planner.py:98-104`) |
| Grant-checked dispatch | No. `FabricAdapter.dispatch` (`A/dev_fabric_adapter.py:305-336`) has a base-pin and a write-ahead DISPATCH, but no grant hook |
| Run correlation | By time window only. The execute workflow has no `run-name` |
| Verifier on agent runs | It emits `REJECTED` because the executor's evidence upload is skipped when a run is red (`atlas-agent-execute.yml:651-658` has no `if:`) |
| Deployed / exercised live / relay-free | No / no / no |

## 2. Smallest vertical slice (no resident service)

```
owner-signed grant -> devloop-tick (one-shot, locked) -> grant-checked dispatch
  -> run-name correlation -> result -> verifier report -> Crosswalk verdict
  -> status.json: INTEGRATION_READY | BLOCKED | AWAITING_GRANT | OWNER_REQUIRED
```

| PR | Change | Reuses | Authority to merge |
|---|---|---|---|
| **A: dispatch grant + hook** | A `DG-<task>-<exec>` grant with fields `repository`, `task_id`, `execution_id`, `work_seal`, `base_revision`, `workflow`, `ref`, `workflow_inputs_sha256`, `policy_sha`, `max_dispatches: 1`, `expires_at`. A verifier in `autonomy/tools/` checks it against a **pinned owner signer list**. A required `authorize()` hook in `FabricAdapter.dispatch` sits between payload build and `bind_dispatch`. The grant ID is indexed uniquely in the Crosswalk (consume-once) | `preflight.py` ref refresh, HALT checks, `policy_sha`; `payload.sha256()` (= the package's `workflow_inputs_sha256`); the #1048 `instructions_sha256` | Owner merge, plus a **separate owner policy commit**: narrow supervisor scope from `autonomy/grants/**` to `G-*.md`, pin signers, and cap grant budgets at §4 |
| **B: `devloop-tick` driver** | `A/dev_driver.py` plus a subcommand in the standalone `A/cli.py`. It takes an `fcntl` lock, runs `recover()`, rebuilds the planner from spool chains, runs `adapter.tick()` and `planner.pump()`, and writes an atomic `status.json`. It routes terminal adapter errors to `planner.fail_execution`. It fixes **W4** (below) | Crosswalk, spool claim-once, deterministic seals, `_readopt_claimed`, `recover()` | Owner merge (floored modules) |
| **C: correlation + evidence** | `run-name: atlas-agent <correlation>` (the input is covered by `workflow_inputs_sha256`); `locate_run` matches it exactly; evidence and smoke steps get `if: ${{ !cancelled() }}` with a pinned workspace dir | `RunInfo`, the existing verify workflow | Owner merge (`.github/**`) |
| D (optional): salvage adoption | A separate predicate for a red run whose salvage artifact says `ENVELOPE_REJECTED_MODEL_SUCCESS` / `TURN_BUDGET_EXCEEDED`, with `persisted_branch` = bound branch and head = branch head. The result is ingested **as a candidate** that still needs V1 `VERIFIED` and green required CI. It never relaxes the `success` predicate for other runs | C | Owner merge |

**Resume after the session ends.** Any invoker the owner authorizes re-runs `devloop-tick`.
Every step is idempotent:
- seals are deterministic;
- re-publishing a seal is a no-op;
- Crosswalk refuses divergent second hops;
- `recover()` completes interrupted sequences.

**Duplicate avoidance.** Write-ahead DISPATCH, serialization of unbound dispatches,
grant-ID consume-once, unique run binding, re-run attempt guards, and the tick lock.

Crash windows that remain open today:

| Window | Today | Fixed by |
|---|---|---|
| **W4**: the API call times out or returns 5xx after GitHub accepted the dispatch | Terminal `RemoteExecutionFailed`; the work is dropped while the run may still execute as an orphan | B: treat as outcome-unknown and let `locate_run` decide |
| W6: a manual dispatch lands inside the correlation window | Possible mis-adoption or ambiguity | C (`run-name`) |
| W9: the planner dies after claiming RESULT/VERDICT | Lineage lost | B (rebuild from spool) |
| W10: two tick processes | Nothing enforces a single writer | B (lock) |

## 3. F2 decision brief: who may cause a dispatch

**Facts that constrain the choice (OBSERVED 2026-10-02).**
- **Credentials:** the agent host's `gh` token is the owner account `WezzSide` with admin rights.
- **Branch protection on `main`:** 0 required reviews; signatures not required.
- **Environments:** `atlas-vps02` has no protection rules.
- **Grant writers:** `autonomy/grants/**` is supervisor-writable from loop level 2.

Therefore "the grant is on `main`" does not prove owner issuance. Issuer proof must be an
owner signing key held off-host.

| Property | (a) Owner-allowlisted pinned local command | (b) GitHub-side dispatcher workflow behind an environment |
|---|---|---|
| Issuer identity | Signature checked against a pinned owner signer list (to build); key off-host | Same signer check in the job **plus** environment required reviewer = owner (to configure) |
| Repository / task / base / inputs binding | PR-A hook: `payload.sha256()`, seal, repository, base-pin | The job recomputes the digest from the committed package (PR-A logic ported) |
| Expiry | `expires_at` vs local clock | `expires_at` vs runner clock; plus the approval wait |
| Revocation | HALT on refreshed `origin/main`; deleting the grant file | Same, plus rejecting the environment approval |
| One-time consumption / replay | Crosswalk grant-ID index; after local state loss, only an off-host `run-name` check closes replay (needs C) | Native: run history and deployments keyed by `run-name` |
| Execution surface | Fresh detached worktree of `origin/main` with a verified tree hash, never the working copy; launcher under `autonomy/tools/**` | Workflow code on `main` only |
| Who can mint | Owner only, after narrowing the supervisor scope | Same, plus environment reviewers |
| Platform / classifier denial | The harness allowlists exactly one command shape. If denied, record `BLOCKED_PLATFORM_DENIAL` and stop; there is no fallback (no `gh workflow run`, no curl). A passing signature or preflight never overrides the denial | The agent never dispatches, so no classifier conflict. The agent must not be able to approve the environment, which requires D5 |
| Exists today | Write-ahead, consume-once seams, HALT/ref refresh, `%G?` stub | Nothing of the dispatcher; the environment exists without rules |

A supervisor can never mint its own dispatch authority under either option. That property
depends on the owner policy commit in PR-A and on D5. **Decision required: D2 plus D5**
(authority note). This brief does not choose.

## 4. DEVQ-0002-E2 runbook (prepared ≠ authorized)

1. **Prerequisites on canonical `main`**: #1043 ✅ (`9e22fdfc`); #1048 with the
   `attempt_kind` fix ⏳. Re-read `main` and record HEAD and TREE.
2. **Build from that `main` revision** using `dev_package.load_spec` / `build_package` /
   `render_package` / `package_sha256`, with:
   - `execution_ordinal=2`, `attempt=2`, `max_attempts=3`, `attempt_kind=implementation` (not
     a repair: E1 left no result branch);
   - `base_revision` = that `main` HEAD;
   - scope: `dev_github_port.py` plus its test file;
   - acceptance commands in `pytest` / `ruff` / `mypy` forms only.

   Record HEAD/TREE, `work_seal`, `workflow_inputs_sha256`, `package_sha256`, the literal
   `task_prompt`, and the builder revision. Label it
   `E2_PACKAGE_PREPARED_AWAITING_VALID_EXECUTION_GRANT`.
3. **Independent verification of the package** (fresh session): determinism, scope, command
   contract against the executor allowlist, no E1 identity reuse, and that the prompt states
   the redirect task exactly.
4. **Dispatch only under a new single-dispatch owner grant** (performed by the owner unless
   D1/F2 decide otherwise). E2 consumes attempt 2/3. Exactly one further implementation-bearing
   attempt remains after it, unless the owner changes the ceiling.
5. **If the run is green**, use the normal path: result branch, V1 report, exact-head CI,
   then independent review.
6. **If the run is red because of the turn envelope**, follow the salvage limitation below.

### Salvage limitation (#1043)

#1043 **preserves** an envelope-rejected candidate on `atlas/agent-<run>-<attempt>`, with
salvage evidence (`execution_policy=TURN_BUDGET_EXCEEDED`, acceptance `NOT_RUN`, verification
`NOT_VERIFIED`, integration `NOT_AUTHORIZED`). It does **not**:
- make the workflow green;
- make the candidate admissible to `ingest_report`. Both `dev_fabric_adapter.py:401-402` and
  `dev_crosswalk.py:340-341` require `workflow_conclusion == "success"`;
- make an adapter tick adopt it. The tick treats a red run as terminal.

**If salvage happens:**
- keep the red workflow status as recorded;
- verify the exact saved candidate: branch head and tree, merge-base equals the sealed base,
  changed paths inside scope, base-fail/head-pass;
- run exact-head CI and an independent review;
- route adoption through the owner as a candidate PR, or through PR-D once it exists.

**Never** fabricate or rewrite `workflow_conclusion` to satisfy the ingestion contract.
