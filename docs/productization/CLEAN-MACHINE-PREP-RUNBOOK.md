# Clean-machine certification runbook (pinned)

> **RELEASE / CLEAN-MACHINE** — reproduces D-144 O3 acceptance from a genuinely clean state.
> Bound to integrated `main` at certification time. Update `TARGET_HEAD` when main moves.

## Prerequisites

| Requirement | Notes |
|---|---|
| Git | clone `https://github.com/WezzSide/project-atlas` |
| Python | 3.12+ (`py -3.12` or `python`) |
| Network | `pip install` and optional `gh` for CI verification |
| Writable temp | e.g. `%TEMP%` or `/tmp` for disposable clone workspace |
| No prior Atlas state | Do not reuse developer venvs, editable installs, or `.atlas` runtime dirs |

**Do not use:** existing `project-atlas-vault` venv, broker worktrees, or cached `pip` wheels from prior editable installs when certifying clean-install semantics.

## Clone command

```powershell
$TARGET_HEAD = "2b8ebab468d4b9e9ce119a86b5a5350de3df96b3"
$WORK = Join-Path $env:TEMP "atlas-clean-machine-cert"
Remove-Item -Recurse -Force $WORK -ErrorAction SilentlyContinue
git clone --depth 1 https://github.com/WezzSide/project-atlas.git $WORK
cd $WORK
git fetch --depth 1 origin $TARGET_HEAD
git checkout $TARGET_HEAD
```

## Checkout / SHA verification

```powershell
git rev-parse HEAD
git rev-parse 'HEAD^{tree}'
```

Expected at D-144 certification:

- `HEAD` = `2b8ebab468d4b9e9ce119a86b5a5350de3df96b3`
- `TREE` = `817df2a42ae6f47d08510147daab5ba565471517`

Fail closed if either differs.

## Install commands

```powershell
py -3.12 -m venv .venv-clean
.\.venv-clean\Scripts\python.exe -m pip install --upgrade pip
.\.venv-clean\Scripts\python.exe -m pip install -e ".[dev]"
```

Alternative (no editable install — PYTHONPATH smoke only):

```powershell
$env:PYTHONPATH = "$(Resolve-Path src)"
```

## Configuration

No secrets required. Acceptance uses repository demo estate fixtures under `tests/fixtures/demo/estate/`.

Do **not** set `AUTHENTIC_ESTATE_ROOT` from demo fixtures.

## Authentic pilot setup

Copy harbor acceptance project to disposable work root:

```powershell
$PILOT = Join-Path $env:TEMP "atlas-pilot-work"
Remove-Item -Recurse -Force $PILOT -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $PILOT | Out-Null
Copy-Item -Recurse tests\fixtures\demo\estate\harbor-api $PILOT\harbor-api
```

## Acceptance commands

From repo root with venv activated:

```powershell
$PY = Resolve-Path .\.venv-clean\Scripts\python.exe

# CLI smoke
& $PY -m project_atlas.cli version

# Core integration slice
& $PY -m pytest tests/integration/test_core_vertical_slice.py -q

# Golden demo estate journey (discover → ingest → indexes → validate → query)
& $PY -m pytest tests/integration/test_as_demo_2_2_golden_fixture.py -q

# ADV clean-clone matrix case
& $PY -m project_atlas.cli adv certify --work-root (Join-Path $env:TEMP "atlas-adv-work") --json

# Post-stack regression boundaries
& $PY -m pytest tests/unit/test_as_coder_alpha_demo_readiness_001.py -q
& $PY -m pytest tests/unit/test_merge_sequence_gate_d138.py -q
& $PY -m pytest tests/unit/test_ci_observer_watch_disposition_d144.py -q

# Integrated IV helpers
& $PY -m pytest tests/unit/test_as_orch_self_wake_resident_driver_001.py -q
```

Or run the bundled helper:

```powershell
& $PY docs\scripts\d144_certification_runner.py --lane clean-machine-only
```

## Expected results

| Step | Expected |
|---|---|
| `version` | exit 0 |
| `test_core_vertical_slice` | all pass |
| `test_as_demo_2_2_golden_fixture` | all pass |
| `adv certify` | `status: certified`, `clean_clone_replay: pass` |
| `demo_readiness` | `inbox_list: READY`, `authentic_pilot: false` (honest) |
| merge-sequence gate tests | 18 pass |
| watch disposition tests | 4 pass |

## Failure diagnostics

| Symptom | Likely cause | Action |
|---|---|---|
| HEAD mismatch | wrong branch / shallow fetch | re-fetch exact `TARGET_HEAD` |
| pip install fails | network/proxy | fix pip index; retry install |
| ingest fail-closed | source marker drift | re-copy fixture; rediscover |
| adv certify fail | disposable root inside repo | use temp work-root outside clone |
| demo_readiness inbox NOT_IMPLEMENTED | PR438 fix missing | verify `0ab1585` in ancestry |

## Cleanup

```powershell
Remove-Item -Recurse -Force $WORK, $PILOT -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $env:TEMP "atlas-adv-work") -ErrorAction SilentlyContinue
```

## Receipt

Machine-readable output: `.atlas/orchestration/sdk-runtime/d144-clean-machine-receipt.json` (written by `d144_certification_runner.py` when run from a broker/root worktree).
