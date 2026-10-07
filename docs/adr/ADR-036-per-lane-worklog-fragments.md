# ADR-036: Per-lane worklog fragments (WORKLOG.md as a rendered view)

- Status: PROPOSED (2026-09-09) — owner decision required; nothing in this ADR changes behaviour until accepted
- Deciders: repository owner
- Related: AGENTS.md ("append to `WORKLOG.md` as work packages complete"), ADR-006 (repository governance baseline), ADR-035 (Studio governed action intent)

## Context

`WORKLOG.md` is the single, append-only execution log every work package
writes to. Measured on `origin/main` at `b87b4a22` (2026-09-09):

| Measure | Value |
|---|---|
| Size | 14,917 lines / 786 KB |
| Commits touching it since 2026-08-25 | 217 |
| Commits whose message mentions it | 170 |
| Known NUL byte inside it | 1 (classified as a repository residual, `6dcb8bbd`) |

Because every lane appends to the same tail, **every parallel lane conflicts
with every other lane at integration time, in this file and only in this
file**. Dry-run merges of the Atlas Studio stack onto `origin/main` on
2026-09-09 show the shape exactly: the coordination tip
(`feat/atlas-dag-e2e-harden`) merges CLEAN; A0 (#763), A1 (#770) and A2
(#776) each CONFLICT in `WORKLOG.md` alone. Sibling lanes on the same base
(#776 and #781) overlap in `WORKLOG.md` alone.

The conflicts are always resolved by hand-concatenating both tails, which:

1. costs a human or an agent a merge step per lane per integration;
2. is the one place where a merge can silently drop or duplicate a
   package's evidence entry (the claim record drifting from the code —
   already observed in this repository's seal-claim-record lessons);
3. makes `WORKLOG.md` the *only* file in the tree whose content depends
   on merge order rather than on the work itself, defeating the
   deterministic-output principle (NFR-001) for the evidence record;
4. concentrates the repository's entire execution history in one file
   that `grep` wrappers already misclassify as binary.

The pattern is a classic shared-append hotspot. The fix is the standard
one: each writer owns its own file; the aggregate is a *derived view*.

## Decision (proposed)

1. **Fragments are the source of truth.** Each work package or lane
   writes its log to `docs/worklog/<PACKAGE-ID>.md` (for example
   `docs/worklog/AS-STUDIO-A2-001.md`). A fragment has a small front
   matter (`package`, `lane`, `status`, `first_commit`) and the same
   free-form body WORKLOG entries have today. Two lanes never write the
   same fragment; a lane that touches several packages writes several.
2. **`WORKLOG.md` becomes a rendered view, not a hand-edited file.** A
   deterministic script (`scripts/worklog-render.py`, stdlib only)
   concatenates fragments in a stable order (package id, then
   `first_commit` topological order) into `WORKLOG.md`. Rendering happens
   on `main` after merge (or in CI as a drift check), never on a lane
   branch — so lane branches stop touching `WORKLOG.md` at all.
3. **Drift check, fail-closed.** CI asserts `WORKLOG.md` equals the
   rendered output of the fragments on `main`; on lane branches CI
   asserts `WORKLOG.md` is *unchanged* relative to the merge base, so a
   lane cannot reintroduce the hotspot.
4. **History is preserved.** The existing `WORKLOG.md` content up to the
   cut-over commit is frozen into `docs/worklog/000-HISTORICAL.md`
   (verbatim, NUL byte and all, as a residual) and rendered first.
5. **AGENTS.md / CLAUDE.md** are updated in the same change to say
   "append to your package fragment under `docs/worklog/`", and
   `atlas-dag`/Studio evidence generators that cite `WORKLOG.md` cite the
   fragment path instead.

## Consequences

- Integration of stacked or sibling lanes stops conflicting on evidence.
  The 2026-09-09 Studio stack would have merged CLEAN at every level.
- Evidence entries become attributable to exactly one lane and one
  package, which is what the seal / claim-record process already assumes.
- A rendered `WORKLOG.md` is reproducible from the tree alone, restoring
  NFR-001 for the execution record.
- Cost: one small script, one CI step, a documentation change, and a
  one-time cut-over commit on `main`. Lanes in flight at cut-over keep
  their existing `WORKLOG.md` tails; the cut-over merge moves those tails
  into fragments once.
- Not in scope: rewriting history, changing `docs/backlog.md`, or
  touching the control-plane sibling deliverable.

## Alternatives considered

- **Keep the single file and use `git merge` union driver** — hides
  ordering nondeterminism inside `.gitattributes`, still allows silent
  duplication, and does not help agents that resolve conflicts by hand.
- **Log only in PR descriptions** — evidence would live outside the tree
  and outside `atlas discover`, contradicting the docs-as-spec principle.
- **Per-day files** — reintroduces the hotspot whenever two lanes work
  on the same day, which is the normal case.

## Truth boundaries

`PROPOSED != ACCEPTED`. This ADR does not move any file, add any script,
or change any gate. If accepted, the cut-over is its own work package with
its own evidence, exact-head CI, and independent verification.
