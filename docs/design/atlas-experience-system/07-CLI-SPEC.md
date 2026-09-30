# 07 — CLI Experience Specification

**Session:** `AS-20260903T132018Z-claude-design-continuation-20260903-project-atlas-60ba98bb`
**Status:** `SPECIFIED` — not implemented in this lane. Backlog `AX-008`.
**Addresses:** audit finding "67 top-level commands, 0 argument groups"

> The CLI is Atlas's **primary** surface: it is where the write pipeline lives (J-11) and
> the only surface with execution authority. Nothing here weakens that; the whole
> specification is additive and backward-compatible.

---

## 1. The problem, precisely

| Measure | Value |
|---|---|
| Top-level commands | 67 |
| Total parsers | 134 |
| `add_argument_group` calls | **0** |
| `cli.py` | 5,855 lines |

`atlas --help` therefore emits 67 undifferentiated command names in one list. Research R-2
gives the governing rule: *a command with subcommands should be an **area**, not an
action, and areas exist to organise help output.*

There is also a visible redundancy: **7 `<lens>` / `<lens>-status` pairs** — `overview`,
`state`, `changed`, `decisions`, `unknown`, `roadmap`, `next`. `overview-status` is
`overview` in a different output mode, not a different command. That is 7 of the 67 slots
spent on a flag.

## 2. The hard constraint: nothing may be renamed away

Atlas command names appear in governance documents, ADRs, receipts, `WORKLOG.md`, CI, and
agent skills. A rename is a truth-integrity event, not a UX change.

**Therefore: every one of the 67 existing command names keeps working, indefinitely, with
identical behaviour and exit codes.** This spec adds an organising layer above them. There
is no deprecation phase in which anything breaks.

## 3. Areas

Areas are derived from the job model in `02`, so the CLI and the web answer the same
questions in the same groupings.

| Area | Question | Commands grouped under it |
|---|---|---|
| `atlas project` | What does Atlas know? (J-1, J-2, J-3) | `overview`, `state`, `changed`, `decisions`, `unknown`, `brief`, `roadmap`, `attention`, `next` |
| `atlas ask` | Ask a governed question (J-4) | `ask2`, `query`, `kdiff` |
| `atlas source` | Is the input healthy? (J-6, J-11) | `discover`, `ingest`, `source-health`, `validate`, `build-indexes`, `build-portfolio`, `connect` |
| `atlas agent` | Autonomous work + handoff (J-7, J-9) | `context`, `context-pack`, `handoff`, `capture`, `inbox`, `obsidian`, `review` |
| `atlas ops` | Is the installation sound? (J-10) | `doctor`, `ops`, `snapshot`, `restore`, `retention`, `revocation`, `schema`, `compat`, `lifecycle` |
| `atlas graph` | Structure and federation (J-5) | `accept-graph`, `resolve-graph`, `store-graph`, `kf2`, `federation`, `register-global-*`, `xproj*` |

`atlas --help` becomes six areas plus `init`, `version`, `doctor` and `live` at top level —
roughly ten lines instead of 67. `atlas project --help` then lists that area's nine.

### Aliasing contract

```
atlas overview --project P          # keeps working, forever, unchanged
atlas project overview --project P  # new, identical behaviour
```

Implementation: register each existing top-level parser and add the area parser as a thin
dispatcher to the same handler. No handler is duplicated, so the two paths cannot diverge.
Legacy names are hidden from the top-level help listing (via `help=argparse.SUPPRESS`) but
remain fully functional and fully documented in `atlas help legacy`. **Hidden is not
removed** — this distinction must be stated in the release note, because for a
governance-sensitive tool the difference matters.

### Retiring the `-status` pairs without removing them

`<lens>-status` becomes `--status` on the lens:

```
atlas overview-status --project P    # keeps working
atlas project overview --status      # new, identical output
```

That recovers 7 of the 67 slots in the *help listing* while removing nothing.

## 4. Output and exit-code semantics — the fifth layer

R-2 describes four CLI layers (Contract, UX, UI, Documentation). Atlas needs a fifth:
**epistemic status of the answer.** A conventional CLI has two outcomes; Atlas has more,
and conflating them is a truth-boundary failure.

| Outcome | Exit | Rationale |
|---|:--:|---|
| Answered, sourced | `0` | success |
| **`UNKNOWN`** | **`0`** | **Honestly reporting "no traceable source" is a correct answer, not a failure.** `UNKNOWN` must never be exit `1`, or scripts will treat honesty as breakage and Atlas's core invariant becomes a CI error. |
| `CONTESTED` | `0` | reporting a conflict is a correct answer |
| `STALE` | `0` | correctly reported, with the validity window |
| `OWNER_REQUIRED` | `0` | correctly reported; the CLI did its job by declining |
| Operational error | `1` | vault unreadable, IO failure |
| Usage error | `2` | argparse (existing convention) |

This preserves the documented `0/1/2` contract in `CLAUDE.md` exactly. The epistemic state
is carried in the **output**, never in the exit code — machine consumers read `--json`.

## 5. Truth states in the terminal

Glyph + label per `05`, honouring `NO_COLOR`:

```
$ atlas project unknown --project atlas
? UNKNOWN     deployment target          no traceable source
⇄ CONTESTED   minimum python version     2 competing sources
                pyproject.toml    3.11
                docs/plan.md      3.12
              resolve: atlas review decide --project atlas --claim minimum-python
⧗ STALE       index freshness            valid until 2026-08-30

3 open questions · 0 resolved by this command · UNKNOWN stays UNKNOWN
```

Requirements:

- **Never colour-only.** Glyph and label always print; colour is added when the stream is
  a TTY and `NO_COLOR` is unset.
- **Contested prints every side.** No winner is selected for display, and the resolution
  command is printed with the claim pre-filled.
- **A count line closes the output**, and states plainly that the command resolved nothing —
  reading is not deciding.

## 6. Help and errors

**Progressive disclosure**, mirroring R-5's three tiers:

1. `atlas --help` — six areas + four top-level commands, one line each.
2. `atlas <area> --help` — that area's commands.
3. `atlas <area> <cmd> --help` — full flags, with a worked example.

**Errors name the next action.** Every error ends with a runnable command:

```
error: vault not found at ./vault
  Atlas needs an initialised vault to read.
  next: atlas init --output ./vault
```

This is the one place the CLI should spend words: R-2 lists error quality as core UX, and
the audit found the web layer already does this well (`useReadStatus` fail-closed message
names `scripts/windows/atlas-start.ps1`). The CLI should match that standard.

## 7. Acceptance criteria for `AX-008`

1. `atlas --help` lists ≤ 12 entries.
2. All 67 legacy command names still execute with byte-identical behaviour — verified by a
   test that invokes each legacy name and its area equivalent and diffs the output.
3. `add_argument_group` (or subparser areas) count > 0.
4. `atlas project unknown` on a vault with unknowns exits `0`.
5. Every truth state renders glyph + label with `NO_COLOR=1`.
6. No handler is duplicated between a legacy and an area path.

## 8. Explicitly not specified here

- Splitting `cli.py` (5,855 lines) into modules. Real technical debt, but a refactor with
  a different risk profile that must not ride along with a UX change. Backlog separately.
- Interactive prompts. Atlas's CLI is used by agents and CI; it stays non-interactive.
- Any change to write-path behaviour. This spec touches organisation and output only.
