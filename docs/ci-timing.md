# CI timing — Windows 19m vs Linux 12m → plan to ≤10m

Proposal only. This note does not change `.github/workflows/ci.yml`.
There is no workflow `repository:` owner gate to retarget; the `ci`
workflow already runs on every PR (`.github/workflows/ci.yml:3-6`).

Target: Windows quality wall clock **≤ 10 minutes** so per-iteration
verification is not why autonomy is throttled.

## Measured wall clock

Source: successful `ci` run
[`35018874179`](https://github.com/WezzSide/project-atlas/actions/runs/35018874179)
on 2026-09-15 (`autonomy/scaffold` PR, same workflow as `main`).

| Job | Start → finish | Wall clock |
| --- | --- | --- |
| `quality (ubuntu-latest, 3.12, full)` | 20:19:39Z → 20:31:52Z | **12m 13s** |
| `quality (windows-latest, 3.12, windows)` | 20:19:39Z → 20:39:08Z | **19m 29s** |
| `quality (ubuntu-latest, 3.13, compat)` | 20:19:39Z → 20:26:05Z | 6m 26s (not critical path) |
| `control-plane` | 20:19:39Z → 20:20:33Z | 54s |

Critical path is the Windows quality job.

## Where the Windows minutes go

| Step | File:line | Duration in `35018874179` | Notes |
| --- | --- | --- | --- |
| Install package | `ci.yml:41-42` | Windows 25s / Linux 9s | Hosted-runner + pip cache |
| Ruff + mypy | `ci.yml:44-50` | Linux ~11s / Windows skipped | Linux-only |
| Pytest with coverage (Linux full) | `ci.yml:52-54` | **11m 41s** | Single invocation; inherits `--cov` |
| Pytest product-perf (Windows) | `ci.yml:66-71` | **33s** | Correctly uses `--no-cov` |
| Pytest (Windows, not product_perf) | `ci.yml:73-75` | **16m 59s** | Inherits `--cov` from addopts |
| CLI smoke | `ci.yml:77-86` | Windows 5s / Linux 3s | Negligible |
| Golden fixture re-run (Windows) | `ci.yml:88-90` | **66s** | Same test already inside the main Windows pytest |

`pyproject.toml:45` sets global addopts
`-q --cov=project_atlas --cov-report=term-missing --cov-report=xml --no-cov-on-fail`.
The Windows product-perf step already documents that coverage
instrumentation is too expensive on hosted Windows
(`ci.yml:68-70`). The following full-suite step does not pass
`--no-cov`.

Suite shape on this tree: **478** `tests/unit/test_*.py` files vs
**39** `tests/integration/test_*.py` files. File count is not time
share. Integration (vault rebuilds, golden fixtures) is the likely
Windows hog; a 50/50 unit/integration time split must not be assumed.

Existing markers (`pyproject.toml:46-50`): `integration`,
`security_regression`, `product_perf`. There is no `unit` marker;
unit tests are `not integration`.

## Path to ≤10 minutes (do not implement here)

`--no-cov` alone is not a proven ≤10m fix. We do not have a measured
uncovered Windows full-suite duration. Linux 3.12 covered pytest is
11m 41s; Windows covered pytest is 16m 59s. Coverage overhead on
Windows is real (see `ci.yml:68-70`) but unquantified for the full
suite. Do not treat "drop coverage" as sufficient.

The plan that can hit ≤10m **even if coverage stays** is a three-way
parallel Windows matrix, because 16m 59s sequential cannot fit in
10m after install (25s) plus the extra 33s + 66s steps:

1. **Measure first (one PR, no required-check change):** add a
   diagnostic workflow or temporary steps that record
   `pytest -m "not integration and not product_perf" --no-cov`
   vs `pytest -m integration --no-cov` vs `pytest -m product_perf --no-cov`
   durations on `windows-latest`. That is the evidence the split is
   sized against, not file counts.
2. **Drop the golden re-run** (`ci.yml:88-90`, 66s) unless a clean
   process is required for CRLF isolation. The test already runs in
   the main Windows pytest.
3. **`--no-cov` on every Windows pytest step** (`ci.yml:75`). Coverage
   stays on Linux `full` (`ci.yml:52-54`). No required-check rename.
   Apply after (1) so the save is measured.
4. **If Windows integration `--no-cov` is still >10m:** shard
   integration across two parallel jobs (e.g. `tests/integration/test_as_*`
   vs the rest, or an explicit `integration_slow` marker on the golden
   / vault-rebuild tests). Unit + product_perf + each integration shard
   must each be ≤10m including install. This **does** change the
   required-check name `quality (windows-latest, 3.12, windows)`
   (`ci.yml:26-28`) and needs an explicit branch-protection update.
5. **Do not split Linux `full`** until coverage is collected in one
   process or merged from shards.

Numeric claim that is actually supported today: a single sequential
Windows job cannot reach ≤10m while it still runs a 16m 59s pytest.
Parallelism (step 4) is the only plan that does not depend on an
unmeasured coverage discount.

## Remaining owner-slug residue (factory / trust)

Live clone URLs, GitHub API `CANONICAL_REPO`, and
`CANONICAL_REPO_URL` are `WezzSide/project-atlas`.

The sealed pin `CANONICAL_REPOSITORY_IDENTITY` in
`src/project_atlas/orchestration/autonomy/models.py` remains
`github.com/b0lk13/project-atlas` because it is hashed into
`INITIAL_RETARGET_EVIDENCE_DIGEST`. Rewriting it is an owner
pin-retarget, not hygiene. `repository_identities_match()` treats
that sealed identity and `LIVE_REPOSITORY_IDENTITY`
(`github.com/wezzside/project-atlas`) as one repository so a live
`origin` does not fail-closed. The `bolkdev` org is kept empty for a
future transfer; it is not the live clone path.

Alias tests still mention `B0LK13/project-atlas` on purpose: they
prove the pre-transfer slug attributes to the live owner. That is
verified equivalence, not a live clone path.

Owner pin-retarget (make the sealed identity the live owner) is the
backlog item where Atlas still asks to be trusted: the digest is
historical evidence and must be re-sealed, not silently edited.
