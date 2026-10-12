# Real-runtime demonstration — Claude Code

`FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY`. The acceptance suite proves the
supervisor's behaviour with labelled fixture workers. This page is the separate
claim: the same scheduling path, driving the real Claude Code runtime.

## What ran

Two tasks, the second depending on the first, in a disposable git repository
outside this checkout. No user prompt between them.

- `step-a` — create `hello.txt` containing `ATLAS-STEP-A-DONE`.
- `step-b` — read `hello.txt`, create `world.txt` containing
  `DERIVED-FROM:<that content>`. Depends on `step-a`, so it can only pass if
  `step-a` really ran first.

Both acceptance conditions are `FILE_MATCHES` regexes evaluated by the
supervisor against the workspace after the worker exited. Neither consults the
worker's own report.

Program: `real-runtime-demo-program.json` (`workspace_root` is relative to the
program file). Report: `real-runtime-demo-report.json`. Event log:
`real-runtime-demo-events.jsonl`.

## Environment

| Fact | Value |
| --- | --- |
| Runtime | Claude Code `2.1.267` |
| Model | `sonnet` |
| Permission mode | `acceptEdits` |
| Tools | `Read, Write, Edit, Glob, Grep` |
| Credential mechanism | `SUBSCRIPTION_OAUTH` |
| Host | Linux 7.0.0-31-generic, Python 3.12.14 |
| Date | 2026-09-10 |

## Result

```
stop_reason        PROGRAM_COMPLETE
program_complete   true
cycles_run         3
launches_this_run  2
tasks_dispatched   step-a (cycle 1, NEW), step-b (cycle 2, NEW)
estimated_cost_usd 1.2861708
```

Workspace afterwards:

```
hello.txt  ATLAS-STEP-A-DONE
world.txt  DERIVED-FROM:ATLAS-STEP-A-DONE
```

## Reproduce

```bash
python -m project_atlas.orchestration.program.cli program validate \
  --program docs/orchestration/program/evidence/real-runtime-demo-program.json
python -m project_atlas.orchestration.program.cli program start \
  --program <your copy, with workspace_root and base_pin pointing at a real repo>
```

`base_pin` and `workspace_root` in the committed copy name the disposable
repository this run used; point them at your own before running it.

## What this demonstration does and does not establish

It establishes that the Claude Code adapter launches the installed runtime,
delivers the instruction, parses the structured result, and that the supervisor
continued from one task to the next on its own.

It does **not** establish: that acceptance would catch a subtle wrong answer
(these conditions are exact-string), that the cost figure is what was billed
(`total_cost_usd` is the runtime's own client-side estimate — the run reported
$1.29 for two small edits, which is an estimate under a subscription, not a
charge), or anything about any other runtime.

## What the first attempt found

The first end-to-end run failed, and it is recorded here rather than quietly
re-run. Every launch came back `UNCERTAIN` with
`ADAPTER_RAISED: I/O operation on closed file`.

`run_child_to_completion` closes the child's stdin so the runtime sees EOF on
its prompt, then calls `communicate()`, which closes `self.stdin` again and
raises. The fixture adapter passes no stdin, so no fixture test could reach the
path. The fix detaches the handle after closing it, and two regression tests
(`test_child_runner_delivers_stdin_and_still_collects_output`,
`test_child_runner_survives_a_child_that_never_reads_stdin`) now cover it.

Worth stating plainly: the supervisor behaved correctly throughout that
failure. It recorded UNCERTAIN, refused to retry, and stopped with
`RECONCILE_REQUIRED` — which is exactly what an unknown external effect is
supposed to produce.
