# Autonomy Frontier and DEVQ-0002 Record — 2026-10-02

| Field | Value |
|---|---|
| Token | `NEXT_AUTONOMY_FRONTIER_PREPARED` |
| Observed at | `main` HEAD `7373092598d2c0ee94b9b976736b735f375b926e`, TREE `90b1b95a093c4267b1a2cfcf11403c2b5ad2e4bb` |
| Companion | [2026-10-02-G1-G15-BASELINE.md](./2026-10-02-G1-G15-BASELINE.md) |
| Status | All items are `PLANNED`. Nothing here is implemented or authorized by this document |

## 1. Ranked frontier

Items are ranked by how much owner or operator relay they remove while preserving trust.

| Rank | ID | Removes | Prerequisites | Size | Authority needed |
|---|---|---|---|---|---|
| 1 | **F1 `GENERIC_DEVQ_INGRESS_AND_PACKAGE_BUILDER`** | Hand-authoring a code module and package per task. Today DEVQ-0002 cannot be packaged at all without new code (`dev_first_run.task_statement()` accepts only `ATLAS-DEVQ-0001`) | none | M–L (3–4 PRs) | Owner-committed DEVQ policy file (section 2) |
| 2 | **F2 Sanctioned dispatch grant** (resolves `AUTHORIZED_AGENT_DISPATCH_BLOCKED_BY_AUTO_MODE_CLASSIFIER`) | Owner hands-on `workflow_dispatch` per execution, plus manual PowerShell relay | F1 (stable `workflow_inputs_sha256`) | M | Owner: grant policy and signing key; per-grant commit |
| 3 | **F3 Merge #1043 + add `run-name`** | Manual byte rescue (VPS2 capture); ambiguous run ↔ inputs correlation | IV done (P0=0 P1=0) | S | Owner merge |
| 4 | **F4 Resident dev-loop driver with a durable planner ledger** | Human pump between dispatch → collect → verify → repair | F2, F3 | M | Grant policy |
| 5 | **F5 Verifier that produces verdicts on agent runs** | Owner reading CI to infer acceptance; merges without IV artifacts | none | S–M | Workflow change → owner merge |
| 6 | **F6 Bounded re-execution for infrastructure failures** | Owner deciding E(n+1) after a lost or envelope-rejected run | F3 | S–M | Inside the existing `max_attempts` ceiling |
| 7 | **F7 Durable lineage knowledge** | Reconstructing state from PR bodies | none | S | none |
| 8 | **F8 Signed publisher identity + fencing for the spool** | Single-writer and same-host assumptions (fleet prerequisite) | none | M | Owner key ceremony |

## 2. F1 design — `GENERIC_DEVQ_INGRESS_AND_PACKAGE_BUILDER`

```
GitHub issue / governed backlog
   -> normalized QueueItem
   -> deterministic admissibility + ranking
   -> generic WorkItem
   -> deterministic package + seal
```

It builds on modules that exist on `main` (under `src/project_atlas/orchestration/`):

1. **Source snapshot (read-only).**
   - Add a read-only `list_issues` / `get_issue` to the `GitHubPort` protocol
     (`autonomy/dev_fabric_adapter.py`) and `GitHubRestPort` (`autonomy/dev_github_port.py`).
   - Add `GITHUB_ISSUE` to `OriginationSourceFormat` (`origination/sources.py`).
   - Each issue becomes a canonical snapshot (number, title, body sha256, labels, state,
     `updated_at`) with its own digest. The digest and the API ETag go into provenance.
2. **Normalization.** Reuse the origination pipeline (`origination/adapter.py`,
   `origination/acceptance_contracts.py`).
   - An issue body is *intent only*. An item becomes execution-ready only with an explicit
     acceptance-contract sidecar that declares allowed and forbidden paths and acceptance
     commands. Scope is never inferred by a model.
3. **QueueItem mapping: value judgments are explicit policy, not machine facts.**
   - `QueueItem.severity` and `roadmap_value` currently default to `0`
     (`autonomy/dev_queue.py`). That makes "not judged" indistinguishable from "lowest".
   - F1 takes them, and `category`, from an owner-committed policy file (proposed:
     `autonomy/devq-policy.yaml`), pinned by sha256 the same way `policy_sha` is pinned in
     `autonomy/loop.yaml`. Values are keyed by issue number or label.
   - **No policy entry means not admissible** (fail closed). Each value records
     `source: owner-policy@<sha>`.
   - `requires_owner` is derived from observable risk attributes, never from free text.
   - Then the existing deterministic `dev_queue.validate` / `rank` / `select_next` run.
4. **Generic WorkItem.** Replace the DEVQ-0001-specific `dev_first_run.py` constants with
   `dev_package.build_work(item, contract, base_revision, execution_ordinal)`, which calls
   `dev_contracts.make_work`.
   - `base_revision` is the observed `origin/main` tip, reconciled at build time (OC-E).
   - `authority_ref` is the F2 grant ID.
   - Forbidden paths always include a hard floor: `.github/`, `autonomy/**`,
     `infra/atlas-runner/controller/**`, trust roots.
   - Task statements come from a registry keyed by work ID.
5. **Deterministic package and seal.**
   - Keep `build_dispatch_payload` → `workflow_inputs_sha256`, plus `work_seal` and
     `render_package` (sorted JSON).
   - Add a `provenance` block: source snapshot digest, policy sha, builder version and base
     revision.
   - Golden test: identical inputs must give a byte-identical package (idiom of
     `tests/unit/test_orchestration_dev_first_run.py`).

**First PR (bounded):** steps 4–5 only, the generic builder fed by an explicit WorkItem
spec file. That is enough to build DEVQ-0002-E2 without a bespoke module. Ingress (steps
1–3) follows.

## 3. F2 design — sanctioned dispatch grant (no classifier bypass)

`AUTHORIZED_AGENT_DISPATCH_BLOCKED_BY_AUTO_MODE_CLASSIFIER` is a **platform finding**. An
agent harness's auto-mode classifier correctly refuses `workflow_dispatch`, because nothing
it can verify says the owner authorized *this exact* dispatch. The owner therefore relays
the dispatch manually (PowerShell). The fix is to make the grant verifiable, not to weaken
the classifier.

- **Grant artifact.** `autonomy/grants/DG-<task>-<exec>.md`, written by the owner only.
  `autonomy/grants/**` is already outside executor scope. DG grants must also be excluded
  from supervisor-issued grants at every loop level: `autonomy/policy.md` role scopes let the
  supervisor write `autonomy/grants/**` from level 2, so this needs an owner policy edit. Fields:
  - grant ID, `policy_sha`
  - `workflow`, `ref`
  - `work_seal`, `workflow_inputs_sha256`, `base_revision`
  - `max_dispatches: 1`, `expires_at`
- **Signature.** A signed owner commit. Turn on `require_signed_grants` (implemented in
  `autonomy/tools/preflight.py`, currently `false` in `autonomy/policy.md`).
- **Dispatch authority.** A narrow `dispatch_authority` step before
  `GitHubPort.dispatch_workflow`. It verifies:
  - the grant is on `origin/main` with a good signature;
  - the policy sha matches;
  - `sha256(payload) == workflow_inputs_sha256` and the seal matches;
  - the Crosswalk has no prior DISPATCH for that seal (consume-once).

  It then records the grant ID in the Crosswalk and in the run's `run-name`.
- **Classifier representation.** Choose one, owner decision:
  - (a) The owner allowlists exactly one command shape in the harness permission settings,
    e.g. `atlas dev-dispatch --grant DG-…`, whose only effect is a grant-verified dispatch.
    The allowlisted command must run only code from an owner-merged, pinned revision
    (e.g. under `autonomy/tools/**`, a floor for every role), with its tree hash checked
    against `origin/main` before it runs. A working-tree copy is never trusted, because the
    executor can write `src/**`.
  - (b) A GitHub-side dispatcher workflow triggered by the grant commit, behind an
    environment protection rule.

  In both cases the agent never holds a broader dispatch capability than one verified grant.

## 4. Fleet — evidence required before `ATLAS_VPS_FLEET_AUTONOMOUS`

The current design is one controller on one host (`docs/adr/ADR-033-atlas-runner-fabric-001.md`).
Every row below needs its own exact-identity evidence (run IDs, drill records) before the
claim may be made. Software-loop progress does not count toward it.

| Requirement | Today | Status | Evidence required |
|---|---|---|---|
| VPS capability inventory | Uniform runner label set | NOT_PROVEN | Versioned, per-node inventory (labels, CPU/memory, toolchain, network class) with publisher identity |
| VPS health model | Single-host heartbeat (`HEARTBEAT_STALE_SECONDS=120`) | PARTIAL (1 node) | Fleet health aggregation that keeps connectivity, liveness and health separate |
| Executor placement | GitHub label match + local admission | NOT_PROVEN | Capability- and health-aware selection with recorded placement decisions |
| Verifier placement | GitHub-hosted verifier | PARTIAL | Verifier placement recorded per verdict |
| Verifier/executor separation | Identity rules in contracts and the spool | PARTIAL | A VERIFIED agent run with verifier node ≠ executor node, recorded |
| Leases / claim ownership | Spool link claim (#1045); runner `acquire_lease` unused | PARTIAL | Cross-host claim on a real shared medium with fencing tokens and TTL |
| Queue reallocation | None | NOT_PROVEN | Tested reallocation |
| Worker failure detection | `reconcile.py` `worker_lost`, startup only | PARTIAL | Periodic reconcile plus node-loss detection |
| Requeue | Manual re-dispatch | NOT_PROVEN | Automatic, bounded, exactly-once requeue |
| Failover | None | NOT_PROVEN | Second controller or node, failover drill |
| Result durability after node loss | Only GitHub branches and artifacts are off-host | NOT_PROVEN | Off-host ledger plus a node-loss drill |
| Control-plane failure behaviour | Fixed retry; systemd restart; single-writer adapter | PARTIAL | Crash and partition drills for controller and adapter |
| Stale node eviction | Manual runner deregistration | NOT_PROVEN | Automatic eviction |
| Capacity / rate caps | Local caps only | PARTIAL | Fleet-wide job and spend caps |
| Multi-node canary | Single-host staged deploy and rollback | NOT_PROVEN | N-node canary run IDs |
| Recovery evidence | Single host (deploys 36313899913 → 36315660717; acceptance 36271297201) | PARTIAL (1 node) | Multi-node recovery runs |

Discrepancies noticed (not fixed here): the runner README says NOT_DEPLOYED while the deploy
runs show deployments (latest success 36832690603); `atlas-runner-smoke.yml` has never run.

## 5. DEVQ-0002 record

Before this record, DEVQ-0002 existed on GitHub only inside the #1043 branch.

| Field | Value | Label |
|---|---|---|
| Product contract | HARDEN-DEVLOOP-001: authenticated generic GitHub REST requests must not leak `Authorization` across redirects | OBSERVED (owner directive) |
| E1 | Implementation-bearing attempt 1; run **36905529693** (2026-10-01T18:15:53Z), `workflow_dispatch` by owner, head `9a565c71a80dff9d3a4ead5571dde1082e3f5d78` | OBSERVED |
| E1 outcome | Model result `success` / `is_error=false` / `terminal_reason=completed` / `stop_reason=end_turn` / `errors_count=0` / `num_turns=25` > `--max-turns 20`. The agent step failed and push was skipped. No `atlas/agent-36905529693-*` branch exists. **Result bytes lost** | OBSERVED |
| E1 evidence | Artifact 11184106268 `atlas-agent-sdk-diagnostic`, zip `sha256:bc5adc3b26b830596482309370d8550d131839b07b95405838016e83ab58eb1c`, inner `claude-sdk-diagnostic.json` sha256 `980969989717458cd7167d438665d7a617e1023a4939f75fa5290c00c8b91b69`. Expires on GitHub 2026-10-15; preserved byte-for-byte in [evidence/devq-0002-e1-claude-sdk-diagnostic.json](./evidence/devq-0002-e1-claude-sdk-diagnostic.json) | OBSERVED |
| E1 verifier | Run 36906255665 failed at evidence verification | OBSERVED |
| Product verdict | `UNOBSERVABLE` | OBSERVED |
| E1 package / seal / `workflow_inputs_sha256` | Not recorded in the repository or on GitHub | UNKNOWN |
| Bounded repairs used | 0 | OBSERVED (no repair dispatched) |
| E2 | `NOT_DISPATCHED`. Preparation is gated on the owner merging #1043 | PLANNED |
| E2 builder blocker | `dev_first_run.task_statement()` raises for lineage `ATLAS-DEVQ-0002`. E2 needs F1 step 4–5 or a bespoke module | OBSERVED |
| E2 contract constraints | Acceptance commands in allowlist form (`pytest …`, `ruff …`, `mypy …`, never `python -m …`); fresh sealed base, package, work seal, `workflow_inputs_sha256` and `package_sha256`; no E1 identity reuse; dispatch only under a new single-dispatch owner grant | PLANNED |
