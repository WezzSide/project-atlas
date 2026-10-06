# Operational launch handoff

Everything below is a real command against the implemented CLI. Nothing here
installs a system service, and nothing claims persistence that has not been
demonstrated.

## Prerequisites

| Thing | Value |
| --- | --- |
| Working directory | any; the supervisor never uses the CWD to find anything. Paths in the program file are absolute or relative **to the program file** |
| Installation | `pip install -e ".[dev]"` from a checkout of this branch, into a Python **3.12+** environment. The `atlas` console script is the entry point |
| Runtimes | `claude` ≥ 2.1.259 logged in, and/or `codex` with `codex login`. Check with `atlas program capabilities` |
| State location | `<--state-root>/.atlas/orchestration/program/` — `state.json`, `events.jsonl`, `evidence/`, `leases.json`, `service/`. Defaults to the **program file's own directory**; never inside the workspace, so a worker's diff cannot contain the supervisor's checkpoints |
| Registry location | `<--registry>/.atlas/orchestration/program/agents.json` |

Verify the install reaches the supervisor:

```bash
atlas program capabilities | head -20
```

## 1. Create an approved program and enrol workers

```bash
# A program file is data. Write it, or copy the template:
cp docs/orchestration/program/programs/first-program-TEMPLATE.json ./my-program.json
$EDITOR ./my-program.json          # fill the OPERATOR-SUPPLIED fields

atlas program validate --program ./my-program.json      # refuses before anything runs

atlas agent enroll --registry ~/atlas-agents \
  --agent-id claude-worker --role implementer --adapter claude-code \
  --workspace /path/to/repo --enrolled-by "$USER"

atlas agent assign --registry ~/atlas-agents \
  --agent-id claude-worker --program ./my-program.json --assigned-by "$USER"

atlas agent list --registry ~/atlas-agents
```

`atlas program credentials --program ./my-program.json` shows which account each
profile will authenticate as **before** you spend anything.

## 2. Start, inspect, pause, resume

```bash
atlas program start   --program ./my-program.json --state-root ~/atlas-state --registry ~/atlas-agents
atlas program status  --program ./my-program.json --state-root ~/atlas-state
atlas program control --program ./my-program.json --state-root ~/atlas-state   # the full view

atlas program control --program ./my-program.json --state-root ~/atlas-state \
  --action pause  --requested-by "$USER"
atlas program control --program ./my-program.json --state-root ~/atlas-state \
  --action resume --requested-by "$USER"
```

**Pause is reversible and does not interrupt.** It withholds new dispatch and
lets in-flight workers finish; the result names each one still running. Cancel
is the one that interrupts, and its outcomes are recorded `UNCERTAIN`.

## 3. Recover after supervisor interruption

Just start it again. Every start reconciles before it dispatches.

```bash
atlas program start --program ./my-program.json --state-root ~/atlas-state --registry ~/atlas-agents
```

If it stops with `RECONCILE_REQUIRED`, an attempt's outcome is unknown:

```bash
atlas program reconcile --program ./my-program.json --state-root ~/atlas-state
```

That **inspects**. It settles nothing on its own.

## 4. Uncertain execution — never replayed automatically

An interrupted attempt is classified from evidence, not assumed. Where the
runtime can continue its own session it is resumed; otherwise the program stops
and waits for you. Nothing is redispatched because the supervisor could not
tell what happened.

To settle one, you assert that its effect did not land:

```bash
atlas program control --program ./my-program.json --state-root ~/atlas-state \
  --action reconcile --attempt-id <ATTEMPT_ID> --requested-by "$USER"
```

The record says this was **your** judgement, not the supervisor's determination.

## 5. Stop workers and the supervisor safely

```bash
# Ask a running supervisor to stop before its next launch, and terminate any
# worker it is currently running (that worker's outcome becomes UNCERTAIN):
atlas program control --program ./my-program.json --state-root ~/atlas-state \
  --action cancel --requested-by "$USER"

# The same request without going through the control contract:
atlas program cancel --program ./my-program.json --state-root ~/atlas-state

# If it was started as a detached service:
atlas program service stop --program ./my-program.json --state-root ~/atlas-state
atlas program service status --program ./my-program.json --state-root ~/atlas-state --registry ~/atlas-agents
```

A worker is terminated as a **process group**, so the runtime's own children go
with it. Prefer `pause` when you only want it to stop taking on more.

## Credential selection

The child environment is **built**, never inherited wholesale: the profile's
`env_allowlist` plus `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ`, `TMPDIR`.

Under `SUBSCRIPTION_OAUTH`, `ANTHROPIC_API_KEY` is deliberately **not**
forwarded, because Claude Code prefers it over a logged-in subscription — an
inherited key silently redirects every run to another account. A profile that
declares `SUBSCRIPTION_OAUTH` *and* allow-lists that key is refused.

No account is switched automatically. No intentional credential is discarded.
No spending limit is raised.

## Concurrency and budgets

| Limit | Where | Enforced against |
| --- | --- | --- |
| `max_concurrent_workers` | program `limits` | default **1**; raising it relaxes nothing else |
| `max_task_launches` | program `limits` | total worker launches, whole program |
| `max_attempts_per_task` | program `limits` | attempts at one task |
| `max_task_seconds` | program + profile | one launch; the tighter wins |
| `max_program_seconds` | program `limits` | one `start` invocation |
| `max_cycles`, `max_idle_cycles` | program `limits` | loop bounds |
| `max_estimated_cost_usd` | program + profile | forwarded to a runtime that has a budget flag, compared against **that runtime's client-side estimate** — not an account limit |

One enrolled agent is one worker, never a pool: two tasks sharing an agent
serialise even with free slots. Overlapping mutation surfaces never run
together.

## Foreground, detached, or service-managed — what actually survives

| Mode | Command | Survives terminal closure? | Survives host restart? |
| --- | --- | --- | --- |
| **Foreground** | `atlas program start …` | **No.** It is a normal child of your shell | No |
| **Detached** | `atlas program service start …` | **Yes — demonstrated.** New session/process group, stdout to `service/service.log`, identity written by the service itself | **No** |
| **Service-managed** | systemd unit written by `service install` | would, if you install it | **Not demonstrated. Not claimed** |

`atlas program service install` writes a launcher **and a systemd user unit as
text**. It activates nothing. Enabling it changes your machine, so the command
prints what to run and does not run it:

```bash
cp <unit> ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now atlas-program-<program_id>.service
```

**Restart persistence is not claimed.** No system service was installed and no
host restart was exercised. What *was* demonstrated is a detached service
running a program to completion after the launching shell returned.

Even without a live supervisor, nothing is lost: state is on disk, an
interrupted attempt stays `UNCERTAIN`, and a lease stays `ACTIVE` until a
successor reconciles it.
