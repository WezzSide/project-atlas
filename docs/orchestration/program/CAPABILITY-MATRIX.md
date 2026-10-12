# Capability matrix

`atlas program capabilities` emits this live; a committed snapshot is
`evidence/capability-matrix.json`. Three tiers, kept apart on purpose.

> "We wrote an adapter" and "we ran it against the real thing" are different
> claims. A matrix that prints one word for both invites the reader to assume
> the stronger one.

## IMPLEMENTED_AND_RUNTIME_TESTED

An adapter exists **and** has been exercised end to end against the real
runtime. The evidence is named, not asserted.

| Runtime | Version tested | Evidence |
| --- | --- | --- |
| `claude-code` | 2.1.267 | `evidence/REAL-RUNTIME-DEMO.md`, `evidence/SYSTEMWIDE-ACCEPTANCE.md`, `evidence/operator-journey-transcript.txt` |
| `codex` | codex-cli 0.153.4 | `evidence/REAL-RUNTIME-DEMO-CODEX.md`, `evidence/SYSTEMWIDE-ACCEPTANCE.md`, `evidence/operator-journey-transcript.txt` |

### The concurrent acceptance, labelled precisely

**Claude Code + Codex concurrent program acceptance.** Two runtimes progressing
through separate approved task queues under one supervisor, two workers in
flight at once, four tasks, one invocation, no operator prompt between them.

It does **not** claim:

* anything about a runtime other than those two at those versions
* anything about long or difficult tasks — these were one-line file writes
* more than two concurrent workers
* anything about billed spend; the cost figure is a client-side estimate where
  it exists at all, and Codex reports none, so `0.0` there means *unknown*

## IMPLEMENTED_FIXTURE_ONLY

An adapter exists and passes its tests; no real-runtime run backs it.

| Runtime | Note |
| --- | --- |
| `local-command` | The labelled fixture worker. `FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY` |

## INVENTORIED_UNAVAILABLE

Present on the machine, deliberately not adapted, blocker named.

| Runtime | Version | Demand | Blocker |
| --- | --- | --- | --- |
| `cursor-agent` | 2026.09.08 | **high** — Atlas's existing `orchestration.sdk` lane is Cursor-based | account at its usage limit |
| `copilot` | 1.0.83 | moderate — used on this host, not orchestrated by Atlas | GitHub token present but the account is rate limited (403) |
| `gemini` | 0.58.0 | none observed | — |
| `aider` | 0.86.2 | none observed | — |
| `amp` | — | none observed | — |

Their flag-level capabilities are recorded in `SUPPORT-MATRIX.md`. What is
missing is what an adapter has to get right — the JSON output shape, how a
terminal state is signalled, the error taxonomy — and that needs a real run.

**Neither is being polled.** The blockers were established once. Raising a
limit, switching accounts or retrying on a timer is not authorized and is not
done; `atlas program capabilities` only reads `--version`, which is local and
free. When either account is available the verification is a short probe and
the adapters follow.

## What no runtime here can do

* attach to a **running interactive** session (both can resume a **stored** one)
* be adopted after the fact — a session enters a program only through an
  explicit handoff
* guarantee a worker stays inside its declared `mutation_paths`; only Codex's
  `--sandbox` and Claude Code's `--restricted` are real filesystem boundaries

## There is no generic adapter

`AdapterKind` has three members and no "any CLI" member, so a program file
cannot name a runtime nobody has verified.
