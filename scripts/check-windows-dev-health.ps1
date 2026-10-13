param()

$ErrorActionPreference = "Stop"

function Invoke-Safe {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)
    try {
        return & $Action
    } catch {
        return $null
    }
}

function Test-PathSafe {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [bool](Test-Path -LiteralPath $Path)
}

function Probe-Tool {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Args,
        [Parameter(Mandatory = $true)][bool]$Required,
        [string]$ExpectedVersion = ""
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        return [ordered]@{
            status = $(if ($Required) { "missing" } else { "optional_missing" })
            required = $Required
            command = $Name
            expected_version = $ExpectedVersion
            observed = $null
            exit_code = 127
            error = "command_not_found"
        }
    }

    try {
        $output = & $Name @Args 2>&1
        $exitCode = $LASTEXITCODE
        $observed = ($output | Select-Object -First 1).ToString().Trim()

        if ($exitCode -ne 0) {
            return [ordered]@{
                status = "probe_failed"
                required = $Required
                command = $Name
                expected_version = $ExpectedVersion
                observed = $observed
                exit_code = $exitCode
                error = "nonzero_exit"
            }
        }

        if ($ExpectedVersion -and $observed -notmatch [regex]::Escape($ExpectedVersion)) {
            return [ordered]@{
                status = "version_drift"
                required = $Required
                command = $Name
                expected_version = $ExpectedVersion
                observed = $observed
                exit_code = 0
                error = $null
            }
        }

        return [ordered]@{
            status = "healthy"
            required = $Required
            command = $Name
            expected_version = $ExpectedVersion
            observed = $observed
            exit_code = 0
            error = $null
        }
    } catch {
        return [ordered]@{
            status = "probe_failed"
            required = $Required
            command = $Name
            expected_version = $ExpectedVersion
            observed = $null
            exit_code = -1
            error = $_.Exception.Message
        }
    }
}

$osCaption = Invoke-Safe { (Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption) }
$osVersion = Invoke-Safe { (Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Version) }
$osBuild = Invoke-Safe { (Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty BuildNumber) }

$toolSpecs = @(
    @{ Name = "git"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "gh"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "py"; Args = @("-3.12", "--version"); Required = $true; Expected = "3.12" },
    @{ Name = "uv"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "node"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "npm"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "npx"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "pnpm"; Args = @("--version"); Required = $true; Expected = "" },
    @{ Name = "docker"; Args = @("--version"); Required = $false; Expected = "" },
    @{ Name = "winget"; Args = @("--version"); Required = $false; Expected = "" },
    @{ Name = "codebase-memory-mcp"; Args = @("--version"); Required = $true; Expected = "0.10.8" },
    @{ Name = "npx"; Args = @("-y", "@playwright/mcp@0.0.80", "--help"); Required = $true; Expected = "" },
    @{ Name = "npx"; Args = @("-y", "@upstash/context7-mcp@4.0.5", "--help"); Required = $false; Expected = "" },
    @{ Name = "semgrep"; Args = @("--version"); Required = $true; Expected = "1.176.1" },
    @{ Name = "pip-audit"; Args = @("--version"); Required = $true; Expected = "2.10.1" },
    @{ Name = "yamllint"; Args = @("--version"); Required = $true; Expected = "1.38.0" },
    @{ Name = "gitleaks"; Args = @("version"); Required = $true; Expected = "8.30.1" },
    @{ Name = "trivy"; Args = @("--version"); Required = $true; Expected = "0.74.0" },
    @{ Name = "syft"; Args = @("version"); Required = $true; Expected = "1.51.1" },
    @{ Name = "grype"; Args = @("version"); Required = $true; Expected = "0.118.0" },
    @{ Name = "actionlint"; Args = @("-version"); Required = $true; Expected = "1.7.12" },
    @{ Name = "hadolint"; Args = @("--version"); Required = $true; Expected = "2.15.1" },
    @{ Name = "taplo"; Args = @("--version"); Required = $false; Expected = "0.10.0" },
    @{ Name = "markdownlint-cli2"; Args = @("--version"); Required = $false; Expected = "0.23.2" },
    @{ Name = "pre-commit"; Args = @("--version"); Required = $false; Expected = "4.6.2" }
)

$tools = [ordered]@{}
foreach ($spec in $toolSpecs) {
    $toolKey = if ($spec.Args[0] -eq "-y" -and $spec.Args.Count -gt 1) { "$($spec.Name)::$($spec.Args[1])" } else { $spec.Name }
    $tools[$toolKey] = Probe-Tool -Name $spec.Name -Args $spec.Args -Required $spec.Required -ExpectedVersion $spec.Expected
}

$requiredFailures = @($tools.Values | Where-Object { $_.required -and $_.status -ne "healthy" })
$optionalIssues = @($tools.Values | Where-Object { -not $_.required -and $_.status -ne "healthy" })
$ghAuthStatus = "gh-missing"
if (Get-Command gh -ErrorAction SilentlyContinue) {
    try {
        $ghAuthStatus = (& gh auth status 2>&1 | Select-Object -First 1).ToString().Trim()
    } catch {
        $ghAuthStatus = "probe_failed"
    }
}

$report = [ordered]@{
    status = if ($requiredFailures.Count -eq 0) { "healthy" } elseif ($requiredFailures.Count -lt 4) { "partial" } else { "fail" }
    host = [ordered]@{
        os = $osCaption
        version = $osVersion
        build = $osBuild
        architecture = $env:PROCESSOR_ARCHITECTURE
        powershell = $PSVersionTable.PSVersion.ToString()
    }
    paths = [ordered]@{
        userprofile = $env:USERPROFILE
        temp = $env:TEMP
        localappdata = $env:LOCALAPPDATA
        appdata = $env:APPDATA
        recommended_worktree_root = "D:\atlas-worktrees"
    }
    tools = $tools
    mcp_config = [ordered]@{
        copilot = if (Test-PathSafe "$($env:USERPROFILE)\.copilot\mcp-config.json") { "configured" } else { "not_configured" }
        vscode = if (Test-PathSafe "$($env:APPDATA)\Code\User\mcp.json") { "configured" } else { "not_configured" }
        cursor = if (Test-PathSafe "$($env:USERPROFILE)\.cursor\mcp.json") { "configured" } else { "not_configured" }
        github_wrapper = if (Test-PathSafe "$($env:USERPROFILE)\.local\bin\github-mcp-wrapper.ps1") { "configured" } else { "not_configured" }
    }
    summary = [ordered]@{
        required_issue_count = $requiredFailures.Count
        optional_issue_count = $optionalIssues.Count
    }
    github_auth = [ordered]@{
        gh_auth_status = $ghAuthStatus
    }
}

$report | ConvertTo-Json -Depth 6
