param(
    [switch]$Check,
    [switch]$DryRun,
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$ExitOk = 0
$ExitUsage = 2
$ExitRequiredMissing = 3
$ExitInstallFailed = 4
$ExitHardFailure = 10

if (-not ($Check -or $DryRun -or $Install)) {
    $Check = $true
}

$modeCount = @($Check, $DryRun, $Install | Where-Object { $_ }).Count
if ($modeCount -gt 1) {
    Write-Host "usage: .\scripts\bootstrap-dev-tooling.ps1 [-Check|-DryRun|-Install]"
    exit $ExitUsage
}

$Mode = if ($Install) { "Install" } elseif ($DryRun) { "DryRun" } else { "Check" }
Write-Host "mode=$Mode"

$Pinned = [ordered]@{
    "codebase-memory-mcp" = "0.10.8"
    "@playwright/mcp" = "0.0.80"
    "@upstash/context7-mcp" = "4.0.5"
    "markdownlint-cli2" = "0.23.2"
}

$Script:Results = [System.Collections.Generic.List[object]]::new()
$Script:Mutated = $false
$Script:HardFailure = $null

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Add-Result {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$Required,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Message = ""
    )
    $entry = [ordered]@{
        name = $Name
        required = $Required
        status = $Status
        message = $Message
    }
    $Script:Results.Add($entry)
    if ($Message) {
        Write-Host "${Status}: $Name ($Message)"
        return
    }
    Write-Host "${Status}: $Name"
}

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        if ($Mode -eq "Install") {
            New-Item -ItemType Directory -Path $Path -Force | Out-Null
            $Script:Mutated = $true
        } else {
            Add-Result -Name "directory::$Path" -Required $true -Status "not_configured" -Message "missing_directory"
        }
    }
}

function Get-PathRoot {
    param([Parameter(Mandatory = $true)][string]$Preferred, [string]$Fallback = "")
    $value = [Environment]::GetEnvironmentVariable($Preferred)
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        return $value
    }
    if ($Fallback) {
        $fallbackValue = [Environment]::GetEnvironmentVariable($Fallback)
        if (-not [string]::IsNullOrWhiteSpace($fallbackValue)) {
            return $fallbackValue
        }
    }
    return $null
}

function Test-VersionProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Args,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )
    try {
        $output = & $Name @Args 2>&1
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            return [ordered]@{ status = "probe_failed"; message = "version_exit_$exitCode" }
        }
        $line = ($output | Select-Object -First 1).ToString().Trim()
        if (-not $line) {
            return [ordered]@{ status = "probe_failed"; message = "empty_version_output" }
        }
        if ($line -match [regex]::Escape($ExpectedVersion)) {
            return [ordered]@{ status = "healthy"; message = $line }
        }
        if ($line -match "[0-9]+(\.[0-9]+){1,3}") {
            return [ordered]@{ status = "version_drift"; message = $line }
        }
        return [ordered]@{ status = "probe_failed"; message = "unparseable_version_output: $line" }
    } catch {
        return [ordered]@{ status = "probe_failed"; message = $_.Exception.Message }
    }
}

function Install-NpmPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][bool]$Required
    )
    $spec = "$Name@$Version"
    if (-not (Test-Command "npm")) {
        Add-Result -Name $spec -Required $Required -Status $(if ($Required) { "missing" } else { "optional_missing" }) -Message "npm_not_found"
        return
    }

    try {
        $hasName = (& npm list -g --depth=0 $Name 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and $hasName -match [regex]::Escape($spec)) {
            Add-Result -Name $spec -Required $Required -Status "healthy"
            return
        }
    } catch {
        Add-Result -Name $spec -Required $Required -Status "probe_failed" -Message $_.Exception.Message
        return
    }

    if ($Mode -ne "Install") {
        Add-Result -Name $spec -Required $Required -Status $(if ($Required) { "missing" } else { "optional_missing" }) -Message "install_required"
        return
    }

    try {
        & npm install -g $spec --no-fund --no-audit
        if ($LASTEXITCODE -eq 0) {
            $Script:Mutated = $true
            Add-Result -Name $spec -Required $Required -Status "installed"
        } else {
            Add-Result -Name $spec -Required $Required -Status "install_failed" -Message "npm_exit_$LASTEXITCODE"
        }
    } catch {
        Add-Result -Name $spec -Required $Required -Status "install_failed" -Message $_.Exception.Message
    }
}

function Install-PipxPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Package,
        [Parameter(Mandatory = $true)][string]$Binary,
        [Parameter(Mandatory = $true)][bool]$Required,
        [Parameter(Mandatory = $true)][string[]]$VersionArgs,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )
    if (Test-Command $Binary) {
        $probe = Test-VersionProbe -Name $Binary -Args $VersionArgs -ExpectedVersion $ExpectedVersion
        if ($probe.status -eq "healthy") {
            Add-Result -Name $Package -Required $Required -Status "healthy" -Message $probe.message
            return
        }
        if ($Mode -ne "Install") {
            Add-Result -Name $Package -Required $Required -Status $probe.status -Message $probe.message
            return
        }
    }
    if (-not (Test-Command "pipx")) {
        Add-Result -Name $Package -Required $Required -Status $(if ($Required) { "missing" } else { "optional_missing" }) -Message "pipx_not_found"
        return
    }

    if ($Mode -ne "Install") {
        Add-Result -Name $Package -Required $Required -Status $(if ($Required) { "missing" } else { "optional_missing" }) -Message "install_required"
        return
    }

    try {
        & pipx install --force $Package
        if ($LASTEXITCODE -eq 0) {
            if (Test-Command $Binary) {
                $probe = Test-VersionProbe -Name $Binary -Args $VersionArgs -ExpectedVersion $ExpectedVersion
                if ($probe.status -eq "healthy") {
                    $Script:Mutated = $true
                    Add-Result -Name $Package -Required $Required -Status "installed" -Message $probe.message
                    return
                }
                Add-Result -Name $Package -Required $Required -Status "install_failed" -Message $probe.message
                return
            }
            Add-Result -Name $Package -Required $Required -Status "install_failed" -Message "binary_missing_after_install"
        } else {
            Add-Result -Name $Package -Required $Required -Status "install_failed" -Message "pipx_exit_$LASTEXITCODE"
        }
    } catch {
        Add-Result -Name $Package -Required $Required -Status "install_failed" -Message $_.Exception.Message
    }
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][bool]$Required,
        [Parameter(Mandatory = $true)][string[]]$VersionArgs,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )
    if (Test-Command $CommandName) {
        $probe = Test-VersionProbe -Name $CommandName -Args $VersionArgs -ExpectedVersion $ExpectedVersion
        if ($probe.status -eq "healthy") {
            Add-Result -Name $Id -Required $Required -Status "healthy" -Message $probe.message
            return
        }
        if ($Mode -ne "Install") {
            Add-Result -Name $Id -Required $Required -Status $probe.status -Message $probe.message
            return
        }
    }
    if ($Mode -ne "Install") {
        Add-Result -Name $Id -Required $Required -Status $(if ($Required) { "missing" } else { "optional_missing" }) -Message "install_required"
        return
    }
    if (-not (Test-Command "winget")) {
        Add-Result -Name $Id -Required $Required -Status $(if ($Required) { "missing" } else { "optional_missing" }) -Message "winget_not_found"
        return
    }

    try {
        & winget install --id $Id --exact --version $ExpectedVersion --scope user --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) {
            if (Test-Command $CommandName) {
                $probe = Test-VersionProbe -Name $CommandName -Args $VersionArgs -ExpectedVersion $ExpectedVersion
                if ($probe.status -eq "healthy") {
                    $Script:Mutated = $true
                    Add-Result -Name $Id -Required $Required -Status "installed" -Message $probe.message
                    return
                }
                Add-Result -Name $Id -Required $Required -Status "install_failed" -Message $probe.message
                return
            }
            Add-Result -Name $Id -Required $Required -Status "install_failed" -Message "binary_missing_after_install"
        } else {
            Add-Result -Name $Id -Required $Required -Status "install_failed" -Message "winget_exit_$LASTEXITCODE"
        }
    } catch {
        Add-Result -Name $Id -Required $Required -Status "install_failed" -Message $_.Exception.Message
    }
}

function Ensure-GitHubWrapper {
    $profileRoot = Get-PathRoot -Preferred "USERPROFILE" -Fallback "HOME"
    if (-not $profileRoot) {
        Add-Result -Name "github-mcp-wrapper.ps1" -Required $true -Status "not_configured" -Message "user_profile_unavailable"
        return $null
    }
    $wrapperPath = Join-Path $profileRoot ".local\\bin\\github-mcp-wrapper.ps1"
    if (Test-Path -LiteralPath $wrapperPath) {
        Add-Result -Name "github-mcp-wrapper.ps1" -Required $true -Status "healthy"
        return $wrapperPath
    }

    if ($Mode -ne "Install") {
        Add-Result -Name "github-mcp-wrapper.ps1" -Required $true -Status "missing" -Message "install_required"
        return $wrapperPath
    }

    try {
        Ensure-Directory -Path (Split-Path -Parent $wrapperPath)
        $wrapper = @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$McpArgs)
$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "gh CLI is required for runtime authentication."
    exit 1
}

$token = (& gh auth token 2>$null)
if (-not $token) {
    Write-Error "gh auth token unavailable. Run: gh auth login"
    exit 1
}

$env:GITHUB_TOKEN = $token
try {
    if (Get-Command github-mcp-server -ErrorAction SilentlyContinue) {
        & github-mcp-server @McpArgs
        exit $LASTEXITCODE
    }
    if (Get-Command npx -ErrorAction SilentlyContinue) {
        & npx -y github-mcp-server@1.12.0 @McpArgs
        exit $LASTEXITCODE
    }
    Write-Error "github-mcp-server unavailable. Install it or ensure npx is present."
    exit 1
} finally {
    Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
}
'@
        Set-Content -Path $wrapperPath -Value $wrapper -Encoding UTF8
        $Script:Mutated = $true
        Add-Result -Name "github-mcp-wrapper.ps1" -Required $true -Status "installed"
        return $wrapperPath
    } catch {
        Add-Result -Name "github-mcp-wrapper.ps1" -Required $true -Status "install_failed" -Message $_.Exception.Message
        return $wrapperPath
    }
}

function Ensure-McpConfigEntry {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$ServerName,
        [Parameter(Mandatory = $true)][hashtable]$Definition,
        [Parameter(Mandatory = $true)][bool]$Required
    )
    $displayName = "mcp::${ConfigPath}::${ServerName}"
    $existing = $null
    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $existing = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json -AsHashtable
        } catch {
            Add-Result -Name $displayName -Required $Required -Status "probe_failed" -Message "invalid_json"
            return
        }
    } else {
        $existing = @{}
    }

    if (-not $existing.ContainsKey("mcpServers")) {
        $existing["mcpServers"] = @{}
    }

    $hasEntry = $existing["mcpServers"].ContainsKey($ServerName)
    $isMatch = $false
    if ($hasEntry) {
        $current = $existing["mcpServers"][$ServerName] | ConvertTo-Json -Depth 8 -Compress
        $target = $Definition | ConvertTo-Json -Depth 8 -Compress
        $isMatch = ($current -eq $target)
    }

    if ($hasEntry -and $isMatch) {
        Add-Result -Name $displayName -Required $Required -Status "healthy"
        return
    }

    if ($Mode -ne "Install") {
        Add-Result -Name $displayName -Required $Required -Status "not_configured" -Message "entry_missing_or_drifted"
        return
    }

    try {
        Ensure-Directory -Path (Split-Path -Parent $ConfigPath)
        $existing["mcpServers"][$ServerName] = $Definition
        $json = $existing | ConvertTo-Json -Depth 10
        Set-Content -Path $ConfigPath -Value $json -Encoding UTF8
        $Script:Mutated = $true
        Add-Result -Name $displayName -Required $Required -Status "installed"
    } catch {
        Add-Result -Name $displayName -Required $Required -Status "install_failed" -Message $_.Exception.Message
    }
}

foreach ($kv in $Pinned.GetEnumerator()) {
    $required = $true
    if ($kv.Key -eq "@upstash/context7-mcp" -or $kv.Key -eq "markdownlint-cli2") {
        $required = $false
    }
    Install-NpmPackage -Name $kv.Key -Version $kv.Value -Required $required
}

Install-NpmPackage -Name "github-mcp-server" -Version "1.12.0" -Required $true
Install-PipxPackage -Package "semgrep==1.176.1" -Binary "semgrep" -Required $true -VersionArgs @("--version") -ExpectedVersion "1.176.1"
Install-PipxPackage -Package "pip-audit==2.10.1" -Binary "pip-audit" -Required $true -VersionArgs @("--version") -ExpectedVersion "2.10.1"
Install-PipxPackage -Package "yamllint==1.38.0" -Binary "yamllint" -Required $true -VersionArgs @("--version") -ExpectedVersion "1.38.0"
Install-PipxPackage -Package "pre-commit==4.6.2" -Binary "pre-commit" -Required $false -VersionArgs @("--version") -ExpectedVersion "4.6.2"

Install-WingetPackage -Id "Gitleaks.Gitleaks" -CommandName "gitleaks" -Required $true -VersionArgs @("version") -ExpectedVersion "8.30.1"
Install-WingetPackage -Id "AquaSecurity.Trivy" -CommandName "trivy" -Required $true -VersionArgs @("--version") -ExpectedVersion "0.74.0"
Install-WingetPackage -Id "Anchore.Syft" -CommandName "syft" -Required $true -VersionArgs @("version") -ExpectedVersion "1.51.1"
Install-WingetPackage -Id "Anchore.Grype" -CommandName "grype" -Required $true -VersionArgs @("version") -ExpectedVersion "0.118.0"
Install-WingetPackage -Id "rhysd.actionlint" -CommandName "actionlint" -Required $true -VersionArgs @("-version") -ExpectedVersion "1.7.12"
Install-WingetPackage -Id "tamasfe.taplo" -CommandName "taplo" -Required $false -VersionArgs @("--version") -ExpectedVersion "0.10.0"
Install-WingetPackage -Id "hadolint.hadolint" -CommandName "hadolint" -Required $true -VersionArgs @("--version") -ExpectedVersion "2.15.1"

$wrapperPath = Ensure-GitHubWrapper
$mcpDefinitions = [ordered]@{
    "codebase-memory" = @{ command = "codebase-memory-mcp"; args = @() }
    "github" = @{ command = "pwsh"; args = @("-NoProfile", "-File", $(if ($wrapperPath) { $wrapperPath } else { "github-mcp-wrapper.ps1" })) }
    "playwright" = @{ command = "npx"; args = @("-y", "@playwright/mcp@0.0.80", "--headless") }
    "context7" = @{ command = "npx"; args = @("-y", "@upstash/context7-mcp@4.0.5") }
}

$profileRoot = Get-PathRoot -Preferred "USERPROFILE" -Fallback "HOME"
$appDataRoot = Get-PathRoot -Preferred "APPDATA"
$configPaths = @()
if ($profileRoot) {
    $configPaths += (Join-Path $profileRoot ".copilot\\mcp-config.json")
    $configPaths += (Join-Path $profileRoot ".cursor\\mcp.json")
} else {
    Add-Result -Name "mcp::profile-root" -Required $true -Status "not_configured" -Message "user_profile_unavailable"
}
if ($appDataRoot) {
    $configPaths += (Join-Path $appDataRoot "Code\\User\\mcp.json")
} else {
    Add-Result -Name "mcp::vscode-config" -Required $true -Status "not_configured" -Message "appdata_unavailable"
}

foreach ($cfg in $configPaths) {
    Ensure-McpConfigEntry -ConfigPath $cfg -ServerName "codebase-memory" -Definition $mcpDefinitions["codebase-memory"] -Required $true
    Ensure-McpConfigEntry -ConfigPath $cfg -ServerName "github" -Definition $mcpDefinitions["github"] -Required $true
    Ensure-McpConfigEntry -ConfigPath $cfg -ServerName "playwright" -Definition $mcpDefinitions["playwright"] -Required $true
    Ensure-McpConfigEntry -ConfigPath $cfg -ServerName "context7" -Definition $mcpDefinitions["context7"] -Required $false
}

$requiredIssues = @($Script:Results | Where-Object { $_.required -and $_.status -in @("missing", "version_drift", "not_configured", "probe_failed", "install_failed") }).Count
$installFailures = @($Script:Results | Where-Object { $_.status -eq "install_failed" }).Count
$optionalIssues = @($Script:Results | Where-Object { -not $_.required -and $_.status -ne "healthy" -and $_.status -ne "installed" }).Count

Write-Host "required_issue_count=$requiredIssues"
Write-Host "optional_issue_count=$optionalIssues"
Write-Host "install_failure_count=$installFailures"
Write-Host "mutated=$Script:Mutated"

if ($Script:HardFailure) {
    exit $ExitHardFailure
}

if ($Mode -eq "Install") {
    if ($installFailures -gt 0 -or $requiredIssues -gt 0) {
        exit $ExitInstallFailed
    }
    exit $ExitOk
}

if ($requiredIssues -gt 0) {
    exit $ExitRequiredMissing
}

exit $ExitOk
