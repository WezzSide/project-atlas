from pathlib import Path


def test_windows_bootstrap_script_supports_expected_modes() -> None:
    script = Path("scripts/bootstrap-dev-tooling.ps1").read_text()
    assert "-Check" in script
    assert "-DryRun" in script
    assert "-Install" in script


def test_windows_bootstrap_script_pins_core_mcp_versions() -> None:
    script = Path("scripts/bootstrap-dev-tooling.ps1").read_text()
    assert '"codebase-memory-mcp" = "0.10.8"' in script
    assert '"@playwright/mcp" = "0.0.80"' in script
    assert '"@upstash/context7-mcp" = "4.0.5"' in script


def test_windows_bootstrap_script_avoids_plaintext_github_token() -> None:
    script = Path("scripts/bootstrap-dev-tooling.ps1").read_text()
    assert "ghp_" not in script
    assert "github_pat_" not in script
    assert "sk-" not in script
    assert "AKIA" not in script


def test_windows_health_script_is_read_only_report() -> None:
    script = Path("scripts/check-windows-dev-health.ps1").read_text()
    assert "ConvertTo-Json" in script
    assert "gh auth status" in script
    assert "status = \"healthy\"" in script
    assert "status = \"probe_failed\"" in script


def test_windows_health_script_handles_probe_failures_without_abort() -> None:
    script = Path("scripts/check-windows-dev-health.ps1").read_text()
    assert "try {" in script
    assert "catch {" in script
    assert "optional_missing" in script
    assert "version_drift" in script


def test_windows_bootstrap_script_has_exit_contract() -> None:
    script = Path("scripts/bootstrap-dev-tooling.ps1").read_text()
    assert "$ExitRequiredMissing = 3" in script
    assert "$ExitInstallFailed = 4" in script
    assert "required_issue_count=" in script


def test_windows_bootstrap_script_writes_github_wrapper_and_mcp_entries() -> None:
    script = Path("scripts/bootstrap-dev-tooling.ps1").read_text()
    assert "github-mcp-wrapper.ps1" in script
    assert "mcpServers" in script
    assert "entry_missing_or_drifted" in script


def test_windows_bootstrap_marks_context7_optional() -> None:
    script = Path("scripts/bootstrap-dev-tooling.ps1").read_text()
    assert "@upstash/context7-mcp" in script
    assert 'if ($kv.Key -eq "@upstash/context7-mcp" -or $kv.Key -eq "markdownlint-cli2")' in script
