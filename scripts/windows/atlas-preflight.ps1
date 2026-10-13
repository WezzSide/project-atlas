#Requires -Version 5.1
<#
.SYNOPSIS
  AS-PROD-INSTALL-001 reusable Windows preflight checks.

.DESCRIPTION
  Verifies stranger bootstrap prerequisites before starting Core/API + Web:
  - Repo root (pyproject.toml + apps/web)
  - Python 3.12+
  - Node.js / npm
  - Writable .tmp (and .tmp/productization)

  Prints honest PRODUCTIZATION / NOT RELEASE / NOT PILOT status.
  On failure emits structured product errors:
    WHAT:   ...
    CAUSE:  ...
    ACTION: ...
    RETRY:  ...

.PARAMETER Json
  Emit a machine-readable JSON summary to stdout (still prints banner to host).

.PARAMETER SkipBanner
  Suppress the honesty banner (useful when called from atlas-start.ps1).

.EXAMPLE
  powershell -NoProfile -File scripts\windows\atlas-preflight.ps1

.EXAMPLE
  powershell -NoProfile -File scripts\windows\atlas-preflight.ps1 -Json
#>
[CmdletBinding()]
param(
    [switch]$Json,
    [switch]$SkipBanner
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
. (Join-Path $ScriptDir "_AtlasCommon.ps1")

if (-not $SkipBanner) {
    Write-AtlasProductBanner
}

$checks = [ordered]@{
    package_id           = "AS-PROD-INSTALL-001"
    productization       = $true
    release_certified    = $false
    pilot_pass           = $false
    not_release          = $true
    stranger_audience    = $true
    repo_root            = $null
    python               = $null
    python_ok            = $false
    node_ok              = $false
    npm_ok               = $false
    tmp_writable         = $false
    productization_tmp   = $null
    ok                   = $false
    failed_check         = $null
}

$logDir = $null
$errLog = $null

try {
    $repoRoot = Resolve-AtlasRepoRoot -ScriptDir $ScriptDir
    if (-not $repoRoot) {
        Write-AtlasProductError `
            -What "Cannot resolve Project Atlas repository root." `
            -Cause "Expected pyproject.toml and apps/web/package.json two levels above scripts/windows." `
            -Action "Clone WezzSide/project-atlas and run this script from the checkout (scripts\windows\atlas-preflight.ps1)." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1"
        $checks.failed_check = "repo_root"
        if ($Json) { $checks | ConvertTo-Json -Depth 6; exit 1 }
        exit 1
    }
    $checks.repo_root = $repoRoot

    $runtimeRoot = Join-Path $repoRoot ".tmp\productization"
    $logDir = Join-Path $runtimeRoot "logs"
    $errLog = Join-Path $logDir "preflight-errors.log"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $checks.productization_tmp = $runtimeRoot

    # --- Python 3.12+ (prefer tip-local .venv when present; ENV-ISO-002) ---
    $python = Get-AtlasPythonCommand -RepoRoot $repoRoot
    if (-not $python) {
        Write-AtlasProductError `
            -What "Python 3.12+ is required for Atlas Core." `
            -Cause "Neither tip-local .venv\Scripts\python.exe, 'py -3.12', nor python/python3 reporting 3.12+ was found." `
            -Action "Install Python 3.12+ from https://www.python.org/downloads/ (enable 'Add python.exe to PATH' / py launcher). Prefer a tip-local venv: py -3.12 -m venv .venv" `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
            -LogPath $errLog
        $checks.failed_check = "python"
        if ($Json) { $checks | ConvertTo-Json -Depth 6; exit 1 }
        exit 1
    }
    $verLine = & $python.Exe @($python.Args + @("-c", "import sys; print(sys.version.split()[0])"))
    $checks.python = @{
        label     = $python.Label
        version   = "$verLine"
        exe       = $python.Exe
        tip_local = [bool](Test-AtlasInterpreterIsTipVenv -Python $python -RepoRoot $repoRoot)
    }
    $checks.python_ok = $true
    Write-Host "OK  Python $($checks.python.version) via $($python.Label) (tip_local=$($checks.python.tip_local))"

    # --- Node / npm ---
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) {
        Write-AtlasProductError `
            -What "Node.js is required to start the Atlas web shell." `
            -Cause "Command 'node' was not found on PATH." `
            -Action "Install Node.js LTS from https://nodejs.org/ and reopen the terminal." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
            -LogPath $errLog
        $checks.failed_check = "node"
        if ($Json) { $checks | ConvertTo-Json -Depth 6; exit 1 }
        exit 1
    }
    $nodeVer = (& node --version 2>$null)
    $checks.node_ok = $true
    Write-Host "OK  Node $nodeVer"

    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) {
        Write-AtlasProductError `
            -What "npm is required to install/run apps/web." `
            -Cause "Command 'npm' / 'npm.cmd' was not found on PATH (Node install may be incomplete)." `
            -Action "Repair/reinstall Node.js LTS so npm.cmd is on PATH, then reopen the terminal." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
            -LogPath $errLog
        $checks.failed_check = "npm"
        if ($Json) { $checks | ConvertTo-Json -Depth 6; exit 1 }
        exit 1
    }
    $npmVer = (& $npm.Source --version 2>$null)
    $checks.npm_ok = $true
    Write-Host "OK  npm $npmVer"

    # --- writable .tmp ---
    $tmpRoot = Join-Path $repoRoot ".tmp"
    if (-not (Test-PathWritable -Directory $tmpRoot)) {
        Write-AtlasProductError `
            -What "Repository .tmp directory is not writable." `
            -Cause "Could not create/write a probe file under $tmpRoot." `
            -Action "Fix directory permissions or free disk space, then retry. Atlas uses .tmp/productization for disposable runtime." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
            -LogPath $errLog
        $checks.failed_check = "tmp_writable"
        if ($Json) { $checks | ConvertTo-Json -Depth 6; exit 1 }
        exit 1
    }
    if (-not (Test-PathWritable -Directory $runtimeRoot)) {
        Write-AtlasProductError `
            -What "Productization runtime path is not writable." `
            -Cause "Could not create/write under $runtimeRoot." `
            -Action "Ensure .tmp/productization can be created. Do not point this launcher at authentic estate roots." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
            -LogPath $errLog
        $checks.failed_check = "productization_tmp"
        if ($Json) { $checks | ConvertTo-Json -Depth 6; exit 1 }
        exit 1
    }
    $checks.tmp_writable = $true
    Write-Host "OK  Writable .tmp and .tmp/productization"

    $checks.ok = $true
    Write-Host ""
    Write-Host "PREFLIGHT PASS (PRODUCTIZATION / NOT RELEASE / NOT PILOT)" -ForegroundColor Green
    Write-Host "Next: powershell -NoProfile -File scripts\windows\atlas-start.ps1" -ForegroundColor Cyan
    Write-Host "STRANGER path docs: docs\productization\install\STRANGER.md"
    Write-Host ""

    if ($Json) {
        $checks | ConvertTo-Json -Depth 6
    }
    exit 0
}
catch {
    Write-AtlasProductError `
        -What "Preflight aborted unexpectedly." `
        -Cause $_.Exception.Message `
        -Action "Capture the console output and open an issue with AS-PROD-INSTALL-001 context. Do not claim RELEASE or PILOT." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
        -LogPath $errLog
    $checks.failed_check = "exception"
    $checks.ok = $false
    if ($Json) { $checks | ConvertTo-Json -Depth 6 }
    exit 1
}
