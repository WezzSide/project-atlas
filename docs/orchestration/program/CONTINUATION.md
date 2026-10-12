# How continuation actually works

The property the whole package exists for: **a worker finishing one task causes
the next eligible task to launch, with no operator prompt in between.**

## One task finishing → the next starting

A cycle, in order:

1. **Settle** anything that finished since the last cycle. Settling comes first
   so a task that just completed can unblock its dependants in the *same*
   cycle, not the next one.
2. **Promote** every `DISCOVERED` task with no external precondition to
   `READY`.
3. **Poll** external preconditions once each, non-blocking.
4. **Complete?** Only if no worker is running and every task is
   `CERTIFIED`/`CLOSED`.
5. **Select** — `autonomy.continuation.select_next`, the existing engine:
   dependencies satisfied (`CERTIFIED`/`CLOSED`), no owner gate, no surface
   overlap with in-flight work, agent not already busy.
6. **Re-check ownership and authority**, then dispatch. An eligibility decision
   taken a moment ago is not a licence to dispatch now.
7. If workers are running and nothing more can start, **wait for the first one
   to finish** — never for all of them.

A task reaching `CERTIFIED` releases its lease. Its dependants' dependency
condition becomes true on the next selection, which happens immediately.

Nothing in that loop asks a person anything.

## External waits becoming eligible again

A task with an `external_precondition` stays `DISCOVERED` — deliberately, not
as a workaround. The existing DAG has **no edge from `BLOCKED` back to
`READY`**, so parking a merely-waiting task in `BLOCKED` would strand it
permanently, while `DISCOVERED → READY` is exactly the "became eligible"
transition the DAG already models.

Each cycle polls its probe (a fixed argv; exit status is the signal) through
`sdk.external_observers`, which supplies durable rows, exponential backoff, and
**once-only** terminal-event consumption. When the probe reports its pass
status the task is promoted once — a re-delivered event does not promote it
again, which is what stops a duplicate dispatch.

Meanwhile every other eligible task keeps running. `PENDING_EXTERNAL_EVENT` is
a property of one task, never of the program.

## Five states people conflate

| State | Means | What happens next |
| --- | --- | --- |
| **Task completion** | one task's acceptance conditions were observed to hold | its lease is released; the next eligible task starts unprompted |
| **Program completion** | *every* task is in an accepted state **and no worker is running** | the supervisor exits `PROGRAM_COMPLETE` |
| **Waiting** | a task's external precondition has not resolved | polled with backoff; unrelated work continues; the supervisor does not stop |
| **Reconciliation required** | an attempt's outcome is **unknown** | the program stops. Nothing is replayed. `reconcile` is the way out |
| **Supervisor termination** | this process ended | ends *none* of the above. State is on disk, an `UNCERTAIN` attempt stays uncertain, an `ACTIVE` lease stays active until a successor reconciles it |

`TASK_COMPLETE != PROGRAM_COMPLETE.` A worker's session ending is a task event.

## Can an existing interactive session be adopted?

**No — for either runtime, and not by design accident.**

Neither `claude` nor `codex` offers attachment to a *running interactive*
session. This supervisor additionally never adopts a process it did not start:
a supervisor that inherits a process cannot say what that process was
authorized to do, whose credentials it holds, or what it has already changed.

Both runtimes **can** resume a session they have **stored**. That is a
different thing, and it is the supported path.

### The supported path: a controlled handoff

```bash
# 1. Get the session id from the runtime that stored it:
#      claude  -> the `session_id` in its --output-format json result
#      codex   -> the `thread_id` in its first thread.started event
# 2. Enrol it against a task, explicitly and on the record:
atlas program handoff --program ./my-program.json --state-root ~/atlas-state \
  --task <TASK_ID> --session-id <SESSION_ID> --enrolled-by "$USER" \
  --note "continuing yesterday's session"
```

The next dispatch for that task continues that session **in a new supervised
run**, under this program's profile, limits, acceptance and ownership. The
prior session's permissions do not carry over — the profile decides, as for any
other dispatch.

The enrolment is a recorded human act (`enrolled_by`, `note`), is corroborated
by the adapter's probe where it can be and says so plainly when it cannot, and
is consumed **exactly once** — at dispatch, not after the run returns, so a
supervisor crash cannot resume the same session twice.

It is refused when the adapter has no resume contract, when a handoff is
already pending for that task, and when the task is already done.

**No session is ever enrolled automatically.** There is no discovery path, no
scan of running processes, and no code that reads another agent's session
directory looking for work to adopt.
