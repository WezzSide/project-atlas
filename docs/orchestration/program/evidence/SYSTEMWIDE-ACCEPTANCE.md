# Systemwide acceptance — Codex and Claude under one supervisor

The headline claim: **Codex and Claude workers progressing through separate
approved task queues under one supervisor, with ownership protection, explicit
limits, and no user prompt between eligible tasks.**

Real runtimes, not fixtures. `FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY`, and
this page is on the other side of that line.

## What ran

One approved program, four tasks, two queues that never touch each other:

```
claude-a ──► claude-b        (agent claude-worker, runtime claude-code)
codex-a  ──► codex-b         (agent codex-worker,  runtime codex)
```

Each task writes one file and is accepted on a `FILE_MATCHES` regex the
supervisor evaluates against the workspace after the worker exits. The
dependency inside each queue means the second task can only pass if the first
really ran. The two queues have disjoint mutation paths and disjoint surfaces,
so they are free to overlap — and the point of the run is that they did.

`max_concurrent_workers: 2`.

Program: `systemwide-acceptance-program.json`. Report:
`systemwide-acceptance-report.json`. Event log:
`systemwide-acceptance-events.jsonl`.

## Result

```
stop_reason                       PROGRAM_COMPLETE
program_complete                  true
cycles_run                        5
launches_this_run                 4
max_concurrent_workers_observed   2
estimated_cost_usd                0.289126   (Claude only; Codex reports none)

tasks_dispatched
  cycle 1  claude-a   NEW      ┐ both launched in the same cycle,
  cycle 1  codex-a    NEW      ┘ two runtimes in flight at once
  cycle 2  claude-b   NEW
  cycle 3  codex-b    NEW
```

Workspace afterwards:

```
claude-1.txt  CLAUDE-A-DONE
claude-2.txt  CLAUDE-B-DONE
codex-1.txt   CODEX-A-DONE
codex-2.txt   CODEX-B-DONE
```

`max_concurrent_workers_observed: 2` is measured, not configured. A program
that permits two workers and never runs more than one has not demonstrated
concurrency, so the report carries what actually happened rather than what was
allowed.

## Which acceptance criteria this covers

| Criterion | Where |
| --- | --- |
| More than one task without another user prompt | four tasks, one `start` invocation |
| Codex and Claude in separate queues, one supervisor | `tasks_dispatched`, two adapters, two agents |
| Existing DAG, ownership and authorization reused | `select_next`, `autonomy.dag`, `autonomy.leases`, the durable lease projection — see `REUSE-MAP.md` |
| Task completion distinct from program completion | `TASK_ACCEPTED` ×4 precede the single `NOTIFY_PROGRAM_COMPLETE` in the event log |
| Ownership protection | one active lease per task and per agent; `LEASE_GRANTED`/`LEASE_RELEASED` pairs in the log |
| Explicit limits | `max_task_launches: 8`, `max_concurrent_workers: 2`, per-task and per-program wall clock |
| No duplicate dispatch | exactly four `DISPATCH_INTENT` events, one per task |

Restart recovery, cancellation, owner gates and the acceptance-fails-anyway
case are covered by the test suites rather than by this run; injecting a crash
into a paid run buys nothing a fixture cannot prove more precisely.

## What this run does not establish

* Nothing about a long or difficult task. These are four one-line file writes.
* Nothing about a runtime other than `claude 2.1.267` and `codex-cli 0.153.4`
  on this host.
* Nothing about billed spend. `0.289126` is Claude Code's own client-side
  estimate for two launches; Codex reports no cost figure at all and
  contributed `0` to that total, which means *unknown*, not *free*.
* Nothing about more than two concurrent workers.

## A defect this run found

Cycle 4 emitted `NO_ELIGIBLE_WORK — "no task is eligible and nothing is
pending"` while `codex-b` was still `ACTIVE`. It is visible in the committed
report, which is left as it was rather than re-run.

Nothing behaved wrongly: the stop reason was already treated as provisional
while workers are in flight, cleared, and the program went on to complete. But
the operator was told something false, and a notification that can be false is
worse than no notification at all.

Fixed by `_notify_unless_busy`, which withholds the four provisional
notifications while any worker is running, and covered by
`test_no_false_idle_notification_while_a_worker_is_running`. The committed
report therefore shows behaviour the current code no longer produces — said
plainly here rather than repaired by quietly spending another four launches.

## Reproduce

```bash
python -m project_atlas.orchestration.program.cli program validate \
  --program docs/orchestration/program/evidence/systemwide-acceptance-program.json
# point workspace_root and base_pin at a disposable repo of your own, then:
python -m project_atlas.orchestration.program.cli program start --program <your copy>
```

Both runtimes must be installed and logged in (`claude`, `codex login`). Note
that an inherited `ANTHROPIC_API_KEY` overrides a Claude subscription login;
the profile's `SUBSCRIPTION_OAUTH` mechanism refuses to allow-list it for
exactly that reason.
