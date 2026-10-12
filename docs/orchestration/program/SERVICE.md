# Running the supervisor as a durable service

`SERVICE_INSTALLED != SERVICE_RUNNING != PROGRAM_AUTHORIZED`. Installing writes
a launcher. Starting runs a supervisor. Neither grants the program anything the
approved program file did not already say.

Uses the mechanism this repository already has for a long-lived governor host:
a detached process group, stdout to a log beside the state, a stop file for
graceful shutdown, and a recorded process identity that survives PID reuse
(`orchestration.sdk.host`).

## Install (writes a launcher, activates nothing)

```bash
python -m project_atlas.orchestration.program.cli program service install \
  --program PROGRAM.json --state-root ~/atlas-state --registry ~/atlas-agents
```

Writes two files under `<state-root>/.atlas/orchestration/program/service/`:

* `run-<program_id>.sh` — a foreground launcher you can run directly.
* `atlas-program-<program_id>.service` — a **systemd user unit as text**.

Nothing is activated. Enabling a service changes the operator's machine, so the
command prints what to run and does not run it:

```bash
cp <unit> ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now atlas-program-<program_id>.service
```

## Start, stop, inspect

```bash
python -m ...program.cli program service start   --program PROGRAM.json --state-root ~/atlas-state --registry ~/atlas-agents
python -m ...program.cli program service status  --program PROGRAM.json --state-root ~/atlas-state --registry ~/atlas-agents
python -m ...program.cli program service stop    --program PROGRAM.json --state-root ~/atlas-state
python -m ...program.cli program service run     --program PROGRAM.json --state-root ~/atlas-state --registry ~/atlas-agents
```

`run` is the service body itself; `start` detaches one. A detached service
requires `--registry` and binds every active agent whose role is declared by
the program and whose durable assignment names that exact program file. A
missing, unreadable, mismatched, or inactive assignment fails closed before a
worker launch. The prior unregistered mode remains available only through an
explicit `--allow-unregistered` flag for callers that intentionally use it.

## Who writes the recorded PID

The **service** writes its own identity, from inside its own process. The
launcher never does.

A launcher that records the PID it spawned is recording its own child, which on
some platforms is a shim that exits immediately — and a supervisor identity
naming a dead shim makes "is it still running?" unanswerable. This repository
has hit that defect class before (PR #766), so `start` waits for the service to
publish its own identity and fails with `SERVICE_DID_NOT_START` if it never
does.

`service_alive` checks the recorded start identity as well as the PID: a live
PID whose start identity differs is a stranger's process, not the supervisor.

## Rounds, and when the service stops

Each round is a full `supervisor.start()`, which itself runs many cycles.
Between rounds the service pauses rather than spinning.

| Stop reason | Service behaviour |
| --- | --- |
| `PROGRAM_COMPLETE` | exits |
| `OWNER_DECISION_REQUIRED` | exits — waiting on a person, not on time |
| `RECONCILE_REQUIRED` | exits — needs `program reconcile` |
| `HARD_BLOCKER`, `LIMIT_REACHED`, `CANCELLED` | exits |
| `AWAITING_INDEPENDENT_VERIFICATION` | exits |
| `NO_ELIGIBLE_WORK`, `WAITING_ON_EXTERNAL_EVENT`, `CYCLE_BUDGET_REACHED` | pauses `--poll-seconds` and runs another round |
| anything unrecognised | exits and names it, rather than looping on a state this code does not understand |

## Recovery is not replay

Every round begins with the same restart reconciliation any `start` does.
Interrupted attempts are classified from evidence, resumed where the runtime
supports it, and otherwise left for a person. A completed program run again
launches nothing — there is a test for exactly that.

| Interruption | What happens |
| --- | --- |
| **Supervisor restart** | attempts classified by phase and confidence; `INTENT_RECORDED` resolved by probing whether the run ever started; nothing redispatched on a guess |
| **Agent exit** | a worker finishing its session is a task event; the program continues to the next eligible task |
| **UI closure** | not a supervisor event at all. Status is a read of durable state by a separate process; reading it mutates nothing |
| **Host interruption** | the durable lease row stays ACTIVE, the event log is fsynced, and the next start rehydrates ownership rather than granting a second lease |

## Failure states, named separately

| State | Meaning | Retried? |
| --- | --- | --- |
| `TRANSIENT_INFRASTRUCTURE` | spawn failure, transient network, disk | yes, within the attempt budget |
| `ACCEPTANCE_FAILED` | the worker ran; the conditions do not hold | yes |
| `INVALID_TASK_INPUT` | the task itself is malformed | never |
| `POLICY_REFUSAL` | the runtime denied what the worker needed | never — the same denial would repeat |
| `QUOTA_OR_CREDENTIAL` | credentials rejected, or the account is out of quota | never, and never worked around by switching provider |
| `NO_PROGRESS` | ran again, changed nothing observable | never |
| `UNCERTAIN_OUTCOME` | the effect is unknown | never; stops for reconciliation |
| `LIMIT_EXHAUSTED` / `CANCELLED` | a bound was reached, or an operator stopped it | never |

A runtime that is missing, too old, or misconfigured is a named state too:
`RUNTIME_UNAVAILABLE` blocks the task and stops the program, rather than
raising.

**Policy refusal specifically.** A run whose `permission_denials` was non-empty
and whose acceptance then failed is classed `POLICY_REFUSAL`, not
`ACCEPTANCE_FAILED`. Retrying would be denied the same thing, and burning the
whole attempt budget on it helps nobody. Runtimes that cannot report denials
structurally report `0`, which means **not observed**, never "definitely none"
— see `SUPPORT-MATRIX.md`.

## Switching runtimes

Only when authorized, and the authorization is durable. `agent assign
--allow-runtime-substitution` records
`runtime_substitution_authorized` on the agent, and the supervisor reads that
record at launch. It is deliberately not a launch-time flag: an authorization
that lives only in the argv of whichever command happened to run is not one
anyone can audit, and an earlier version of this package bound enrolled agents
with substitution permitted unconditionally — so `assign` could refuse an
assignment the supervisor would then happily run.

Re-enrolling an agent clears the grant. It was given for a specific agent
record, and a grant that outlives the thing it was granted for is not a grant.
