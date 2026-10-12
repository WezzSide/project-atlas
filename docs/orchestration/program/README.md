# AS-ORCH-PROGRAM-SUPERVISOR-001 — continuous execution of an approved program

You approve a work program once. The supervisor executes its eligible tasks,
preserves progress across worker sessions ending and across its own restarts,
waits for external events without blocking unrelated work, and asks for input
only when a decision is genuinely yours.

`TASK_COMPLETE != PROGRAM_COMPLETE.` A worker finishing its session is a task
event. The program continues.

## Truth boundaries

```
PROGRAM_APPROVAL            != MERGE_AUTHORIZATION
WORKER_REPORTED_COMPLETION  != ACCEPTANCE
ACCEPTANCE                  != INDEPENDENT_VERIFICATION
TASK_COMPLETE               != PROGRAM_COMPLETE
ADAPTER_EXIT_ZERO           != ACCEPTANCE_PASSED
ESTIMATED_COST              != BILLED_SPEND
FIXTURE_RUN                 != REAL_RUNTIME_COMPATIBILITY
DECLARATIVE PERMISSION      != ENFORCED BOUNDARY
```

This package never merges, never grants an owner gate, never widens a program,
and never treats anything a worker wrote as authority.

## Invocation

The supervisor is registered on the Atlas CLI, so every command below is an
`atlas` command. The module entry point
(`python -m project_atlas.orchestration.program.cli`) accepts exactly the same
arguments and delegates to the same dispatcher, so the two cannot drift.

```bash
atlas program capabilities                      # runtime-tested / fixture-only / unavailable
atlas program runtimes                          # what this machine supports, and cannot
atlas program validate    --program P           # validate; report the enforcement picture
atlas program credentials --program P           # which account, by name and presence only
atlas program start       --program P --state-root S [--registry R]
atlas program status      --program P --state-root S
atlas program control     --program P --state-root S [--action pause|resume|cancel|reconcile]
atlas program cancel      --program P --state-root S   # same request, without the control contract
atlas program reconcile   --program P --state-root S
atlas program events      --program P --state-root S --limit 50
atlas program handoff     --program P --task T --session-id S --enrolled-by you
atlas program service     install|start|stop|status|run --program P

atlas agent enroll     --registry R --agent-id A --role implementer \
                       --adapter claude-code --workspace W --enrolled-by you
atlas agent assign     --registry R --agent-id A --program P --assigned-by you
atlas agent launch     --registry R --agent-id A
atlas agent list       --registry R
atlas agent status     --registry R --agent-id A
atlas agent set-status --registry R --agent-id A --status ACTIVE|SUSPENDED|RETIRED
```

A worked end-to-end path through all of these, with two real runtimes, is in
`OPERATOR-JOURNEY.md` and runnable as `operator-journey.sh`.

| Topic | Page |
| --- | --- |
| Which runtimes are supported and what they cannot do | `SUPPORT-MATRIX.md` |
| Who enforces which permission | `PERMISSIONS.md` |
| Registering agents and the four identities | `ENROLLMENT.md` |
| Running as a durable service | `SERVICE.md` |
| The contract Atlas Studio consumes | `CONTROL.md` |
| What was reused from the existing control plane | `REUSE-MAP.md` |
| **Launching it for real** | `LAUNCH.md` |
| **How continuation works** | `CONTINUATION.md` |
| The end-to-end operator path | `OPERATOR-JOURNEY.md` |
| The three-tier capability matrix | `CAPABILITY-MATRIX.md` |
| Picking the first real program | `WORK-SELECTION.md` |
| A program to fill in | `programs/first-program-TEMPLATE.json` |
| Real-runtime evidence | `evidence/` |

Every command prints one JSON object. Exit codes follow the repository
convention: `0` success, `1` operational error, `2` usage error.
`--state-root` overrides where program state lives; it defaults to the program
file's own directory and never lives inside the workspace, so a worker's diff
can never contain the supervisor's checkpoints.

### Not wired into `atlas`

Deliberately. `src/project_atlas/cli.py` is under a structural guard
(`tests/unit/test_atlas3_demo_isolation_001.py::test_cli_mutation_is_additive_only`)
requiring every diff to it to add an Atlas 3 parser hook. A supervisor
subcommand is not that. `cli.register_program_parser(subparsers)` plus one
dispatch line is the whole wiring whenever an owner grant exists.

## The program file

```json
{
  "schema_version": 1,
  "program": {
    "program_id": "my-program",
    "objective": "...",
    "approved_by": "wesley",
    "approval_reference": "docs/…/APPROVAL.md",
    "workspace_root": "path/to/repo",
    "base_pin": "<40-char sha>",
    "limits": {"max_task_launches": 20, "max_attempts_per_task": 3},
    "tasks": [ { "task_id": "…", "instruction": "…", "acceptance": [ … ] } ]
  },
  "profile_defaults": { "adapter": "claude-code" },
  "profiles": { "implementer": { "agent_id": "…", "capabilities": ["IMPLEMENT"] } }
}
```

`approved_by` / `approval_reference` are provenance, not a credential. A
program that names an approver is not thereby authorized to do anything an
owner gate would hold.

`workspace_root` resolves relative to the program file when it is not absolute.
A working example is in `evidence/real-runtime-demo-program.json`.

### Tasks

| Field | Meaning |
| --- | --- |
| `task_id`, `title`, `instruction` | The instruction is data for the worker, never an instruction to Atlas |
| `depends_on` | Dependencies. Cycles are rejected at load |
| `profile_ref`, `profile_override` | The execution contract; overrides may only narrow |
| `mutation_paths`, `surface_id`, `surface_semantic` | Feeds the existing overlap gate |
| `acceptance` | One or more conditions the **supervisor** checks locally |
| `external_precondition` | The task waits for an event before becoming eligible |
| `owner_gate` | Never dispatched autonomously |
| `requires_independent_verification`, `verifier_profile_ref` | Its own worker can never satisfy this |
| `retry_safe_when_no_launch_evidence` | Only for a task whose effect is repeatable |

### Acceptance kinds

`COMMAND` (fixed argv, exit 0 passes), `FILE_EXISTS`, `FILE_MATCHES` (regex),
`GIT_TREE_CHANGED`. All are run by the supervisor from the workspace after the
worker exits. None of them ask the worker how it went.

## Permissions

See `PERMISSIONS.md` — it states per field whether Atlas, the runtime or the
OS is what actually stops a violation, and names the things nothing enforces.

## Recovery

State lives in `<state-root>/.atlas/orchestration/program/`: `state.json` (the
reconciled picture, rewritten atomically) and `events.jsonl` (append-only,
fsynced, written *before* the effects it names).

On restart every non-terminal attempt is classified before anything is
selected:

| Phase found | What happens |
| --- | --- |
| `INTENT_RECORDED` | The ambiguity window. The adapter is asked whether that exact run ever started (possible because the session id is assigned and written down before launch). Evidence it started → resume that session, or stop. No evidence and the task declares its effect repeatable → relaunch. Otherwise → stop |
| `ADAPTER_INVOKED` | Live process with a matching start identity → still ours. Gone → resume the session if the adapter supports it, else stop |
| `ADAPTER_RETURNED` | Acceptance is read-only and repeatable, so just evaluate it — unless the outcome was `UNCERTAIN`, in which case confidence outranks phase and it stops |
| `ACCEPTANCE_EVALUATED` | Seal it |

**Nothing is ever redispatched because the supervisor could not tell what
happened.** `RECONCILE_REQUIRED` stops the program and
`program reconcile --resolve-uncertain <attempt-id>` records *your* judgement
that the effect did not land. That is your assertion, not the supervisor's
determination.

Recovery is not replay: a resumed session continues the worker's own
conversation; it never re-runs a completed one.

## Failure classes

`TRANSIENT_INFRASTRUCTURE` and `ACCEPTANCE_FAILED` are retried within the
attempt budget. `INVALID_TASK_INPUT`, `POLICY_REFUSAL`, `QUOTA_OR_CREDENTIAL`,
`NO_PROGRESS`, `LIMIT_EXHAUSTED` and `CANCELLED` are never retried.
`UNCERTAIN_OUTCOME` stops for reconciliation.

Quota and credential failures are never retried in a loop, and this package
offers no way to switch provider to evade an account limit.

Progress is measured as a fingerprint of the workspace's tracked-file status
and HEAD — not a count of messages or commits. A worker that runs again and
leaves a byte-identical tree has made none, and the task stops.

## Limits

`max_task_launches` (whole program), `max_attempts_per_task`,
`max_task_seconds`, `max_program_seconds`, `max_cycles`, `max_idle_cycles`,
and `max_estimated_cost_usd`. Every one but the last is enforced against
something this package counts. The last is forwarded to the runtime's own
budget flag and compared against the runtime's client-side estimate.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `PROGRAM_DIGEST_DRIFT` | The program file changed after the program started. Restore it or start a new program |
| `SUPERVISOR_DOUBLE_START` | Another supervisor owns this state root. `status` shows `supervisor_pid`/`supervisor_alive` |
| `RECONCILE_REQUIRED` on start | A previous run left an attempt with no outcome. Run `reconcile` |
| `RUNTIME_TOO_OLD` | `claude` predates 2.1.259 (`--permission-prompts`) or the profile's `adapter_min_version` |
| `CREDENTIAL_MECHANISM_CONFLICT` | A `SUBSCRIPTION_OAUTH` profile allow-lists `ANTHROPIC_API_KEY` |
| Every launch fails `QUOTA_OR_CREDENTIAL` | Check which account the runtime is using — an inherited `ANTHROPIC_API_KEY` overrides a subscription login |
| `NO_ELIGIBLE_WORK` with tasks left | Look at `status.blocked` and `status.owner_decision_required` |
| Program never finishes, `waiting_on_external_event` non-empty | The precondition's probe is not reporting its pass status. `events` shows each poll |

## Concurrency

`max_concurrent_workers` defaults to **1**. Sequential execution is the proven
case, and a program gets concurrency because it asked for it.

Raising it relaxes nothing else. The surface-overlap gate still refuses two
tasks that share a mutation path or semantic; the durable lease projection
still refuses a second active lease for one task **or one agent** — an enrolled
agent is one worker, not a pool — and owner gates still hold. What the number
bounds is how many *non-conflicting* tasks may be in flight.

Only `adapter.run()` leaves the supervisor's thread. Leases, dispatch intent,
transitions, acceptance and settlement all happen on the supervisor's own
thread, so program state is never mutated from more than one place.

`max_concurrent_workers_observed` in the report is measured, not configured: a
program that permits four workers and never ran more than one has not
demonstrated concurrency.

## Limitations

* Cursor and Copilot are inventoried but **not adapted** — their output
  contracts could not be verified because both accounts are at a limit. See
  `SUPPORT-MATRIX.md`; the reason is recorded rather than glossed.
* Acceptance is only as good as the conditions a program declares. These
  conditions are checked; the *right* conditions are the program author's job.
* `mutation_paths` bounds what Atlas will dispatch, not what a running worker
  can touch. Only Codex's `--sandbox` and Claude Code's `--restricted` are real
  filesystem boundaries.
* Cost is an estimate where it is reported at all, and Codex reports none.
* A program file is immutable while it runs; changing it is
  `PROGRAM_DIGEST_DRIFT`, not a hot reload.
* Independent verification uses a second configured agent or waits for a
  person. There is no third option.
