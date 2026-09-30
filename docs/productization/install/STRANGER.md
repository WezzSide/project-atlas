# STRANGER path — start Atlas on Windows

> **PRODUCTIZATION / NOT RELEASE / NOT PILOT**
> Audience: **STRANGER** (first local start). Claim when healthy: `STRANGER_CAN_START_ATLAS`.
> This is not MSI install, not winget, not release certification, not pilot pass.

## Goal

One primary action → preflight → configure → start Core/API → start Web → health → open Atlas.

**TIME_TO_FIRST_VALUE** target: **≤ 15 minutes** on a machine that already has Python 3.12+ and Node.js LTS.

## Prerequisites (before the one action)

1. Git clone of `WezzSide/project-atlas`
2. Python **3.12+** on PATH (`py -3.12` or `python`)
3. Node.js LTS + **npm** on PATH
4. Ability to write under the repo `.tmp` directory

## One action

From the repository root in PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\atlas-start.ps1
```

What it does:

1. Prints the honesty banner (PRODUCTIZATION / NOT RELEASE / NOT PILOT)
2. Runs `atlas-preflight.ps1` (Python, Node/npm, repo root, writable `.tmp`)
3. Runs editable install if needed: `pip install -e ".[dev]"`
4. Configures a vault:
   - Prefer **DEMO_FIXTURE** bootstrap into `.tmp/productization/vault` when fixture estate exists
   - Otherwise prompts for an **existing** vault path (never invents authentic estate)
5. Starts `atlas live api-serve` on `127.0.0.1` (bounded / loopback only)
6. Starts `apps/web` via `npm run dev` (does **not** add Playwright)
7. Health-checks API `/v1/meta` and the web port
8. Opens the local Atlas URL in your default browser

Stop later:

```powershell
powershell -NoProfile -File scripts\windows\atlas-stop.ps1
```

## Preflight only

```powershell
powershell -NoProfile -File scripts\windows\atlas-preflight.ps1
```

## If something fails

Read the **WHAT / CAUSE / ACTION / RETRY** block on stderr (also under `.tmp/productization/logs/` when present). Fix the ACTION, then run the RETRY command.

Common fixes:

| Symptom | Action |
|---|---|
| Python missing | Install Python 3.12+ and reopen the terminal |
| npm missing | Install Node.js LTS |
| pip install fails | Fix network/proxy; retry `python -m pip install -e ".[dev]"` |
| DEMO_FIXTURE missing | Pass `-Vault <existing-vault>` or restore `tests/fixtures/demo/estate` |
| Port in use | `atlas-stop.ps1`, or pass `-ApiPort` / `-WebPort` |

## Honesty

- **NOT RELEASE** — no release certificate from this path
- **NOT PILOT** — DEMO_FIXTURE / local vault ≠ authentic estate pilot
- No MSI / winget / signing yet
- Prefer `.tmp/productization/` runtime paths
