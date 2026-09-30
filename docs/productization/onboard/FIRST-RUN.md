# FIRST-RUN — stranger path after install

> **PRODUCTIZATION / NOT RELEASE / NOT PILOT**  
> Read [HONESTY.md](./HONESTY.md) first. Claim target after a healthy start remains
> `STRANGER_CAN_START_ATLAS` (local, install-owned). This doc does **not** certify
> release, pilot, or `ALPHA_READY`.

## Purpose

Give a stranger a single narrative that **links**:

1. Install productization docs / scripts (`AS-PROD-INSTALL-001`)
2. Preflight (`scripts/windows/atlas-preflight.ps1`)
3. Start (`scripts/windows/atlas-start.ps1`)
4. **(Future)** Core `atlas doctor` — owned by Cloud `#254` / PROD-DOCTOR; **not implemented here**

## Prerequisites

Same as install stranger path:

1. Git clone of `WezzSide/project-atlas`
2. Python **3.12+** on PATH
3. Node.js LTS + **npm** on PATH
4. Writable repo `.tmp` directory

Full install detail: [`../install/STRANGER.md`](../install/STRANGER.md).

## Recommended journey

### A. Optional one-action onboard helper

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\atlas-onboard.ps1
```

What it does:

1. Prints the onboard honesty banner (PRODUCTIZATION / NOT RELEASE / NOT PILOT)
2. Runs `atlas-preflight.ps1` (explicit chain step)
3. Invokes `atlas-start.ps1` (editable install if needed → vault → API → web → health → browser)
4. Prints a **future doctor** note — does **not** invoke `atlas doctor` or any doctor CLI

Useful flags (forwarded to start):

```powershell
powershell -NoProfile -File scripts\windows\atlas-onboard.ps1 -UseDemoFixture -NonInteractive -SkipBrowser
powershell -NoProfile -File scripts\windows\atlas-onboard.ps1 -Vault D:\path\to\vault
```

### B. Manual chain (same outcome)

```powershell
# 1) Preflight only
powershell -NoProfile -File scripts\windows\atlas-preflight.ps1

# 2) Start (also re-runs preflight internally)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\atlas-start.ps1

# 3) Stop when done
powershell -NoProfile -File scripts\windows\atlas-stop.ps1
```

### C. Future doctor (slot reserved)

When Core doctor lands (`#254`):

```text
# PLACEHOLDER — do not run until doctor is on main
# atlas doctor ...
```

Until then:

- Do **not** pretend doctor exists in Core CLI from this package
- Do **not** wire Playwright smoke as a substitute for doctor
- Treat “first-run green” as **preflight + start health only**

## Success / honesty

| Outcome | Meaning |
|---|---|
| Preflight OK + API `/v1/meta` + web health | Local `STRANGER_CAN_START_ATLAS` path (install claim) |
| DEMO_FIXTURE under `.tmp/productization/` | Disposable runtime — **NOT PILOT** |
| Doctor note printed | Informational only — doctor owned elsewhere |
| `ALPHA_READY=YES` | **Forbidden** from this package |

## If something fails

Use the structured product error block from install scripts:

```text
WHAT:   ...
CAUSE:  ...
ACTION: ...
RETRY:  ...
```

Then open [`../install/STRANGER.md`](../install/STRANGER.md) and [`../install/LIMITATIONS.md`](../install/LIMITATIONS.md).

## Checklist

Use [CHECKLIST.md](./CHECKLIST.md) while walking the journey.
