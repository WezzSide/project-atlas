# Real-runtime demonstration — Codex

The companion to `REAL-RUNTIME-DEMO.md`. Same supervisor, same scheduling
path, a different runtime with a materially different interface.

## What ran

Two tasks, the second depending on the first, in a disposable git repository.
No user prompt between them.

- `codex-step-a` — create `alpha.txt` containing `CODEX-STEP-A-DONE`.
- `codex-step-b` — read `alpha.txt`, create `beta.txt` containing
  `DERIVED-FROM:<that content>`.

Program: `codex-runtime-demo-program.json`. Report:
`codex-runtime-demo-report.json`. Event log:
`codex-runtime-demo-events.jsonl`.

## Environment

| Fact | Value |
| --- | --- |
| Runtime | `codex-cli 0.153.4` |
| Model | runtime default (none pinned) |
| Permission mode | `acceptEdits` → `--sandbox workspace-write` |
| Credential mechanism | `SUBSCRIPTION_OAUTH` (`codex login`) |
| Host | Linux 7.0.0-31-generic, Python 3.12.14 |
| Date | 2026-09-10 |

## Result

```
stop_reason        PROGRAM_COMPLETE
program_complete   true
cycles_run         3
launches_this_run  2
tasks_dispatched   codex-step-a (cycle 1, NEW), codex-step-b (cycle 2, NEW)
estimated_cost_usd 0.0   ← see below
```

Workspace afterwards:

```
alpha.txt  CODEX-STEP-A-DONE
beta.txt   DERIVED-FROM:CODEX-STEP-A-DONE
```

Session identities captured from the streamed event files:

```
codex-step-a  thread 01a08aac-2a62-7941-94b7-19d672d11023
codex-step-b  thread 01a08aac-9220-7491-9468-8b19aaa691ca
```

Token usage was recorded per attempt (93,376 input / 775 output for step A;
93,085 / 540 for step B).

## About that `estimated_cost_usd: 0.0`

Codex reports **no cost figure at all** — `turn.completed` carries token counts
and nothing else. The adapter reports `estimated_cost_usd = None` per attempt
rather than inventing a number, and the program total is therefore `0.0`
because nothing was ever added to it. Read it as *unknown*, not as *free*. The
matrix says `reports_cost: false` and `supports_cost_limit: false` for exactly
this reason, and a Codex program's spend is bounded by launches, attempts and
wall clock instead.

## What this establishes, and what it does not

It establishes that the Codex adapter launches `codex exec`, delivers the
instruction on stdin, maps `permission_mode` onto the runtime's sandbox, parses
the JSONL event stream, recovers the runtime-minted thread id from the streamed
file, and that the supervisor continued from one task to the next unprompted.

It does not establish anything about a long or difficult task, about resume
after a real interruption against this runtime (the resume *contract* is
covered by unit tests, the live path is not), or about any runtime other than
this one at this version.

## The sandbox is real, and it exits 0 anyway

A separate probe asked Codex under `--sandbox read-only` to create a file. It
did not hang, did not prompt, and **exited 0** — with a completed turn and the
message "Unable to create `blocked.txt`: the current workspace is read-only."
The file was not created.

Two things follow. The sandbox is genuinely enforced by the runtime, which is
more than `--add-dir` gives on Claude Code. And a clean exit with a completed
turn is still not evidence the task was done — which is why acceptance is
evaluated by the supervisor from the workspace and never read off the worker's
report.
