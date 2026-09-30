#Requires -Version 5.1
<#
.SYNOPSIS
  AS-PROD-INSTALL-001 one-action Windows stranger bootstrap (start Atlas locally).

.DESCRIPTION
  PRODUCTIZATION path (NOT RELEASE / NOT PILOT):
  1) honesty banner
  2) preflight (Python 3.12+, Node/npm, repo root, writable .tmp)
  3) tip-local .venv + editable Core install if needed (never rewrite shared global Scripts)
  4) configure vault: DEMO_FIXTURE bootstrap under .tmp/productization OR operator vault path
  5) start atlas live api-serve on 127.0.0.1 (bounded)
  6) start apps/web (npm run dev) without Playwright
  7) health checks (API /v1/meta + web port)
  8) open local Atlas URL in the default browser

  Never invents authentic estate. Prefer DEMO_FIXTURE + .tmp/productization.
  On failure: structured product errors to stderr + log:
    WHAT:   ...
    CAUSE:  ...
    ACTION: ...
    RETRY:  ...

.PARAMETER Vault
  Existing vault directory. When omitted, uses DEMO_FIXTURE bootstrap when available,
  otherwise prompts (unless -NonInteractive).

.PARAMETER UseDemoFixture
  Force disposable vault build from DEMO_FIXTURE estate paths.

.PARAMETER NonInteractive
  Fail closed instead of prompting for a vault path.

.PARAMETER SkipBrowser
  Do not open the default browser after health checks.

.PARAMETER SkipWeb
  Start API only (still PRODUCTIZATION / NOT RELEASE).

.PARAMETER SkipInstall
  Skip pip install -e ".[dev]" even if atlas is missing (advanced).

.PARAMETER ApiPort
  LIVE_API port (default 8765). Bound to 127.0.0.1 only.

.PARAMETER WebPort
  Vite web port (default 5173).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\atlas-start.ps1

.EXAMPLE
  powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture -NonInteractive
#>
[CmdletBinding()]
param(
    [string]$Vault = "",
    [switch]$UseDemoFixture,
    [switch]$NonInteractive,
    [switch]$SkipBrowser,
    [switch]$SkipWeb,
    [switch]$SkipInstall,
    [int]$ApiPort = 8765,
    [int]$WebPort = 5173
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
. (Join-Path $ScriptDir "_AtlasCommon.ps1")

function Start-TrackedProcess {
    <#
    .SYNOPSIS
      Spawn a process and immediately bind SEC-025 provisional identity (SEC-026).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StateDir,
        [Parameter(Mandatory = $true)][string]$SessionNonce,
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)]$ProcessRecords,
        [int[]]$ExpectedPorts = @()
    )
    $logOut = Join-Path $StateDir "$Name.stdout.log"
    $logErr = Join-Path $StateDir "$Name.stderr.log"
    # Pre-create redirect targets so Wave-B ACL can attach before secrets land.
    New-Item -ItemType File -Path $logOut -Force -ErrorAction SilentlyContinue | Out-Null
    New-Item -ItemType File -Path $logErr -Force -ErrorAction SilentlyContinue | Out-Null
    if ($Name -eq "live-api") {
        Protect-AtlasSensitiveFile -Path $logOut
        Protect-AtlasSensitiveFile -Path $logErr
    }
    $proc = Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logOut `
        -RedirectStandardError $logErr
    # Re-assert ACL after Start-Process open/truncate (SEC-ADV004-B-002).
    if ($Name -eq "live-api") {
        Protect-AtlasSensitiveFile -Path $logOut
        Protect-AtlasSensitiveFile -Path $logErr
    }
    $record = New-AtlasProcessIdentityRecord `
        -Name $Name `
        -ProcessId $proc.Id `
        -SessionNonce $SessionNonce `
        -WorkingDirectory $WorkingDirectory `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -ExpectedPorts $ExpectedPorts `
        -LogStdout $logOut `
        -LogStderr $logErr
    [void]$ProcessRecords.Add($record)
    # SEC-026: provisional identity on disk immediately after spawn (crash-safe).
    Write-AtlasSessionState -PidFile $PidFile -SessionNonce $SessionNonce -Processes @($ProcessRecords.ToArray()) -Extra @{
        claim = "PROVISIONAL_SESSION"
        note  = "Provisional identities after spawn; not yet health-complete"
    }
    return $record
}

function Invoke-AtlasStartAbortCleanup {
    <#
    .SYNOPSIS
      SEC-026: on failed start, stop only verified session processes; report orphans.
    #>
    param(
        [Parameter(Mandatory = $true)]$ProcessRecords,
        [Parameter(Mandatory = $true)][string]$SessionNonce,
        [Parameter(Mandatory = $true)][string]$PidFile
    )
    $summary = Stop-AtlasVerifiedSession -Processes @($ProcessRecords.ToArray()) -SessionNonce $SessionNonce
    Write-Host "ORPHAN_PROCESS_COUNT=$($summary.ORPHAN_PROCESS_COUNT) (session=$SessionNonce)" -ForegroundColor $(if ($summary.ORPHAN_PROCESS_COUNT -eq 0) { "Green" } else { "Red" })
    $stateDir = Split-Path -Parent $PidFile
    $tokenPath = Join-Path $stateDir "live-api.read.token"
    if (Test-Path -LiteralPath $tokenPath) {
        try { icacls $tokenPath /grant:r "${env:USERNAME}:(F)" | Out-Null } catch { }
        Remove-Item -LiteralPath $tokenPath -Force -ErrorAction SilentlyContinue
    }
    if ($summary.ORPHAN_PROCESS_COUNT -eq 0 -and (Test-Path -LiteralPath $PidFile)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    return $summary
}

function Get-AtlasPipShowField {
    <#
    .SYNOPSIS
      Read one field from `pip show <package>` (e.g. Editable project location).
    #>
    param(
        [Parameter(Mandatory = $true)]$Python,
        [Parameter(Mandatory = $true)][string]$Package,
        [Parameter(Mandatory = $true)][string]$Field
    )
    $raw = & $Python.Exe @($Python.Args + @("-m", "pip", "show", $Package)) 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        return $null
    }
    $prefix = "${Field}:"
    foreach ($line in @($raw)) {
        $text = [string]$line
        if ($text.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $text.Substring($prefix.Length).Trim()
        }
    }
    return $null
}

function Test-AtlasCliTipCompatible {
    <#
    .SYNOPSIS
      True when atlas.exe supports `live`, editable install points at RepoRoot,
      PYTHONPATH cannot shadow a foreign project_atlas, and import __file__ is under tip
      (I03 + ENV-ISO-001 fail-closed tip identity).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$AtlasExe,
        [Parameter(Mandatory = $true)]$Python,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    if (-not (Test-Path -LiteralPath $AtlasExe)) {
        return @{
            Ok     = $false
            Reason = "missing_atlas_exe"
            Detail = "atlas.exe not found at $AtlasExe"
        }
    }

    $ppSafe = Test-AtlasPythonPathTipSafe -RepoRoot $RepoRoot
    if (-not $ppSafe.Ok) {
        return @{
            Ok     = $false
            Reason = [string]$ppSafe.Reason
            Detail = [string]$ppSafe.Detail
        }
    }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $helpOut = & $AtlasExe "live" "--help" 2>&1 | Out-String
        $helpCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($helpCode -ne 0 -or ($helpOut -match "invalid choice") -or ($helpOut -notmatch "api-serve")) {
        return @{
            Ok     = $false
            Reason = "missing_live_subcommand"
            Detail = "CLI at $AtlasExe does not support 'atlas live' (stale / tip-incompatible install)."
        }
    }

    $editable = Get-AtlasPipShowField -Python $Python -Package "project-atlas" -Field "Editable project location"
    if (-not $editable) {
        return @{
            Ok     = $false
            Reason = "not_editable_from_repo"
            Detail = "project-atlas is not an editable install from this checkout ($RepoRoot)."
        }
    }
    if (-not (Test-AtlasPathUnderRoot -Path $editable -Root $RepoRoot)) {
        return @{
            Ok     = $false
            Reason = "editable_wrong_worktree"
            Detail = "Editable project location '$editable' is not under current RepoRoot '$RepoRoot' (stale CLI from another worktree)."
        }
    }

    $importSafe = Test-AtlasImportLocationTipSafe -Python $Python -RepoRoot $RepoRoot
    if (-not $importSafe.Ok) {
        return @{
            Ok     = $false
            Reason = [string]$importSafe.Reason
            Detail = [string]$importSafe.Detail
        }
    }

    return @{
        Ok     = $true
        Reason = "ok"
        Detail = "tip-compatible editable install at $editable; $($importSafe.Detail)"
    }
}

function Ensure-AtlasTipLocalVenv {
    <#
    .SYNOPSIS
      ENV-ISO-002: ensure tip-local .venv exists; never mutate a shared global env.
      Uses a bootstrap interpreter (py -3.12 / PATH python) only to create .venv.
      Refuses junction/reparse .venv whose final target escapes RepoRoot (CLAUDE-ADV005-002).
    #>
    param(
        [Parameter(Mandatory = $true)]$BootstrapPython,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$ErrLog,
        [switch]$SkipInstall
    )
    $tipPyPath = Get-AtlasTipVenvPythonPath -RepoRoot $RepoRoot
    $venvDir = Join-Path $RepoRoot ".venv"
    if (Test-Path -LiteralPath $tipPyPath) {
        $existing = Get-AtlasPythonCommand -RepoRoot $RepoRoot
        if ($existing -and (Test-AtlasInterpreterIsTipVenv -Python $existing -RepoRoot $RepoRoot)) {
            Write-Host "OK  tip-local interpreter: $($existing.Label)"
            return $existing
        }
        # Lexical tip python exists but final FS target is outside RepoRoot (junction bypass).
        if (-not (Test-AtlasPathUnderRoot -Path $tipPyPath -Root $RepoRoot)) {
            $finalHint = $tipPyPath
            try { $finalHint = Resolve-AtlasFinalPath -Path $tipPyPath } catch { }
            Write-AtlasProductError `
                -What "Refusing tip .venv whose final filesystem target escapes RepoRoot (CLAUDE-ADV005-002 / ENV-ISO-002)." `
                -Cause "Lexical path '$tipPyPath' exists under tip, but final path '$finalHint' is not under '$RepoRoot' (junction/reparse escape)." `
                -Action "Remove the junction/reparse at $venvDir and create a real tip-local venv: py -3.12 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e `".[dev]`" from $RepoRoot." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
            exit 1
        }
    }

    if ($SkipInstall) {
        Write-AtlasProductError `
            -What "Tip-local .venv is required for isolated Atlas start (ENV-ISO-002)." `
            -Cause "No usable tip-local interpreter at $tipPyPath and -SkipInstall prevents creating it." `
            -Action "Create a tip venv then install: py -3.12 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e `".[dev]`" from $RepoRoot. Do not pip-install into a shared global Python." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }

    Write-Host "Configuring: tip-local .venv via $($BootstrapPython.Label) -m venv .venv (no global editable rewrite)..."
    $code = Invoke-AtlasPython -Python $BootstrapPython -WorkingDirectory $RepoRoot -Arguments @(
        "-m", "venv", ".venv"
    )
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $tipPyPath)) {
        Write-AtlasProductError `
            -What "Failed to create tip-local .venv for isolated Atlas start." `
            -Cause "$($BootstrapPython.Label) -m venv .venv exited with code $code (expected $tipPyPath)." `
            -Action "From $RepoRoot run: $($BootstrapPython.Label) -m venv .venv. Ensure the directory is writable. Do not use a shared global editable install across worktrees." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }

    $tip = Get-AtlasPythonCommand -RepoRoot $RepoRoot
    if (-not $tip -or -not (Test-AtlasInterpreterIsTipVenv -Python $tip -RepoRoot $RepoRoot)) {
        Write-AtlasProductError `
            -What "Tip-local .venv was created but could not be selected." `
            -Cause "Get-AtlasPythonCommand did not return tip-venv after creating $venvDir." `
            -Action "Confirm $tipPyPath exists and is Python 3.12+. Re-run start from $RepoRoot." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }
    Write-Host "OK  tip-local .venv ready: $($tip.Label)"
    return $tip
}

function Install-AtlasEditableFromRepo {
    param(
        [Parameter(Mandatory = $true)]$Python,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$ErrLog
    )
    Write-Host "Configuring: editable install via $($Python.Label) pip install -e `".[dev]`" from $RepoRoot ..."
    $code = Invoke-AtlasPython -Python $Python -WorkingDirectory $RepoRoot -Arguments @(
        "-m", "pip", "install", "-e", ".[dev]"
    )
    if ($code -ne 0) {
        Write-AtlasProductError `
            -What "Editable install of Project Atlas failed." `
            -Cause "pip install -e `".[dev]`" exited with code $code." `
            -Action "Ensure Python 3.12+ and pip work, then from repo root run: python -m pip install -e `".[dev]`". Check network/proxy if packages cannot download." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }
}

function Ensure-AtlasEditableInstall {
    param(
        [Parameter(Mandatory = $true)]$Python,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$ErrLog,
        [switch]$SkipInstall
    )
    # ENV-ISO-002: editable install / atlas.exe must live in tip .venv only —
    # never rewrite shared global Scripts\atlas.exe across worktrees.
    if (-not (Test-AtlasInterpreterIsTipVenv -Python $Python -RepoRoot $RepoRoot)) {
        Write-AtlasProductError `
            -What "Refusing editable install into a non-tip interpreter (ENV-ISO-002 fail-closed)." `
            -Cause "Selected Python '$($Python.Label)' ($($Python.Exe)) is not under $RepoRoot\.venv. Shared global editables contaminate sibling worktrees and contend on Scripts\atlas.exe." `
            -Action "Use tip-local isolation only: py -3.12 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -e `".[dev]`" from $RepoRoot. Re-run atlas-start so Get-AtlasPythonCommand prefers .venv\Scripts." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }

    $ppSafe = Test-AtlasPythonPathTipSafe -RepoRoot $RepoRoot
    if (-not $ppSafe.Ok) {
        Write-AtlasProductError `
            -What "PYTHONPATH shadows project_atlas from outside this tip (ENV-ISO-001 fail-closed)." `
            -Cause "$($ppSafe.Detail)" `
            -Action "Unset or rewrite PYTHONPATH so no entry outside $RepoRoot provides project_atlas (e.g. remove sibling worktree \src paths). Tip identity must not rely on I03 editable Location alone." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }

    # Prefer atlas beside the tip-venv interpreter (Resolve-AtlasScriptsDir is venv-aware).
    # Require tip-compatible CLI: `atlas live` + editable Location + import under RepoRoot (I03/ENV-ISO).
    # CLAUDE-ADV005-011: never bind foreign PATH atlas.exe for live serve — tip .venv Scripts only.
    $scriptsDir = Resolve-AtlasScriptsDir -Python $Python
    $hint = Join-Path $scriptsDir "atlas.exe"
    $needsInstall = $true
    $incompatDetail = $null

    if (Test-Path -LiteralPath $hint) {
        if (-not (Test-AtlasPathUnderRoot -Path $hint -Root $RepoRoot)) {
            Write-AtlasProductError `
                -What "Refusing atlas.exe whose final filesystem target escapes RepoRoot (CLAUDE-ADV005-002)." `
                -Cause "Candidate '$hint' final path is not under '$RepoRoot'." `
                -Action "Remove junction/reparse under tip .venv and reinstall: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`" from $RepoRoot." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
            exit 1
        }
        $compat = Test-AtlasCliTipCompatible -AtlasExe $hint -Python $Python -RepoRoot $RepoRoot
        if ($compat.Ok) {
            Write-Host "OK  atlas tip-compatible for $($Python.Label): $hint"
            return $hint
        }
        $needsInstall = $true
        $incompatDetail = [string]$compat.Detail
        Write-Host "NOTE: existing atlas is tip-incompatible ($($compat.Reason)): $incompatDetail" -ForegroundColor DarkYellow
        if ($compat.Reason -eq "pythonpath_foreign_shadow" -or $compat.Reason -eq "import_wrong_worktree") {
            Write-AtlasProductError `
                -What "Atlas tip identity failed closed on import/PYTHONPATH shadow (ENV-ISO-001)." `
                -Cause "$incompatDetail" `
                -Action "Clear foreign PYTHONPATH entries pointing at sibling worktrees, then retry. Do not rely on editable Location alone." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
            exit 1
        }
        Write-Host "      Will reinstall editable package into tip .venv from current RepoRoot unless -SkipInstall." -ForegroundColor DarkYellow
    }
    else {
        $atlasCmd = Get-Command atlas -ErrorAction SilentlyContinue
        if ($atlasCmd) {
            Write-Host "NOTE: PATH atlas $($atlasCmd.Source) is not tip .venv Scripts; refusing foreign PATH bind (CLAUDE-ADV005-011). Will install into tip .venv only." -ForegroundColor DarkYellow
        }
    }

    if ($SkipInstall) {
        if ($incompatDetail) {
            Write-AtlasProductError `
                -What "Atlas CLI is present but tip-incompatible with this checkout (stale CLI / missing live)." `
                -Cause "$incompatDetail -SkipInstall prevents repair via pip install -e `".[dev]`"." `
                -Action "Remove -SkipInstall so the launcher reinstalls into tip .venv from $RepoRoot, or manually run: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`" from this repo root. Confirm: atlas live --help" `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
        }
        else {
            Write-AtlasProductError `
                -What "Atlas CLI ('atlas') is not available for the tip-local .venv." `
                -Cause "-SkipInstall was set and no atlas.exe was found under $scriptsDir." `
                -Action "Remove -SkipInstall or run: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`" from the repo root, then reopen the shell." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
        }
        exit 1
    }

    if ($needsInstall) {
        Install-AtlasEditableFromRepo -Python $Python -RepoRoot $RepoRoot -ErrLog $ErrLog
    }

    if (Test-Path -LiteralPath $hint) {
        if (-not (Test-AtlasPathUnderRoot -Path $hint -Root $RepoRoot)) {
            Write-AtlasProductError `
                -What "Refusing atlas.exe whose final filesystem target escapes RepoRoot (CLAUDE-ADV005-002)." `
                -Cause "Post-install candidate '$hint' final path is not under '$RepoRoot'." `
                -Action "Remove junction/reparse under tip .venv and reinstall from $RepoRoot." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
            exit 1
        }
        $compat = Test-AtlasCliTipCompatible -AtlasExe $hint -Python $Python -RepoRoot $RepoRoot
        if ($compat.Ok) {
            Write-Host "OK  atlas tip-compatible at $hint"
            return $hint
        }
        Write-AtlasProductError `
            -What "Install finished but Atlas CLI is still tip-incompatible." `
            -Cause "$($compat.Detail)" `
            -Action "Confirm pip targeted tip .venv: .\.venv\Scripts\python.exe -m pip show project-atlas. Clear foreign PYTHONPATH. Re-run from $RepoRoot. Verify: atlas live --help" `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $ErrLog
        exit 1
    }

    # CLAUDE-ADV005-011: fail closed — never fall back to foreign PATH atlas.exe for live serve.
    $atlasCmd = Get-Command atlas -ErrorAction SilentlyContinue
    $pathDetail = if ($atlasCmd) { " PATH atlas is '$($atlasCmd.Source)' (foreign; refused)." } else { "" }
    Write-AtlasProductError `
        -What "Install finished but tip .venv Scripts\atlas.exe is still missing (refusing PATH fallback)." `
        -Cause "pip reported success but no atlas.exe was found under $scriptsDir.$pathDetail" `
        -Action "Confirm tip .venv Scripts contains atlas.exe. Do not install into a shared global Python. Confirm: .\.venv\Scripts\python.exe -m pip show project-atlas" `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
        -LogPath $ErrLog
    exit 1
}

function Build-DemoFixtureVault {
    param(
        [Parameter(Mandatory = $true)][string]$AtlasExe,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$VaultDir,
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$FixtureRoot,
        [Parameter(Mandatory = $true)][string]$ErrLog
    )
    Write-Host "Bootstrapping disposable vault from DEMO_FIXTURE (not authentic estate)..."
    Write-Host "  fixture: $FixtureRoot"
    Write-Host "  vault:   $VaultDir"
    if (Test-Path -LiteralPath $VaultDir) {
        Remove-Item -Recurse -Force $VaultDir
    }
    # Run atlas CLI with repo WorkingDirectory so [tool.atlas] / config resolve from this checkout.
    Push-Location $RepoRoot
    try {
        & $AtlasExe init --output $VaultDir
        if ($LASTEXITCODE -ne 0) {
            Write-AtlasProductError `
                -What "Failed to initialize disposable productization vault." `
                -Cause "atlas init exited with code $LASTEXITCODE." `
                -Action "Inspect Core install (atlas version). Ensure VaultDir under .tmp/productization is empty/writable." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture" `
                -LogPath $ErrLog
            exit 1
        }
        $manifest = Join-Path $RuntimeRoot "manifest.json"
        & $AtlasExe discover --source $FixtureRoot --output $manifest
        if ($LASTEXITCODE -ne 0) {
            Write-AtlasProductError `
                -What "Failed to discover DEMO_FIXTURE sources." `
                -Cause "atlas discover exited with code $LASTEXITCODE against $FixtureRoot." `
                -Action "Confirm fixtures exist under tests/fixtures/demo/estate or fixtures/demo/estate." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture" `
                -LogPath $ErrLog
            exit 1
        }
        & $AtlasExe ingest --manifest $manifest --vault $VaultDir --source $FixtureRoot
        if ($LASTEXITCODE -ne 0) {
            Write-AtlasProductError `
                -What "Failed to ingest DEMO_FIXTURE into disposable vault." `
                -Cause "atlas ingest exited with code $LASTEXITCODE." `
                -Action "Read ingest logs; fix fixture corpus; do not substitute authentic estate without an explicit operator vault path." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture" `
                -LogPath $ErrLog
            exit 1
        }
        & $AtlasExe build-indexes --vault $VaultDir
        if ($LASTEXITCODE -ne 0) {
            Write-AtlasProductError `
                -What "Failed to build indexes for disposable vault." `
                -Cause "atlas build-indexes exited with code $LASTEXITCODE." `
                -Action "Re-run after a clean .tmp/productization/vault or report Core index errors." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture" `
                -LogPath $ErrLog
            exit 1
        }
        # build-portfolio also derives the bitemporal validity catalog the
        # KDiff / Time Machine lens reads (generated/ops/bitemporal/).
        & $AtlasExe build-portfolio --vault $VaultDir
        if ($LASTEXITCODE -ne 0) {
            Write-AtlasProductError `
                -What "Failed to build portfolio for disposable vault." `
                -Cause "atlas build-portfolio exited with code $LASTEXITCODE." `
                -Action "Re-run after a clean .tmp/productization/vault or report Core portfolio errors." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture" `
                -LogPath $ErrLog
            exit 1
        }
        & $AtlasExe validate --vault $VaultDir
        if ($LASTEXITCODE -ne 0) {
            Write-AtlasProductError `
                -What "Disposable vault failed validation." `
                -Cause "atlas validate exited with code $LASTEXITCODE." `
                -Action "Treat as a Core/fixture issue. Do not claim STRANGER_CAN_START_ATLAS until validate passes." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture" `
                -LogPath $ErrLog
            exit 1
        }
    }
    finally {
        Pop-Location
    }
    Write-Host "OK  DEMO_FIXTURE disposable vault ready (PRODUCTIZATION only)"
}

function Ensure-WebDependencies {
    param(
        [Parameter(Mandatory = $true)]$Npm,
        [Parameter(Mandatory = $true)][string]$WebDir,
        [Parameter(Mandatory = $true)][string]$ErrLog
    )
    $modules = Join-Path $WebDir "node_modules"
    $rollupNative = Join-Path $modules "@rollup\rollup-win32-x64-msvc"
    $needsInstall = -not (Test-Path -LiteralPath $modules)
    $needsRepair = (Test-Path -LiteralPath $modules) -and -not (Test-Path -LiteralPath $rollupNative)

    if ($needsRepair) {
        Write-Host "Repairing apps/web node_modules (missing Rollup Windows optional native)..."
        Remove-Item -Recurse -Force $modules -ErrorAction SilentlyContinue
        $needsInstall = $true
    }

    if ($needsInstall) {
        $lockFile = Join-Path $WebDir "package-lock.json"
        # SEC-027: require committed lock + npm ci (fail closed; no unbound npm install).
        if (-not (Test-Path -LiteralPath $lockFile)) {
            Write-AtlasProductError `
                -What "apps/web/package-lock.json is missing; refusing unbound npm install." `
                -Cause "SEC-027 requires a committed lockfile for deterministic third-party installs." `
                -Action "Restore package-lock.json from the repository tip, then retry." `
                -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                -LogPath $ErrLog
            exit 1
        }
        Write-Host "Installing apps/web dependencies (npm ci; locked; no Playwright)..."
        Push-Location $WebDir
        try {
            & $Npm.Source ci --no-fund --no-audit
            if ($LASTEXITCODE -ne 0) {
                Write-AtlasProductError `
                    -What "npm ci for apps/web failed." `
                    -Cause "npm ci exited with code $LASTEXITCODE." `
                    -Action "Check network/proxy and Node version. Prefer a committed package-lock.json (SEC-027). Do not add Playwright packages for this productization path." `
                    -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                    -LogPath $ErrLog
                exit 1
            }
            # npm optional-deps bug on Windows can omit @rollup/rollup-win32-x64-msvc.
            if (-not (Test-Path -LiteralPath $rollupNative)) {
                Write-Host "Installing Rollup Windows native optional dependency..."
                & $Npm.Source install --no-fund --no-audit --include=optional "@rollup/rollup-win32-x64-msvc"
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $rollupNative)) {
                    Write-AtlasProductError `
                        -What "apps/web install is missing the Windows Rollup native module." `
                        -Cause "Optional dependency @rollup/rollup-win32-x64-msvc was not present after npm ci (known npm optional-deps issue)." `
                        -Action "From apps/web run: Remove-Item -Recurse -Force node_modules; npm ci --include=optional. Do not add Playwright." `
                        -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
                        -LogPath $ErrLog
                    exit 1
                }
            }
        }
        finally { Pop-Location }
    }
}

# --- banner + honesty stamps ---
Write-AtlasProductBanner
$env:ATLAS_PRODUCTIZATION = "1"
$env:ATLAS_RELEASE_STATUS = "RELEASE CERTIFIED = NO"
$env:ATLAS_PILOT_STATUS = "PILOT = DORMANT_BLOCKED (NOT PILOT PASS)"
$env:ATLAS_INSTALL_CLAIM = "PRODUCTIZATION / NOT RELEASE / NOT PILOT - STRANGER_CAN_START_ATLAS local path"

$repoRoot = Resolve-AtlasRepoRoot -ScriptDir $ScriptDir
if (-not $repoRoot) {
    Write-AtlasProductError `
        -What "Cannot resolve Project Atlas repository root." `
        -Cause "scripts/windows helpers could not find pyproject.toml + apps/web." `
        -Action "Run from a full git checkout of WezzSide/project-atlas." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1"
    exit 1
}

$runtimeRoot = Join-Path $repoRoot ".tmp\productization"
$stateDir = Join-Path $runtimeRoot "state"
$logDir = Join-Path $runtimeRoot "logs"
$errLog = Join-Path $logDir "start-errors.log"
$pidFile = Join-Path $stateDir "atlas-pids.json"
$defaultVault = Join-Path $runtimeRoot "vault"
New-Item -ItemType Directory -Force -Path $stateDir, $logDir | Out-Null

# SEC-026: every start binds a fresh session nonce; clean prior verified session first.
$sessionNonce = New-AtlasSessionNonce
Write-Host "Session nonce: $sessionNonce"
if (Test-Path -LiteralPath $pidFile) {
    Write-Host "Prior productization state found - stopping verified prior session (orphan cleanup)..."
    try {
        $prior = Get-Content -Raw -LiteralPath $pidFile | ConvertFrom-Json
        $priorNonce = ""
        if ($prior.session_nonce) { $priorNonce = [string]$prior.session_nonce }
        if ($prior.processes) {
            $priorSummary = Stop-AtlasVerifiedSession -Processes @($prior.processes) -SessionNonce $priorNonce
            Write-Host "Prior ORPHAN_PROCESS_COUNT=$($priorSummary.ORPHAN_PROCESS_COUNT)"
        }
    }
    catch {
        Write-Host "WARN: could not clean prior state: $($_.Exception.Message)" -ForegroundColor DarkYellow
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

# Refuse inventing authentic estate via env
if ($env:AUTHENTIC_ESTATE_ROOT -and $env:AUTHENTIC_ESTATE_ROOT.Trim().Length -gt 0 -and -not $Vault) {
    Write-Host "NOTE: AUTHENTIC_ESTATE_ROOT is set but this launcher will not invent estate from it." -ForegroundColor DarkYellow
    Write-Host "      Pass -Vault <existing-vault> explicitly if you already have an operator vault." -ForegroundColor DarkYellow
}

# --- preflight (reusable) ---
Write-Host "Running preflight..."
& (Join-Path $ScriptDir "atlas-preflight.ps1") -SkipBanner
if ($LASTEXITCODE -ne 0) {
    Write-AtlasProductError `
        -What "Preflight failed; Atlas was not started." `
        -Cause "scripts/windows/atlas-preflight.ps1 exited with code $LASTEXITCODE." `
        -Action "Resolve the printed WHAT/CAUSE/ACTION items (Python 3.12+, Node/npm, writable .tmp)." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-preflight.ps1" `
        -LogPath $errLog
    exit 1
}

$bootstrapPython = Get-AtlasPythonCommand -RepoRoot $repoRoot
if (-not $bootstrapPython) {
    # Tip .venv may be absent; allow global bootstrap candidate without RepoRoot preference.
    $bootstrapPython = Get-AtlasPythonCommand
}
if (-not $bootstrapPython) {
    Write-AtlasProductError `
        -What "Python 3.12+ disappeared after preflight." `
        -Cause "Get-AtlasPythonCommand returned null unexpectedly." `
        -Action "Re-run preflight and confirm Python remains on PATH." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
        -LogPath $errLog
    exit 1
}

$python = Ensure-AtlasTipLocalVenv -BootstrapPython $bootstrapPython -RepoRoot $repoRoot -ErrLog $errLog -SkipInstall:$SkipInstall
$atlasExe = Ensure-AtlasEditableInstall -Python $python -RepoRoot $repoRoot -ErrLog $errLog -SkipInstall:$SkipInstall

# --- vault configure ---
$fixtureRoot = Resolve-DemoFixtureEstate -RepoRoot $repoRoot
$vaultPath = $null
$vaultMode = $null

if ($Vault -and $Vault.Trim().Length -gt 0) {
    $vaultPath = [System.IO.Path]::GetFullPath($Vault)
    if (-not (Test-Path -LiteralPath $vaultPath)) {
        Write-AtlasProductError `
            -What "Operator vault path does not exist." `
            -Cause "Path '$vaultPath' was not found." `
            -Action "Pass an existing vault directory, or omit -Vault to use DEMO_FIXTURE under .tmp/productization." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -Vault <path>" `
            -LogPath $errLog
        exit 1
    }
    $vaultMode = "operator_vault"
}
elseif ($UseDemoFixture -or $fixtureRoot) {
    if (-not $fixtureRoot) {
        Write-AtlasProductError `
            -What "DEMO_FIXTURE estate is missing." `
            -Cause "Expected tests/fixtures/demo/estate or fixtures/demo/estate." `
            -Action "Restore DEMO_FIXTURE corpus, or pass -Vault <existing-vault>. This launcher will not invent authentic estate." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -Vault <path>" `
            -LogPath $errLog
        exit 1
    }
    Build-DemoFixtureVault -AtlasExe $atlasExe -RepoRoot $repoRoot -VaultDir $defaultVault `
        -RuntimeRoot $runtimeRoot -FixtureRoot $fixtureRoot -ErrLog $errLog
    $vaultPath = $defaultVault
    $vaultMode = "demo_fixture"
}
else {
    if ($NonInteractive) {
        Write-AtlasProductError `
            -What "No vault configured in non-interactive mode." `
            -Cause "DEMO_FIXTURE estate was not found and -Vault was not provided." `
            -Action "Provide -Vault <path> or restore DEMO_FIXTURE, then pass -UseDemoFixture." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -UseDemoFixture -NonInteractive" `
            -LogPath $errLog
        exit 1
    }
    Write-Host ""
    Write-Host "No DEMO_FIXTURE estate found. Enter an EXISTING vault path." -ForegroundColor Yellow
    Write-Host "Do not invent authentic estate roots. Prefer a disposable vault under .tmp/productization." -ForegroundColor Yellow
    $inputVault = Read-Host "Vault path (or blank to cancel)"
    if ([string]::IsNullOrWhiteSpace($inputVault)) {
        Write-AtlasProductError `
            -What "Start cancelled: no vault path provided." `
            -Cause "Operator declined vault prompt and DEMO_FIXTURE was unavailable." `
            -Action "Create/init a vault or restore DEMO_FIXTURE, then retry." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -Vault <path>" `
            -LogPath $errLog
        exit 1
    }
    $vaultPath = [System.IO.Path]::GetFullPath($inputVault)
    if (-not (Test-Path -LiteralPath $vaultPath)) {
        Write-AtlasProductError `
            -What "Entered vault path does not exist." `
            -Cause "Path '$vaultPath' was not found." `
            -Action "Init a vault with 'atlas init --output <dir>' or restore DEMO_FIXTURE." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1 -Vault <path>" `
            -LogPath $errLog
        exit 1
    }
    $vaultMode = "operator_prompt"
}

Write-Host "Vault mode : $vaultMode"
Write-Host "Vault path : $vaultPath"
Write-Host "Runtime    : $runtimeRoot"
Write-Host ""

$processRecords = New-Object System.Collections.Generic.List[object]
$atlasStartCompleted = $false

# --- refuse occupied ports (avoid false-positive health against a foreign listener) ---
if (-not (Test-AtlasPortFree -Port $ApiPort)) {
    Write-AtlasProductError `
        -What "API port $ApiPort is already in use on loopback." `
        -Cause "A listener is already bound on 127.0.0.1:$ApiPort (or 0.0.0.0:$ApiPort). Starting another api-serve would risk false health against the foreign process." `
        -Action "Stop the other process (powershell -NoProfile -File scripts\windows\atlas-stop.ps1 if it was this launcher), or pass -ApiPort <free-port>." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1 -ApiPort <free-port>" `
        -LogPath $errLog
    exit 1
}
if (-not $SkipWeb -and -not (Test-AtlasPortFree -Port $WebPort)) {
    Write-AtlasProductError `
        -What "Web port $WebPort is already in use on loopback." `
        -Cause "A listener is already bound on 127.0.0.1:$WebPort (or 0.0.0.0:$WebPort)." `
        -Action "Free the port or pass -WebPort <free-port>. Stop prior productization sessions with atlas-stop.ps1." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1 -WebPort <free-port>" `
        -LogPath $errLog
    exit 1
}

try {
# --- start LIVE_API (loopback only) ---
# PROD-ADV-011: CORS must match session -WebPort (not hardcoded 5173).
# Set before api-serve so /v1/meta cors_origin + ACAO headers match the Vite origin.
$corsOrigin = "http://127.0.0.1:$WebPort"
$env:ATLAS_CORS_ORIGIN = $corsOrigin
Write-Host "Starting LIVE_API on 127.0.0.1:$ApiPort (CORS_ORIGIN=$corsOrigin) ..."
$tokenPath = Join-Path $stateDir "live-api.read.token"
# SEC-ADV004-B-002: mint into hardened token file; do not rely on stderr dump.
if (Test-Path -LiteralPath $tokenPath) {
    try {
        icacls $tokenPath /grant:r "${env:USERNAME}:(F)" | Out-Null
    }
    catch { }
    Remove-Item -LiteralPath $tokenPath -Force -ErrorAction SilentlyContinue
}
$env:ATLAS_API_TOKEN_FILE = $tokenPath
$apiArgs = "live api-serve --vault `"$vaultPath`" --host 127.0.0.1 --port $ApiPort"
$apiProc = Start-TrackedProcess -Name "live-api" -FilePath $atlasExe `
    -ArgumentList $apiArgs -WorkingDirectory $repoRoot -StateDir $stateDir `
    -SessionNonce $sessionNonce -PidFile $pidFile -ProcessRecords $processRecords `
    -ExpectedPorts @($ApiPort)
Write-Host "  LIVE_API pid=$($apiProc.pid) (provisional identity bound)"

# SEC-009: capture per-launch read token (prefer token file; never print value).
$apiReadToken = Wait-AtlasApiReadToken `
    -StderrLogPath $apiProc.log_stderr `
    -TokenFilePath $tokenPath `
    -TimeoutSec 45
if ([string]::IsNullOrWhiteSpace($apiReadToken)) {
    Write-AtlasProductError `
        -What "LIVE_API started but did not publish ATLAS_API_READ_TOKEN." `
        -Cause "api-serve did not write ATLAS_API_TOKEN_FILE / non-redacted stderr within timeout (SEC-009 session mint)." `
        -Action "Inspect $($apiProc.log_stderr) and $tokenPath. Confirm tip CLI includes SEC-009 session auth. Do not disable auth or hardcode tokens." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
        -LogPath $errLog
    throw "ATLAS_START_ABORT: missing ATLAS_API_READ_TOKEN"
}
# Ensure durable token file content + ACL (Wave B / SEC-ADV004-B-002).
if (-not (Test-Path -LiteralPath $tokenPath) -or `
    ([string](Get-Content -LiteralPath $tokenPath -Raw -ErrorAction SilentlyContinue)).Trim() -ne $apiReadToken) {
    if (Test-Path -LiteralPath $tokenPath) {
        try { icacls $tokenPath /grant:r "${env:USERNAME}:(F)" | Out-Null } catch { }
        Remove-Item -LiteralPath $tokenPath -Force -ErrorAction SilentlyContinue
    }
    Set-Content -LiteralPath $tokenPath -Value $apiReadToken -NoNewline -Encoding ascii
}
Protect-AtlasSensitiveFile -Path $tokenPath
# Scrub any accidental full-token stderr residue and harden stderr ACL.
Clear-AtlasSecretFromLog -LogPath $apiProc.log_stderr -Secret $apiReadToken
Protect-AtlasSensitiveFile -Path $apiProc.log_stderr
Protect-AtlasSensitiveFile -Path $apiProc.log_stdout

$apiHealth = Wait-AtlasHttpOk -Url "http://127.0.0.1:$ApiPort/v1/meta" -TimeoutSec 60 -BearerToken $apiReadToken
if (-not $apiHealth.Ok) {
    $apiStderr = ""
    if (Test-Path -LiteralPath $apiProc.log_stderr) {
        $apiStderr = [string](Get-Content -LiteralPath $apiProc.log_stderr -Raw -ErrorAction SilentlyContinue)
    }
    # I03: stale atlas.exe without `live` produced opaque meta connect failure — name the real cause.
    if ($apiStderr -match "invalid choice:\s*'live'" -or $apiStderr -match "invalid choice:.*\blive\b") {
        $stderrOneLine = ($apiStderr.Trim() -replace "\s+", " ")
        if ($stderrOneLine.Length -gt 240) {
            $stderrOneLine = $stderrOneLine.Substring(0, 240)
        }
        Write-AtlasProductError `
            -What "LIVE_API failed because the Atlas CLI lacks the 'live' subcommand (stale / tip-incompatible install)." `
            -Cause "atlas live api-serve was rejected by '$atlasExe'. Typically an editable install from another worktree or an old package without LIVE_API. stderr: $stderrOneLine" `
            -Action "From this repo root run: $($python.Label) -m pip install -e `".[dev]`" (do not use -SkipInstall), then retry. Confirm: atlas live --help" `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: stale_cli_no_live"
    }
    Write-AtlasProductError `
        -What "LIVE_API health check failed (/v1/meta)." `
        -Cause $(if ($apiHealth.Error) { $apiHealth.Error } else { "No successful HTTP response within timeout." }) `
        -Action "Inspect $($apiProc.log_stderr). Confirm vault path and that port $ApiPort is free. API binds 127.0.0.1 only. If stderr shows invalid choice 'live', reinstall editable CLI from this RepoRoot." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
        -LogPath $errLog
    throw "ATLAS_START_ABORT: api_health_failed"
}
if (-not (Test-AtlasProcessOwnsPort -RootPid $apiProc.pid -Port $ApiPort -HostName "127.0.0.1")) {
    Write-AtlasProductError `
        -What "LIVE_API health succeeded but the listener on port $ApiPort is not our started process." `
        -Cause "HTTP /v1/meta responded, yet Get-NetTCPConnection Listen owner is not pid $($apiProc.pid) or its child (foreign/stale Atlas on the same port)." `
        -Action "Stop foreign listeners on $ApiPort, then retry with a free -ApiPort. Do not claim STRANGER_CAN_START_ATLAS from a borrowed health response." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1 -ApiPort <free-port>" `
        -LogPath $errLog
    throw "ATLAS_START_ABORT: foreign_api_listener"
}
$apiBind = Assert-AtlasLoopbackOnly -Port $ApiPort -Label "LIVE_API"
if (-not $apiBind.Ok) {
    Write-AtlasProductError `
        -What "LIVE_API is listening on a non-loopback address (SEC-029 fail-closed)." `
        -Cause $apiBind.Detail `
        -Action "Ensure api-serve uses --host 127.0.0.1 only. Do not bind 0.0.0.0." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
        -LogPath $errLog
    throw "ATLAS_START_ABORT: api_non_loopback_bind"
}
Write-Host "OK  API health http://127.0.0.1:$ApiPort/v1/meta (owned by started process; loopback-only)"
# PROD-ADV-011: fail closed if meta still advertises a CORS origin that is not this session's WebPort.
try {
    $metaObj = $apiHealth.Body | ConvertFrom-Json
    $metaCors = [string]$metaObj.cors_origin
    if ($metaCors -ne $corsOrigin) {
        Write-AtlasProductError `
            -What "LIVE_API /v1/meta cors_origin does not match session WebPort (PROD-ADV-011)." `
            -Cause "Expected cors_origin='$corsOrigin' (WebPort=$WebPort) but /v1/meta reported '$metaCors'." `
            -Action "Confirm tip CLI reads ATLAS_CORS_ORIGIN. Reinstall editable Core from this RepoRoot, then retry." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1 -WebPort $WebPort" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: cors_origin_mismatch"
    }
    Write-Host "OK  /v1/meta cors_origin matches WebPort ($corsOrigin)"
}
catch {
    if ("$($_.Exception.Message)" -match "^ATLAS_START_ABORT:") { throw }
    Write-AtlasProductError `
        -What "LIVE_API health succeeded but /v1/meta cors_origin could not be verified." `
        -Cause $_.Exception.Message `
        -Action "Inspect API response body. Confirm tip includes PROD-ADV-011 cors_origin in /v1/meta." `
        -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
        -LogPath $errLog
    throw "ATLAS_START_ABORT: cors_origin_unverified"
}
$env:VITE_ATLAS_API_BASE = "http://127.0.0.1:$ApiPort"
# Per-launch read Bearer for Vite web child only (not committed; not in URL).
$env:VITE_ATLAS_API_TOKEN = $apiReadToken
Write-Host "OK  LIVE_API session read token captured for Web (VITE_ATLAS_API_TOKEN; value not logged)"

$webUrl = "http://127.0.0.1:$WebPort/"

# --- start web ---
if (-not $SkipWeb) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) {
        Write-AtlasProductError `
            -What "npm disappeared after preflight; web shell not started." `
            -Cause "npm.cmd / npm not on PATH." `
            -Action "Reinstall Node.js LTS, then retry. Playwright is intentionally not installed by this path." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: npm_missing"
    }
    $webDir = Join-Path $repoRoot "apps\web"
    if (-not (Test-Path -LiteralPath (Join-Path $webDir "package.json"))) {
        Write-AtlasProductError `
            -What "apps/web package.json is missing." `
            -Cause "Web shell checkout appears incomplete." `
            -Action "Sync the repository so apps/web exists. Do not add Playwright via this installer." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: web_package_missing"
    }
    Ensure-WebDependencies -Npm $npm -WebDir $webDir -ErrLog $errLog
    Write-Host "Starting web (npm run dev) on 127.0.0.1:$WebPort ..."
    $webArgs = "run dev -- --host 127.0.0.1 --port $WebPort"
    $webProc = Start-TrackedProcess -Name "web" -FilePath $npm.Source `
        -ArgumentList $webArgs -WorkingDirectory $webDir -StateDir $stateDir `
        -SessionNonce $sessionNonce -PidFile $pidFile -ProcessRecords $processRecords `
        -ExpectedPorts @($WebPort)
    Write-Host "  Web pid=$($webProc.pid) (provisional identity bound)"

    $tcpOk = Wait-AtlasTcpOpen -HostName "127.0.0.1" -Port $WebPort -TimeoutSec 90
    if (-not $tcpOk) {
        $webAlive = $false
        try { $webAlive = [bool](Get-Process -Id $webProc.pid -ErrorAction Stop) } catch { $webAlive = $false }
        $stderrHint = ""
        if (Test-Path -LiteralPath $webProc.log_stderr) {
            $stderrHint = (Get-Content -LiteralPath $webProc.log_stderr -Raw -ErrorAction SilentlyContinue)
            if ($stderrHint -and $stderrHint.Length -gt 400) {
                $stderrHint = $stderrHint.Substring(0, 400)
            }
        }
        $cause = "TCP connect to 127.0.0.1:$WebPort timed out."
        if (-not $webAlive) {
            $cause = "Web process exited early before binding port $WebPort."
        }
        if ($stderrHint) {
            $cause = "$cause stderr: $stderrHint"
        }
        Write-AtlasProductError `
            -What "Web shell did not open port $WebPort in time." `
            -Cause $cause `
            -Action "Inspect $($webProc.log_stderr). If Rollup native is missing, delete apps/web/node_modules and retry. Free the port or pass -WebPort. Stop with atlas-stop.ps1." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: web_port_timeout"
    }
    if (-not (Test-AtlasProcessOwnsPort -RootPid $webProc.pid -Port $WebPort -HostName "127.0.0.1")) {
        Write-AtlasProductError `
            -What "Web port $WebPort is open but not owned by the started web process." `
            -Cause "TCP connect succeeded, yet Get-NetTCPConnection Listen owner is not pid $($webProc.pid) or a descendant in its process tree (foreign/stale listener)." `
            -Action "Stop foreign listeners on $WebPort, then retry with a free -WebPort. Do not claim STRANGER_CAN_START_ATLAS from a borrowed web health response." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1 -WebPort FREE_PORT" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: foreign_web_listener"
    }
    $webBind = Assert-AtlasLoopbackOnly -Port $WebPort -Label "web"
    if (-not $webBind.Ok) {
        Write-AtlasProductError `
            -What "Web shell is listening on a non-loopback address (SEC-029 fail-closed)." `
            -Cause $webBind.Detail `
            -Action "Ensure Vite uses --host 127.0.0.1 only. Do not bind 0.0.0.0." `
            -Retry "powershell -NoProfile -File scripts\windows\atlas-stop.ps1; powershell -NoProfile -File scripts\windows\atlas-start.ps1" `
            -LogPath $errLog
        throw "ATLAS_START_ABORT: web_non_loopback_bind"
    }
    # Prefer HTTP health when Vite answers
    $webHealth = Wait-AtlasHttpOk -Url $webUrl -TimeoutSec 30
    if ($webHealth.Ok) {
        Write-Host "OK  Web health $webUrl (loopback-only)"
    }
    else {
        Write-Host "OK  Web port open (HTTP probe soft-fail: $($webHealth.Error))" -ForegroundColor DarkYellow
    }
}

# Refresh ports on identity records after bind.
foreach ($rec in @($processRecords.ToArray())) {
    $livePorts = @(Get-AtlasListeningPortsForPid -ProcessId ([int]$rec.pid))
    if ($livePorts.Count -gt 0) {
        $rec.ports = $livePorts
    }
    $rec.provisional = $false
}

Write-AtlasSessionState -PidFile $pidFile -SessionNonce $sessionNonce -Processes @($processRecords.ToArray()) -Extra @{
    stranger_can_start = $true
    claim              = "STRANGER_CAN_START_ATLAS"
    note               = "PRODUCTIZATION local start - NOT RELEASE - NOT PILOT"
    vault_mode         = $vaultMode
    vault_path         = $vaultPath
    runtime_root       = $runtimeRoot
    api_url            = "http://127.0.0.1:$ApiPort/v1/meta"
    web_url            = $(if ($SkipWeb) { $null } else { $webUrl })
    cors_origin        = $corsOrigin
    api_port           = $ApiPort
    web_port           = $WebPort
}

$atlasStartCompleted = $true

Write-Host ""
Write-Host "State written: $pidFile (session_nonce=$sessionNonce)"
Write-Host "Stop with:  powershell -NoProfile -File scripts\windows\atlas-stop.ps1"
Write-Host ""
Write-Host "HONEST STATUS:" -ForegroundColor Yellow
Write-Host "  PRODUCTIZATION / NOT RELEASE" -ForegroundColor Yellow
Write-Host "  RELEASE CERTIFIED = NO" -ForegroundColor Yellow
Write-Host "  PILOT PASS = NO (DORMANT_BLOCKED)" -ForegroundColor Yellow
Write-Host "  STRANGER_CAN_START_ATLAS = YES (local health passed)" -ForegroundColor Green
Write-Host "  API: http://127.0.0.1:$ApiPort/v1/meta"
if (-not $SkipWeb) {
    Write-Host "  Web: $webUrl"
}

if (-not $SkipWeb -and -not $SkipBrowser) {
    try {
        Start-Process $webUrl | Out-Null
        Write-Host "Opened browser to $webUrl"
    }
    catch {
        Write-Host "WARN: could not open browser automatically: $($_.Exception.Message)" -ForegroundColor DarkYellow
        Write-Host "Open manually: $webUrl"
    }
}

Write-Host ""
Write-Host "TIME_TO_FIRST_VALUE target: <=15 minutes for a prepared Windows stranger machine." -ForegroundColor Cyan
}
catch {
    if ("$($_.Exception.Message)" -notmatch "^ATLAS_START_ABORT:") {
        Write-Host "START ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
}
finally {
    if (-not $atlasStartCompleted) {
        Write-Host "SEC-026: start incomplete - cleaning verified session processes..." -ForegroundColor Yellow
        $cleanup = Invoke-AtlasStartAbortCleanup -ProcessRecords $processRecords -SessionNonce $sessionNonce -PidFile $pidFile
        if ($cleanup.ORPHAN_PROCESS_COUNT -ne 0) {
            Write-Host "SEC-026 FAIL: ORPHAN_PROCESS_COUNT=$($cleanup.ORPHAN_PROCESS_COUNT)" -ForegroundColor Red
            exit 1
        }
        exit 1
    }
}
exit 0
