# Permission boundaries — who actually enforces what

`DECLARATIVE PERMISSION != ENFORCED BOUNDARY`. A profile field that Atlas
merely records is not a sandbox. This page says, per field, which layer stops
a violation — Atlas, the runtime, or the operating system — so nobody reads a
profile and assumes more containment than exists.

## Atlas enforces

These are refused by this package before a worker starts, and a refusal is a
validation error or a stopped cycle, not a warning.

| Field / rule | What is refused |
| --- | --- |
| `depends_on` | A task never starts before its dependencies are `CERTIFIED`/`CLOSED` (`autonomy.continuation.select_next`) |
| `owner_gate` | An owner-gated task is never dispatched. No code path in this package can grant a gate (`autonomy.owner_gates` fails closed) |
| `mutation_paths` vs `allowed_mutation_prefixes` | A task declaring a path outside its profile's prefixes fails to load |
| `surface_id` / `surface_semantic` / `mutation_paths` | Overlapping surfaces are never worked in parallel (`autonomy.overlap`) |
| task `profile_override` | Any widening — tools, permission mode, capabilities, limits, mutation surface, workspace dirs, dropped evidence — raises `AuthorityExpansionError` at load |
| `required_evidence` | A task omitting an evidence kind its profile requires fails to load |
| `requires_independent_verification` + `verifier_profile_ref` | A verifier resolving to the implementer's `agent_id` fails to load, and is re-checked before certification |
| `limits.*` (program and profile) | Launch count, attempts per task, per-task seconds, program seconds, cycles, idle cycles |
| program file digest | A program file edited after the program started refuses to continue |
| supervisor lock | Two supervisors cannot own one program's state root |
| lease projection | Two active leases for one task, or a lease from a different base pin, are refused |

## The runtime enforces

Passed through to the agent runtime, which applies them in its own process.
Atlas records what it asked for; the runtime is what says no.

| Profile field | Flag | Notes |
| --- | --- | --- |
| `permission_mode` | `--permission-mode` | `bypassPermissions` means the runtime enforces **nothing**; `validate` warns |
| — | `--permission-prompts none` | Always passed. Anything that would prompt is denied rather than hanging unattended |
| `allowed_tools` | `--allowedTools` | Permission-rule syntax, e.g. `Bash(git diff *)` |
| `disallowed_tools` | `--disallowedTools` | |
| `tools` | `--tools` | Restricts the built-in set; `[]` disables all |
| `workspace.restricted` | `--restricted` | This is the flag that makes `additional_dirs` a boundary |
| `limits.max_estimated_cost_usd` | `--max-budget-usd` | Enforced against the runtime's **client-side estimate**, not your bill |
| `result_schema` | `--json-schema` | Validates the worker's final answer's shape. Evidence, not acceptance |
| `isolated_runtime` | `--bare` | Skips hooks, plugins, MCP, CLAUDE.md — and changes credential resolution, see below |

## The operating system enforces

| Thing | How |
| --- | --- |
| Working directory | The child's cwd. A worker starts in the workspace (or a profile subdirectory of it) |
| Environment | Built explicitly from `env_allowlist` plus `PATH`/`HOME`/`LANG`/`LC_ALL`/`TZ`/`TMPDIR`. Not inherited wholesale |
| Termination | SIGTERM then SIGKILL to the worker's **process group**, so the runtime's own children go too |
| Wall clock | The supervisor's own timer, backed by killing the group |

## Enforced by nobody — state this plainly

* **`workspace.additional_dirs` without `restricted`.** Documentation only. A
  worker holding Bash can read and write anywhere its OS user can.
* **`mutation_paths` against the worker.** Atlas refuses to *dispatch* a task
  whose declared surface is too wide, and records the authorized paths on the
  lease. Nothing stops a worker that has already started from touching a file
  outside them. Acceptance is what catches the result; a real filesystem
  boundary would need `restricted`, a container, or an OS sandbox.
* **`max_estimated_cost_usd` as spending control.** It is a client-side
  estimate. It is not an account limit and this package never calls it one.
* **Anything a worker writes.** Repository text, plans, result messages and
  commit messages are data. None of them can enlarge a program, change a
  profile, or grant a gate.

## Credentials

Credentials never appear in a profile, a program file, a checkpoint, an
evidence document, or a report. A profile names a `credential` *mechanism* and
an `env_allowlist` of variable **names**; values are read from the supervisor's
environment at launch and passed to the child.

| Mechanism | What it means |
| --- | --- |
| `SUBSCRIPTION_OAUTH` | The runtime uses the operator's logged-in credentials. `ANTHROPIC_API_KEY` is deliberately not forwarded, and a profile that allow-lists it is refused |
| `ANTHROPIC_API_KEY_ENV` | Allow-list `ANTHROPIC_API_KEY` and set it in the supervisor's environment |
| `API_KEY_HELPER` | Supply an `apiKeyHelper` through the runtime's own settings |
| `NOT_APPLICABLE` | The local-command fixture adapter, which authenticates to nothing |

**Why the API-key rule exists**, concretely. Claude Code prefers
`ANTHROPIC_API_KEY` over a logged-in subscription when both are present. During
this package's own development, an inherited key in the operator's shell sent
every probe to a different account, which was out of credit; the runtime
returned HTTP 400 `Credit balance is too low` with exit status 1 — and, in the
same payload, `"subtype": "success"`. Building the child environment explicitly
is what stops that, and classifying on `is_error`/`terminal_reason`/
`api_error_status` rather than `subtype` is what stops it being misread.

Note that `--bare` (`isolated_runtime`) never reads OAuth credentials or the
keychain: under it, `SUBSCRIPTION_OAUTH` cannot work and an API key or
`apiKeyHelper` is required.
