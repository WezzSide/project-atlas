# Merge-Gate Mechanics — what actually blocks a merge on this repository

status: observed
observed_by: ubuntu-main coordinator lane
observed_at: 2026-09-07
main_base: eadc0f62236cb4ff010b318f678b68f3e1c27dc1
scope: platform mechanics only — no production code, no policy change

This records mechanics that were mis-modelled for at least a day and produced
wasted diagnosis across PRs #694, #697 and #698. It is an observation receipt,
not a policy proposal. Every *configuration* claim below is paired with the
command that produced it. The observations of PR state in section C are a recorded
snapshot of `gh pr view` / `reviewThreads` output on the date given rather
than a command that re-runs to the same values -- those PRs have since
moved. They are cited as evidence for the mechanism, not as a fixture.

**This is a snapshot of repository configuration and GitHub behaviour on
2026-09-07. Configuration can change. Re-run the commands before relying on it.**

## A. No repository rulesets

```
$ gh api repos/WezzSide/project-atlas/rulesets
[]
```

`REPOSITORY_RULESETS = []`. No ruleset-sourced requirements exist, so the
legacy branch-protection API is the whole picture rather than half of it.

## B. Branch protection on `main`, as retrieved

```
$ gh api repos/WezzSide/project-atlas/branches/main/protection
```

| setting | value |
|---|---|
| `required_status_checks` | **key absent from the response** |
| `required_conversation_resolution.enabled` | **true** |
| `required_pull_request_reviews.required_approving_review_count` | 0 |
| `required_pull_request_reviews.dismiss_stale_reviews` | true |
| `enforce_admins.enabled` | true |
| `required_linear_history.enabled` | false |
| `allow_force_pushes.enabled` | true |
| `required_signatures.enabled` | false |

Absence of a key is weak evidence, so it was confirmed against the dedicated
endpoint, which asserts the negative directly rather than leaving it inferred:

```
$ gh api repos/WezzSide/project-atlas/branches/main/protection/required_status_checks
{"message":"Required status checks not enabled", ... "status":"404"}
```

`BRANCH_PROTECTION_READ = AVAILABLE` ·
`GITHUB_REQUIRED_STATUS_CHECKS = NONE` ·
`GITHUB_REQUIRED_CONVERSATION_RESOLUTION = YES`.

## C. Unresolved conversations are the platform blocker

`mergeStateStatus = BLOCKED` on this repository means unresolved review
threads, not failing CI. Read at **2026-09-07 ~11:20Z**, as a natural
experiment nobody set up on purpose. Several of these PRs moved within the
same day -- #706's head advanced and it left `CLEAN` about half an hour
later -- which is the point of dating the reading rather than presenting it
as a standing fact:

| PR | unresolved threads | checks | `mergeStateStatus` |
|---|---|---|---|
| #704 | 0 | failing | `UNSTABLE` — *not* blocked |
| #699 | 0 | green | `CLEAN` → merged 10:35Z |
| #706 | 0 | green | `CLEAN` |
| #700 / #703 / #705 | 4 / 2 / 3 | — | `BLOCKED` |
| #694 | 6 | Windows red | `BLOCKED` |

#704 is the load-bearing row: **failing checks with zero unresolved threads
does not block.** Every PR merged in the preceding two days (#690, #696, #699,
#702) had zero unresolved threads at the merge instant.

## D. CI is Atlas evidence, not a GitHub gate

C is a statement about GitHub, and it is easy to misread as a licence.

> `GITHUB_REQUIRED_STATUS_CHECKS = NONE` does **not** mean red CI is
> acceptable. It means GitHub will not stop you, so the judgement is entirely
> Atlas's.

The governing rule is unchanged and if anything now carries more weight:

```
UNEXPLAINED RED CI = MERGE INELIGIBLE
```

A red result may be classified as mechanically explained — see E and F — but
the classification must be recorded against exact object ancestry. "GitHub
allowed it" is not a classification.

Equally: `GREEN CI != MERGE AUTHORIZATION`. CI is one input to the Atlas
evidence gate alongside exact-head independent verification, claim integrity
and current-main compatibility.

## E. `pull_request` CI evaluates a synthetic merge ref

`.github/workflows/ci.yml` triggers on `pull_request`, so `actions/checkout`
evaluates **`refs/pull/<N>/merge`** — the PR head merged with the base — and
not the branch tree. The tree under test therefore contains base content that
matches **neither branch tip**. The base commit itself stays fetchable by
SHA, so the tree is reconstructible once you know which base was used; the
trap is that nothing in the PR view tells you, and the content can include
files absent from both current tips.

This is how #697 and #694 failed the Windows job on
`tests/unit/test_windows_no_window_creationflags_d676.py`, a file **absent from
both branch trees**. It arrived from the stale base side of the synthetic
merge.

## F. The synthetic merge ref does not refresh predictably

Thirteen hours after #702 merged, all three merge refs still carried the
pre-#702 base:

```
$ git fetch origin refs/pull/698/merge:refs/x && git rev-parse refs/x^1
4fb91bebcd0bb00a74f582879bf830eabb0c6c8d   # main was already 5d7d76d9
```

By midday the same day, #694's ref had recomputed to current main on its own,
with no push to the branch. So:

- base movement does **not** promptly or reliably recompute the ref;
- it does recompute eventually and asynchronously;
- a workflow **rerun replays the recorded synthetic object**, so it cannot
  clear a stale-ref failure;
- a recomputed ref does **not** trigger a new run — #694 ended the day with a
  *current* merge ref still displaying the check result produced by the old
  one, so a red check can outlive the object that produced it.

A candidate-head movement (`synchronize`) is the deterministic path that
produced a fresh synthetic object. Merging current main into the branch is the
non-destructive way to do that; it preserves candidate identity better than a
rebase, but it still creates a new exact object and therefore still requires
fresh exact-head verification.

## G. Read the merge ref before classifying an inherited failure

Reasoning from `PR.base.sha` is not sufficient, though not for the reason it
is tempting to give. It is **not** a creation-time pin: the value from the
live PR API follows the current base ref, while the value inside a workflow
event payload is frozen at the moment that event was emitted. Two readings
of "the base" can therefore disagree, and neither is necessarily the base
that was *tested* -- only `MERGE_REF_PARENT1` is that. Before attributing a
CI failure to a candidate, record:

```
PR_HEAD           =
PR_MERGE_REF      =
MERGE_REF_PARENT1 =    # the base actually tested
MERGE_REF_PARENT2 =    # should equal PR_HEAD
MERGE_REF_TREE    =
```

then classify `CURRENT_SYNTHETIC_MERGE` / `STALE_SYNTHETIC_MERGE` / `UNKNOWN`.

Only `STALE_SYNTHETIC_MERGE` with proven ancestry licenses the
`INHERITED_PRE_<fix>_FAILURE` classification, and it licenses it for that
object alone. **Do not generalise the classification to new failures**: the
pre-#702 Windows signature (every test passing, then `KeyboardInterrupt` at
`threading.py:355` during teardown, exit 1) is now explained, and a future
failure that merely resembles it is not thereby explained.

## Consequences for the merge gate

Three gates, satisfied independently, none substituting for another:

| gate | satisfied by |
|---|---|
| `PLATFORM_MERGE_GATE` | GitHub mergeability — here, conversation resolution |
| `PROJECT_ATLAS_EVIDENCE_GATE` | CI classified + exact-head IV + claim integrity + current-main compatibility |
| `OWNER_GATE` | explicit merge/policy authority |

```
PLATFORM MERGEABLE   != ATLAS MERGE ELIGIBLE
CI PASS              != IV PASS
THREAD RESOLUTION    != TECHNICAL REMEDIATION
PR BASE LABEL        != SYNTHETIC MERGE OBJECT
```

Because conversation resolution is a *platform* gate with no evidentiary
content, it is the one most easily satisfied dishonestly — resolving a thread
changes `mergeStateStatus` without changing anything true. Every conversation
should therefore carry a durable technical disposition (`FIXED` /
`SUPERSEDED` / `CLAIM_CORRECTED` / `NONBLOCKING_ACCEPTED` / `SUCCESSOR_BOUND` /
`NOT_APPLICABLE` / `MATERIAL_OPEN`) **before** anyone resolves it, and the
resolution should come from a reviewer or owner rather than from the lane that
wants the button unlocked.
