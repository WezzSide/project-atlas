from pathlib import Path


def test_bootstrap_script_does_not_use_latest_release_resolution() -> None:
    script = Path("scripts/bootstrap-dev-tooling.sh").read_text()
    assert "/releases/latest" not in script
    assert "@latest" not in script


def test_bootstrap_script_pins_versions_for_all_install_targets() -> None:
    script = Path("scripts/bootstrap-dev-tooling.sh").read_text()
    expected_markers = [
        "PLAYWRIGHT_MCP_VERSION=",
        "CONTEXT7_MCP_VERSION=",
        "MARKDOWNLINT_CLI2_VERSION=",
        "GITLEAKS_VERSION=",
        "TRIVY_VERSION=",
        "SYFT_VERSION=",
        "GRYPE_VERSION=",
        "TAPLO_VERSION=",
        "ACTIONLINT_VERSION=",
    ]
    for marker in expected_markers:
        assert marker in script


def test_bootstrap_script_verifies_taplo_download_checksum() -> None:
    script = Path("scripts/bootstrap-dev-tooling.sh").read_text()
    assert "taplo_SHA256" in script


def test_bootstrap_script_separates_npm_identity_from_version() -> None:
    script = Path("scripts/bootstrap-dev-tooling.sh").read_text()
    assert 'install_npm "@playwright/mcp" "${PLAYWRIGHT_MCP_VERSION}"' in script
    assert 'install_npm "@upstash/context7-mcp" "${CONTEXT7_MCP_VERSION}"' in script
    assert 'install_npm "markdownlint-cli2" "${MARKDOWNLINT_CLI2_VERSION}"' in script
    assert 'npm list -g --depth=0 "${pkg_name}"' in script
    assert 'npm list -g --depth=0 "${pkg_name}@${pkg_version}"' in script


def test_bootstrap_script_attempts_codebase_memory_install() -> None:
    script = Path("scripts/bootstrap-dev-tooling.sh").read_text()
    assert 'install_npm "codebase-memory-mcp"' in script
