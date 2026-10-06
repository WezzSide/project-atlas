# Selecting the first real program — and why there is not one yet

The instruction was to prepare a bounded program for **the next authorized
Atlas work**, after inspecting current ownership, and to report honestly if no
unblocked authorized task exists rather than inventing filler.

**No unblocked, authorized, unowned implementation task exists today.** The
ready-to-use template is `programs/first-program-TEMPLATE.json`.

## What was inspected

* `docs/backlog.md` — **26 unchecked** items against 420 checked
* All open issues (16)
* All open PRs (~60), by the files each one changes
* All 119 local worktrees, for uncommitted edits

## The 26 unchecked backlog items

| Category | Items | Why not dispatchable |
| --- | --- | --- |
| Owner merge gates | ORCH001E-009, ORCHAUT-013, ORCHAUT-020, ORCHLEASE-007, WEB003-006, MDA-R1-007 | Not implementation work. An owner gate is granted outside this system or not at all |
| Externally blocked | ORCH001D-012 (Cursor account usage limit), MDA-R1-005 (host), AT3-046 (credentials / history API) | The blocker is an account or a host, not engineering |
| Owned by an open PR | D-PHASE2A-2 (#654), F11/F12 (awaiting IV), F14 (#779), F16 (#768), F17 (#778) | Dispatching a worker at these would collide with a live lane |
| Gated behind a state flag | AT3-003/014 (`FULL_LIVE_DEMO_READY = YES`), AS-OBSIDIAN-CAPTURE-002 (LIVE_API is contractually read-only) | The precondition is not met |
| Frozen surface, owner-gated | F5-B / #759 (`ingestion.py`, needs a sha256-pinned owner exception) | Explicitly "not runnable by this lane" |
| Roadmap horizon | Chronicle / Ambient Knowledge, AS-OBSIDIAN-CAPTURE-003, AT3-037/038/057/058 | Unscoped feature work, not a bounded task |

## The three small unowned issues, and why each was rejected

**#774 — ruff's `include` silently skips `scripts/`.** Genuinely useful and
small. **Rejected on ownership:** `pyproject.toml` is changed by **33 open
PRs** and has a live uncommitted edit in
`project-atlas-worktrees/improvement-plane-historical-eval`. Dispatching a
worker there would collide with a third of the open work.

**#767 — `read_primary_lock_pid()` crashes on valid-JSON-non-object.** In
`orchestration/sdk/host.py`. **Rejected on ownership:** PR #780 is actively
hardening that same file, and this package imports it — a worker changing it
under both would be racing two lanes at once.

**#755 — F5's sealed suite list names two files that do not exist.** A
two-word docs fix, but it lives at `WORKLOG.md:13586` **inside a sealed record's
byte-identical prefix that every subsequent seal relies on** to prove no sealed
record was rewritten. Editing it invalidates other packages' seal proofs. The
issue itself says it was deliberately not fixed in F6 for this reason.
**Rejected: this needs an owner decision, not a dispatched worker.**

## What this is not

Not a claim that Atlas has no work. It has a great deal. It is a claim that
**work suitable for a first autonomous supervised program** — bounded, with a
machine-checkable acceptance condition, in a file nobody else is holding, and
already authorized — is not currently on the board.

That is a healthy state, not a broken one: nearly everything unchecked is
either waiting on a person by design or already has an owner.

## When one does appear, it should look like this

| Property | Why |
| --- | --- |
| One or two tasks | The first real program should be small enough to read in full |
| A `COMMAND` acceptance check that fails before the work and passes after | Otherwise `CERTIFIED` means "a worker exited 0" |
| `mutation_paths` in a file no open PR touches | Re-check at dispatch time; ownership moves |
| `max_concurrent_workers: 1` | Sequential is the proven path |
| A disposable workspace | Until the program has been through once |
| No owner gate | A gated task is never dispatched autonomously |

Re-run the ownership check before dispatching anything — this report is a
snapshot, and the answer changes as PRs merge.
