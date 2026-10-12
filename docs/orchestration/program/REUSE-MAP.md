# Reuse map — AS-ORCH-PROGRAM-SUPERVISOR-001

What already exists on `main` at `b87b4a22`, what it actually does, where it
stops, and what this package had to add. Nothing below is a new DAG, a new
registry, or a new authority engine.

`UNMERGED != CANONICAL DEPENDENCY`: every row marked *(unmerged)* was read for
context only. This package imports nothing from an unmerged branch.

## Reused directly

| Existing component | Verified capability | Limitation | Change required |
| --- | --- | --- | --- |
| `orchestration.autonomy.models` (`WorkNode`, `AgentRecord`, `AgentLease`, `NodeState`, `OwnerGateKind`, `AgentCapability`, `MutationSurface`, `IvRequirements`, `RetryPolicy`) | Complete typed DAG-node vocabulary with authority fields pinned false at the model boundary | `WorkNode` carries no prompt, adapter, or acceptance-command payload — it describes governance, not execution | None. `ProgramTask.to_work_node()` projects onto it; execution payload stays in this package |
| `orchestration.autonomy.dag` (`ALLOWED_TRANSITIONS`, `assert_transition`, `apply_transition`) | Fail-closed lifecycle; refuses to transition to `MERGED` autonomously | `BLOCKED` has no edge back to `READY`, so it cannot model "waiting for an external precondition" | None. A task waiting on a precondition stays `DISCOVERED` (→ `READY` is legal) instead of being forced through `BLOCKED` |
| `orchestration.autonomy.continuation.select_next` | Picks one `READY` node honouring dependencies (`CERTIFIED`/`CLOSED`), owner gates, surface overlap, hard blockers, resource exhaustion | Returns a `package_id`, not a lease; no notion of program limits | None. The supervisor calls it and then applies program limits separately |
| `orchestration.autonomy.leases` (`grant_lease`, `release_lease`, `ScopeExpansionError`) | Capability match, `READY`-only leasing, refuses `authorized_paths` outside the mutation surface | In-memory value objects only | None |
| `orchestration.autonomy.lease_projection` (`project_grant`, `project_release`, `project_abandon`, `visible_active_lease`) | Durable, lock-guarded lease rows with stale-base rejection; survives process death | Store path is caller-supplied; rows are projection, not authority | None. Supervisor points it at the program state dir |
| `orchestration.autonomy.owner_gates.evaluate_owner_action` | Fails closed unless an external grant is passed in; never invents one | Advisory decision object only | None |
| `orchestration.autonomy.overlap.would_overlap` | Refuses parallel mutation of overlapping surfaces | — | None (single-worker slice keeps it trivially satisfied, but it is still enforced) |
| `orchestration.autonomy.evidence` (`hash_payload`, `make_bundle`, `write_bundle`) | Canonical, replay-stable digests and containment-checked bundle writes | — | None |
| `orchestration.sdk.external_observers` (`ExternalObserver`, `register_observer`, `due_observers`, `update_observer_status`, `park_observer_backoff`, `consume_terminal_event`, `nearest_wake_at`) | Durable external waits with exponential backoff and once-only terminal-event consumption | Paths are rooted at `<root>/.atlas/orchestration/sdk-runtime`; `ObserverType` is a closed enum | None. Supervisor passes its own program state dir as `root`, so program observers never share files with the Cursor SDK lane |
| `orchestration.sdk.host` (`acquire_supervisor_lock`, `release_supervisor_lock`, `assert_single_supervisor_or_raise`, `pid_is_alive`, `process_start_identity`, `request_supervisor_stop`, `stop_requested`, `clear_supervisor_stop`) | Singleton-supervisor protection keyed on (pid, instance id, process start identity); PID-reuse aware; stop-file cancellation | Known reclaim race — two contenders can each unlink a stale lock and both create one (the defect open PR #780 addresses, *unmerged*) | None to `host.py`. This package adds a read-back ownership re-verification after acquisition **and immediately before every consequential dispatch** (`supervisor._assert_still_supervisor`), which closes the window from the losing side |
| `orchestration.autonomy.models.StopReason` | Names why continuation halted | Has no "program complete" or "uncertain outcome" member | `ProgramStopReason` extends the vocabulary in this package rather than editing the shared enum |

## Read for context, deliberately not depended on

| Component | Why not a dependency |
| --- | --- |
| `orchestration.sdk.scheduler` / `nonblocking_scheduler` / `live_dag` / `mission_reconciler` / `resident_driver` | Genuinely reusable machinery, but bound to Atlas's own self-governance: `CANONICAL_PR = 429`, `CANONICAL_REPO = "B0LK13/project-atlas"`, `TRUSTED_MAIN`, seeded demo-release nodes, `AgentRole` values scoped to that mission. A general "approve a work program once" facility cannot inherit that pinning |
| `orchestration.sdk.backend` / `cli_execution_port` | Cursor-specific runtimes (`cursor-sdk`, `cursor-agent`). This package's first real adapter is Claude Code; the seam is `adapters.base.RuntimeAdapter` so a Cursor adapter can be added without touching the supervisor |
| `orchestration.sdk.idempotency.build_idempotency_key` | Rejects any repository other than `CANONICAL_REPOSITORY_IDENTITY`, and its key omits the adapter, so two different commands at one commit collide. This package derives its own key over (program, task, attempt, adapter identity, effective-profile digest, base pin) |
| `orchestration.autonomy.loop` / `governor` | The 001E loop is welded to `TrustedAnchorRecord`, the pilot node factory, and live GitHub inventory of this repository's own PRs. Its *policies* are reused (`select_next`, `dag`, `leases`); its bootstrap is not |
| PR #789 `orchestration/mission/` *(unmerged)* | Closest prior art: one mission run, one workspace, one adapter call, checkpoint + reconcile. It has no program, no DAG, no multi-task continuation and no supervisor. This package is the layer above it and shares no code with it |
| PR #780 `sdk/os_lock.py` *(unmerged)* | Would be the better lock primitive. Not merged, so not imported; the mitigation above is used instead |
| `src/atlas_dag/` | Does not exist on `main` — it lives only in the unmerged `feat/atlas-dag-*` lanes |

## Gaps this package closes

1. **A work program** — an objective the owner approves once, with a bounded
   task set, dependencies, acceptance conditions and execution limits. Nothing
   on `main` has this shape; `MissionObjective` in `mission_reconciler` is a
   hardcoded list of this repository's own release goals.
2. **Agent profiles** — a reusable execution contract (runtime, workspace
   scope, permitted operations, required evidence, limits) with narrowing-only
   task overrides. `AgentRecord` carries capabilities and nothing else.
3. **A Claude Code runtime adapter** — none exists; every backend on `main` is
   Cursor.
4. **A supervisor that continues across task completion** — `AutonomousLoop`
   performs at most one dispatch per tick and returns; `DurableAtlasSupervisor`
   is an asyncio host for the Cursor SDK lane. Neither takes a declarative
   program and runs it to program completion.
