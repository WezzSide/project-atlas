# First-run checklist — AS-PROD-ONBOARD-001

> **PRODUCTIZATION / NOT RELEASE / NOT PILOT** · `ALPHA_READY = NO`  
> Tick items in order. Do not invent doctor or Playwright steps as substitutes.

## Before you start

- [ ] Read [HONESTY.md](./HONESTY.md)
- [ ] Cloned `WezzSide/project-atlas` and opened a PowerShell at repo root
- [ ] Python 3.12+ available (`py -3.12` or `python`)
- [ ] Node.js LTS + npm on PATH
- [ ] Understand DEMO_FIXTURE / `.tmp/productization/` ≠ authentic estate pilot

## Install link

- [ ] Skim [`../install/STRANGER.md`](../install/STRANGER.md) (or OPERATOR guide if using `-Vault`)
- [ ] Accept that MSI / winget / signing are out of scope

## Preflight → start

- [ ] Run onboard helper **or** manual preflight + start:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\atlas-onboard.ps1
```

- [ ] Preflight reports Python / npm / repo / writable `.tmp` OK
- [ ] API health: `http://127.0.0.1:<ApiPort>/v1/meta`
- [ ] Web responds on `http://127.0.0.1:<WebPort>/` (unless `-SkipWeb`)
- [ ] Browser opened **or** you intentionally passed `-SkipBrowser`

## Doctor (future — do not fake)

- [ ] Noted that Core `atlas doctor` is **not** part of this package (`#254`)
- [ ] Did **not** claim doctor PASS / FAIL from this run
- [ ] Did **not** install or run Playwright as an onboard gate (`#253`)

## Honesty closeout

- [ ] Did **not** set or claim `ALPHA_READY` as ready
- [ ] Did **not** claim release certification or pilot pass
- [ ] Local start claim (if healthy) stays under install wording: `STRANGER_CAN_START_ATLAS`
- [ ] Stopped runtimes when finished: `scripts\windows\atlas-stop.ps1`

## Stop

```powershell
powershell -NoProfile -File scripts\windows\atlas-stop.ps1
```
