# Technical Demo — Quickstart (Windows-first)

> **DEMO** · **NOT AUTHENTIC PILOT** · **NOT RELEASE EVIDENCE**
>
> `AS-DEMO-2.1-001` · `TECHNICAL_PREVIEW`

## Prerequisites

- Windows 10/11, PowerShell 7+ preferred (Windows PowerShell 5.1 acceptable)
- Python 3.12+ on `PATH`
- Node.js 20+ and npm (for Web shell)
- Git (clean clone / dedicated worktree)

## 0. Clean clone (required for demo certification)

```powershell
git clone https://github.com/WezzSide/project-atlas.git
cd project-atlas
git checkout feat/as-demo-2.1-001   # or main after merge
```

Do **not** rely on hidden developer vaults, orphan caches, or machine-local
estate roots for Mode A.

## 1. Backend install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
atlas version
```

## 2. Initialize demo vault from DEMO_FIXTURE

Normative corpus (discover root):

```text
tests\fixtures\demo\estate\
```

Outline via script:

```powershell
.\scripts\demo.ps1 -InitVault
```

Manual equivalent:

```powershell
$DemoRoot = Join-Path $PWD "tests\fixtures\demo\estate"
$Vault    = Join-Path $PWD ".tmp\demo-vault"
$Manifest = Join-Path $PWD ".tmp\demo-manifest.json"

atlas init --output $Vault
# AS-DEMO-2.2-RECOVERY-ID-001: init mints .atlas/vault.json automatically
# (default vault_id=atlas-main). No manual identity repair. Snapshot-ready.
atlas discover --source $DemoRoot --output $Manifest
atlas ingest --manifest $Manifest --vault $Vault --source $DemoRoot
atlas build-indexes --vault $Vault
atlas build-portfolio --vault $Vault
atlas validate --vault $Vault
```

Set Mode A:

```powershell
$env:ATLAS_DEMO_MODE = "fixture"
$env:ATLAS_DEMO_FIXTURE = (Resolve-Path "tests\fixtures\demo\estate").Path
# Never set AUTHENTIC_ESTATE_ROOT from this corpus.
```

## 3. Start live API (local bind)

```powershell
# Host/port must match current CLI; adjust if docs drift.
atlas live api-serve --vault .tmp\demo-vault --host 127.0.0.1 --port 8765
```

Smoke (separate shell) — exercise **implemented** routes only:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/v1/health
Invoke-RestMethod http://127.0.0.1:8765/v1/projects
# Add /v1/knowledge, /v1/graph, /v1/ask, /v1/mission, ...
# only if present on this tip — do not invent success routes.
```

## 4. Start Web shell

```powershell
cd apps\web
npm install
$env:VITE_ATLAS_API_BASE = "http://127.0.0.1:8765"
$env:VITE_ATLAS_DEMO_ONLY = "1"   # stamps DEMO / not pilot
npm run dev
```

Open the URL Vite prints. Lens/data source must remain demo/fixture —
never report authentic pilot rows from this corpus.

## 5. Follow the story

Operator steps: [DEMO-SCRIPT.md](./DEMO-SCRIPT.md).

## 6. Quality gates (before claiming TECHNICAL DEMO — VERIFIED)

Backend (repo-approved):

```powershell
python -m ruff check .
python -m mypy src
python -m pytest
```

Frontend (from `apps\web`):

```powershell
npm run typecheck   # or package.json equivalent
npm run build
npm test            # if present
```

Capture **live** pass/fail/skip counts — do not quote historical numbers.

## Mode B (optional, later)

```powershell
$env:ATLAS_DEMO_MODE = "live"
$env:ATLAS_DEMO_ROOT = "<legitimate project root>"
```

Rules:

- Root must already be a legitimate Atlas/project tree.
- **Forbidden:** creating `.atlas-project.yaml` solely to fake authentic pilot.
- Mode B success ≠ `AUTHENTIC_ESTATE_ROOT` release wake unless pilot rules pass independently.
