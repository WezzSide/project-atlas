# Runtime support matrix

`SUPPORTED != INSTALLED != AUTHENTICATED`. Three different facts.

Generated live by `program runtimes` (a committed snapshot is in
`evidence/runtime-support-matrix.json`); this page is the prose behind it.

A runtime appears here only when a real adapter exists, written against that
runtime's own verified interface and exercised end to end. There is no generic
subprocess adapter, deliberately: one would let a program claim support for a
runtime nobody has checked, and "we can launch a process" is not the same
statement as "we understand this runtime's output, permissions and resume
contract".

## Capability comparison

| Capability | `claude-code` | `codex` | `local-command` (FIXTURE) |
| --- | --- | --- | --- |
| Verified version | 2.1.267 | codex-cli 0.153.4 | n/a |
| Non-interactive entry | `claude --print` | `codex exec` | fixed argv |
| Prompt delivery | stdin | stdin (`-`) | environment |
| Structured output | one JSON result object | JSONL event stream | none |
| Session id assigned **before** launch | **yes** (`--session-id`) | **no** — mints its own | yes (env + marker) |
| Session probe after a crash | transcript file | streamed event file | start marker |
| Resume a stored session | `--resume <id>` | `codex exec resume <id>` | **no** |
| Attach to a **live** session | **no** | **no** | **no** |
| Cost reported | estimate (`total_cost_usd`) | **none** (tokens only) | none |
| Per-launch spend cap | `--max-budget-usd` | **none** | none |
| Result schema | `--json-schema` (inline) | `--output-schema` (file) | none |
| Filesystem confinement | only under `--restricted` | `--sandbox` (real) | none |
| Unattended approvals | `--permission-prompts none` | non-interactive by design | n/a |

## The differences that change supervisor behaviour

**Assigned session identity.** Claude Code will use an id we hand it, so the
dispatch intent written *before* the launch already names an addressable
session. Codex mints its own `thread_id` and announces it in its first
`thread.started` event. The Codex adapter therefore streams the event file
straight to disk rather than buffering it in a pipe, so a supervisor that dies
mid-run still finds the identity — the difference between an interrupted
attempt being resumable and being merely unknown. The supervisor consults
`accepts_assigned_session` and does **not** pre-assign an id for Codex:
recording an identity the runtime never heard of is worse than recording none,
because a recovery probe would then look for it and find nothing, which reads
as "this never started".

**What counts as completion.** Claude Code carries `is_error`,
`terminal_reason` and `api_error_status` on one result object.  Codex has no
such object: completion is the presence of `turn.completed`. Neither adapter
trusts exit status alone, and for good reason in both cases —

* Claude Code returned exit 1 with `"subtype": "success"` in the same payload
  while failing on HTTP 400 `Credit balance is too low`.
* Codex, asked under `--sandbox read-only` to create a file, exited 0 with a
  completed turn and the message "Unable to create `blocked.txt`: the current
  workspace is read-only."

Both were observed, not imagined. Acceptance is what catches the second one,
and acceptance is the supervisor's job.

**Permission vocabulary.** `permission_mode` is runtime-neutral. Codex's
sandbox is narrower, so the mapping is one-way conservative: every mode that is
not unambiguously a write mode maps to `read-only`.

| `permission_mode` | Codex `--sandbox` |
| --- | --- |
| `plan`, `dontAsk`, `manual` | `read-only` |
| `acceptEdits`, `auto` | `workspace-write` |
| `bypassPermissions` | `danger-full-access` (+ approval bypass) |

**Cost.** Codex reports token usage and no currency. This package reports its
cost as unknown rather than estimating one, and declares
`supports_cost_limit = False` — there is no per-launch budget flag to forward.
A Codex program's spend is bounded by launches, attempts and wall clock, which
are things this package can actually count.

## Neither runtime can do these

* Attach to a **running interactive session**. Both can resume a session the
  runtime has **stored**, which is a different thing.
* Be adopted after the fact. This supervisor never takes over a process it did
  not start; see the handoff section below.
* Guarantee a worker stays inside its declared `mutation_paths`. Atlas refuses
  to *dispatch* outside them and records them on the lease; nothing stops an
  already-running worker with shell access. Codex's `--sandbox` and Claude
  Code's `--restricted` are the only real filesystem boundaries on offer.

## Controlled handoff

Because attachment is unsupported, the supported alternative is explicit:

```bash
python -m project_atlas.orchestration.program.cli program handoff \
  --program PROGRAM.json --task TASK --session-id <the runtime's stored id> \
  --enrolled-by wesley --note "continuing yesterday's session"
```

The next dispatch for that task continues that session in a **new supervised
run**, under this program's profile, limits, acceptance and ownership. No live
process is adopted, and the prior session's permissions do not carry over — the
profile decides, as for any other dispatch.

The enrolment is a recorded human act (`enrolled_by`, `note`), is corroborated
by the adapter's probe where possible (and says so plainly when it cannot be),
and is consumed exactly **once** — at the moment of dispatch, not after the run
returns, because a handoff still pending when the supervisor dies would resume
the same session again on the next start.

It is refused when the adapter has no resume contract, when a handoff is
already pending for that task, and when the task is already done.

## Not yet supported

Cursor and Copilot are not implemented. `cursor-agent` is installed on this
host and Atlas already has Cursor execution ports elsewhere
(`orchestration/sdk/backend.py`, `orchestration/sdk/cli_execution_port.py`),
but neither has been verified against **this** package's adapter contract, so
neither is claimed here. See the M7 entries in `MISSION-TASK-QUEUE.md`.

## Inventoried, deliberately not implemented

`program runtimes` also reports every agent runtime found on this machine that
this package does **not** adapt, with what was verified, what was not, whether
anything actually needs it, and any blocker. An absent row would read as
"nobody thought about it"; a row saying `adapter_implemented: false` with the
reason reads as what it is.

| Runtime | Installed | Demand | Status |
| --- | --- | --- | --- |
| `cursor-agent` 2026.09.08 | yes | **high** — Atlas's existing `orchestration.sdk` lane is Cursor-based | **blocked**: account at its usage limit |
| `copilot` 1.0.83 | yes | moderate — in use on this host, not orchestrated by Atlas | **blocked**: GitHub token present but the account is rate limited (403) |
| `gemini` 0.58.0 | yes | none observed | not adapted |
| `aider` 0.86.2 | yes | none observed | not adapted |
| `amp` | yes | none observed | not adapted |

### What was verified for the two the directive names

Flag-level facts, read from each installed CLI's own `--help`, which is
authoritative for the build that is installed:

**`cursor-agent`** — `-p/--print`; `--output-format text|json|stream-json`;
`--resume [chatId]` and `--continue`; `create-chat` returns a chat id *before*
any run; `--mode plan|ask` for read-only operation; `--force/--yolo`,
`--auto-review`, `--sandbox enabled|disabled`; `--workspace`, `--add-dir`,
`-w/--worktree`. And one thing only a real attempt revealed: **`--trust` is
required** for a non-interactive run in an untrusted directory — without it the
run exits 1 and says so.

**`copilot`** — `-p/--prompt <text>`; `--output-format json` emits JSONL, one
object per line; `--session-id <id>` **both** resumes a session **and** sets the
UUID for a new one, so an identity can be assigned before launch;
`-r/--resume[=id]`; `--allow-all-tools` is required for non-interactive mode;
`--allow-tool`/`--deny-tool`; `--add-dir`, `--allow-all-paths`, `--log-dir`;
`--acp` starts an Agent Client Protocol server.

### What was not verified, and why that matters

For both: the shape of the JSON output, how a terminal state is signalled, the
error taxonomy, and how a refusal differs from a failure.

Those are exactly the things an adapter has to get right. Both accounts are at
a limit — Cursor's usage limit resets at the end of its monthly cycle; the
GitHub token is rate limited — and raising a spend limit, switching accounts,
or routing around either is not authorized and would not be the right thing to
do regardless.

Shipping a classifier written against an unverified event shape would be a
support claim nobody has checked, which is the thing this page exists to
refuse. When either account is available again, the verification is a short
probe and the adapters follow.

### There is no generic adapter

> Launching a process is not the same as understanding a runtime's output, its
> terminal states, its permission model or its resume contract — and an adapter
> that does the first while claiming the rest is a support claim nobody has
> checked.

`AdapterKind` has three members and no "any CLI" member. A program file cannot
name a runtime that has no adapter, because there is nothing to name.
