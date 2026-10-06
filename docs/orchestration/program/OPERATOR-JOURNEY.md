# The operator journey

One reproducible path through the Atlas entry point: enrol two workers on two
different real runtimes, approve a program, run it, inspect it, pause and
resume it, prove restart is not replay, and prove revoked authority is refused.

```bash
docs/orchestration/program/operator-journey.sh /tmp/atlas-journey
```

Needs `claude` (logged in) and `codex` (`codex login`). It costs four real
worker launches. Nothing it does merges, grants an owner gate, or raises a
limit.

Full captured output: `evidence/operator-journey-transcript.txt`.

## What the recorded run established

| Step | Claim | Result |
| --- | --- | --- |
| 1 | what this machine can actually run | three-tier capability matrix |
| 3 | the program validates before anything runs | `valid: true`, no warnings |
| 4 | which account each profile will use | names and presence only; no values |
| 5–6 | two workers enrolled and assigned | `claude-worker`, `codex-worker` |
| 7 | the four identities, side by side | four distinct lifetimes |
| 8 | **pause withholds dispatch** | `PAUSED`, `launches_this_run: 0` |
| 9 | **both runtimes, no prompt between tasks** | `PROGRAM_COMPLETE`, 4 launches, **peak concurrency 2** |
| 12 | **restart is not replay** | `PROGRAM_COMPLETE`, `launches_this_run: 0` |
| 13 | **revoked authority is refused** | `AGENT_NOT_ACTIVE`, file never written, **zero model calls** |
| 15 | the workers really did the work | all four files, correct contents |

Dispatch order in step 9 was `claude-first`, `codex-first` (concurrently), then
`claude-second`, `codex-second` — each queue advancing on its own dependency
while the other ran.

## Two things this journey does not prove on its own

**Step 13 refuses at the enrolment gate, not the in-cycle one.** `agent assign`
and `agent launch` both reject a SUSPENDED agent before a supervisor is even
constructed, which is what the transcript shows. There is a second, later check
— `_authority_revoked`, run immediately before each dispatch — for the case
that matters more: authority withdrawn *while a program is already running*.
That one cannot be shown by a script that suspends an agent between runs, so it
is covered by `test_suspending_an_agent_stops_the_next_dispatch`,
`test_un_enrolling_an_agent_mid_program_stops_dispatch` and
`test_withdrawing_a_runtime_substitution_grant_stops_dispatch`, each asserting
the second task never launched.

**Four one-line file writes are not a hard task.** The journey proves the
lifecycle, not that these runtimes are good at difficult work.

## A defect this journey found

The first draft ran a throwaway `program start` in step 8 to create the durable
record before pausing — because `pause` refused to act on a program that had
never started. That throwaway start **executed the entire program**, so step 9
reported `launches_this_run: 0` while claiming to have run everything.

The wording was not the bug. Pausing a program before starting it is
legitimate, and is the safest moment to pause one; refusing it forced the only
way to create state to be a dispatch. `pause` and `resume` now initialise the
record themselves, the throwaway start is gone, and
`test_pausing_an_unstarted_program_creates_no_dispatch` covers it.

## Commands the journey uses

```
atlas program capabilities
atlas program validate     --program P
atlas program credentials  --program P
atlas agent   enroll       --registry R --agent-id A --role ROLE --adapter RUNTIME
                           --workspace W --enrolled-by YOU
atlas agent   assign       --registry R --agent-id A --program P --assigned-by YOU
atlas agent   status       --registry R --agent-id A
atlas agent   list         --registry R
atlas agent   set-status   --registry R --agent-id A --status SUSPENDED
atlas agent   launch       --registry R --agent-id A
atlas program start        --program P --state-root S --registry R
atlas program status       --program P --state-root S
atlas program control      --program P --state-root S [--action pause|resume|cancel|reconcile]
atlas program events       --program P --state-root S --limit N
atlas program service      install|start|stop|status|run --program P
atlas program reconcile    --program P --state-root S
atlas program handoff      --program P --task T --session-id S --enrolled-by YOU
atlas program runtimes
```

`--registry` on `start` binds every **ACTIVE** enrolled agent whose role the
program declares. Their identities become the principals on the leases, their
narrowing applies, and their authority is re-read before every dispatch. A
SUSPENDED or RETIRED agent is skipped rather than bound and then refused
repeatedly.
