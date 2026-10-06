# AS-DEMO-2.1-001 - Windows Quickstart

**Package:** AS-DEMO-2.1-001 / TECHNICAL_PREVIEW / NON_RELEASE_CERTIFICATION  
**Audience:** Windows operators (PowerShell 5.1+)  
**Launcher ownership:** `docs/demo/scripts/demo-up.ps1`, `docs/demo/scripts/demo-down.ps1`

## Honest status (read first)

| Claim | Value |
|---|---|
| Mode | **DEMO_FIXTURE** (`ATLAS_DEMO_MODE=fixture`) |
| Certificate target | `TECHNICAL DEMO - VERIFIED` (after gates elsewhere) |
| RELEASE CERTIFIED | **NO** - launcher refuses this claim |
| AUTHENTIC PILOT PASS | **NO** - PILOT stays `DORMANT` / `DORMANT_BLOCKED` |
| Demo success | **not** v2.1.0 release evidence |

Banner text printed by the launcher always includes:

- `TECHNICAL DEMO`
- `NOT RELEASE CERTIFIED`
- `NOT AUTHENTIC PILOT PASS`
- `DEMO_FIXTURE only` (not authentic estate, not release evidence)
- `PILOT DORMANT`

Charter / mode strings: `docs/demo/AS-DEMO-2.1-001.md`, `docs/demo/MODE-BANNER.md`, `docs/demo/QUICKSTART.md`.

## DEMO_FIXTURE paths only

The Windows launcher allowlists:

| Path | Role |
|---|---|
| `tests/fixtures/demo/estate/` | Normative DEMO_FIXTURE discover root (Mode A) |
| `fixtures/demo/estate/` | Alternate hero DEMO_FIXTURE pack (when present) |
| `apps/web/public/` | Web DEMO / FIXTURE stubs (`sample-*.json`, `sample-*.fixture.json`) |
| `.tmp/as-demo-2.1-001/` | Disposable runtime (vault, PID state, logs) - gitignored |

Refused:

- `AUTHENTIC_ESTATE_ROOT` set in the environment
- `ATLAS_DEMO_ROOT` pointing outside the allowlist
- Any attempt to stamp RELEASE CERTIFIED or PILOT PASS via launcher env

Outline-only helper (no process supervision): `scripts/demo.ps1` (`-InitVault` / `-SmokeApi`).

## Prerequisites

1. Clean clone / worktree of `WezzSide/project-atlas`
2. PowerShell 5.1+ (Windows)
3. Node.js + `npm` (for `apps/web`)
4. Optional for `-WithApi`: Python 3.12+ and `pip install -e ".[dev]"` so `atlas` is on PATH
5. Normative corpus present: `tests/fixtures/demo/estate/` (required for `-WithApi`)

## One-command demo (web DEMO stubs)

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File docs\demo\scripts\demo-up.ps1
```

What it does:

1. Prints the honest TECHNICAL DEMO banner
2. Sets `ATLAS_DEMO_MODE=fixture` and `VITE_ATLAS_DEMO_ONLY=1`
3. Starts Vite against `apps/web` using DEMO/FIXTURE public stubs only
4. Writes process state under `.tmp/as-demo-2.1-001/state/demo-pids.json`

Open (after Vite is up):

- `http://127.0.0.1:5173/#/mission-control?mode=demo`
- `http://127.0.0.1:5173/#/workspace?mode=fixture`

UI chips must read DEMO / FIXTURE - never as authentic estate LIVE.

## Optional API against DEMO vault

When `tests/fixtures/demo/estate/` exists:

```powershell
powershell -ExecutionPolicy Bypass -File docs\demo\scripts\demo-up.ps1 -WithApi
```

This runs `atlas init` / `discover` / `ingest` / `build-indexes` / `validate` into
`.tmp/as-demo-2.1-001/vault` from **DEMO_FIXTURE sources only**, then
`atlas live api-serve` on `127.0.0.1:8765`.

`atlas init` automatically establishes `.atlas/vault.json` (AS-DEMO-2.2-RECOVERY-ID-001)
so the disposable vault is recovery-capable — no manual identity repair.

Transport may be LIVE_API, but the vault content remains DEMO_FIXTURE-derived -
still **NOT** authentic pilot and **NOT** RELEASE CERTIFIED. Pilot remains **DORMANT**.

## Stop

```powershell
powershell -ExecutionPolicy Bypass -File docs\demo\scripts\demo-down.ps1
```

Keep logs/vault:

```powershell
powershell -ExecutionPolicy Bypass -File docs\demo\scripts\demo-down.ps1 -KeepRuntime
```

## Troubleshooting

| Symptom | Check |
|---|---|
| Refused AUTHENTIC_ESTATE_ROOT | Unset the env var; this launcher is Mode A only |
| Refused ATLAS_DEMO_ROOT | Point only at allowlisted DEMO_FIXTURE paths |
| `-WithApi` refused | Ensure `tests/fixtures/demo/estate/` exists and `atlas` is on PATH |
| Port in use | `demo-up.ps1 -Port 5174` or stop the other process |
| npm missing | Install Node.js LTS for Windows |

## Related docs

| Doc | Notes |
|---|---|
| `docs/demo/QUICKSTART.md` | Broader Windows-first operator quickstart |
| `docs/demo/MODE-BANNER.md` | Normative honesty banner strings |
| `docs/demo/DEMO-SCRIPT.md` | Operator narrative |
| `scripts/demo.ps1` | Outline helper (`-InitVault` / `-SmokeApi`) |

## Non-goals

- No `atlas demo` Core CLI mutation in this PR
- No authentic `.atlas-project.yaml` planted in real projects
- No RELEASE CERTIFIED or PILOT PASS certificate from this launcher
