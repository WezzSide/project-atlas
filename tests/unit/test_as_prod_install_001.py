"""AS-PROD-INSTALL-001 — Windows stranger bootstrap presence + script contracts.

No network. Asserts scripts/docs exist and carry honesty + health tokens.
Package: PRODUCTIZATION / NOT RELEASE / NOT PILOT.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts" / "windows"
_DOCS = _REPO_ROOT / "docs" / "productization" / "install"

_REQUIRED_SCRIPTS = (
    "atlas-start.ps1",
    "atlas-preflight.ps1",
    "atlas-stop.ps1",
    "_AtlasCommon.ps1",
)

_REQUIRED_DOCS = (
    "README.md",
    "STRANGER.md",
    "OPERATOR.md",
    "LIMITATIONS.md",
)

_START_TOKENS = (
    "STRANGER",
    "NOT RELEASE",
    "PRODUCTIZATION",
    "NOT PILOT",
    "health",
    "/v1/meta",
    "WHAT:",
    "CAUSE:",
    "ACTION:",
    "RETRY:",
    "pip install -e",
    "127.0.0.1",
    ".tmp/productization",
    "api-serve",
    "STRANGER_CAN_START_ATLAS",
    "Test-AtlasPortFree",
    "Test-AtlasProcessOwnsPort",
    "Test-AtlasCliTipCompatible",
    "Ensure-AtlasEditableInstall",
    "Editable project location",
    "missing_live_subcommand",
    "tip-incompatible",
    "live --help",
    "ATLAS_CORS_ORIGIN",
    "cors_origin",
    "PROD-ADV-011",
)

_PREFLIGHT_TOKENS = (
    "Python",
    "npm",
    "NOT RELEASE",
    "PRODUCTIZATION",
    "WHAT:",
    "CAUSE:",
    "ACTION:",
    "RETRY:",
    ".tmp",
)

_DOC_TOKENS = (
    "STRANGER",
    "NOT RELEASE",
    "PRODUCTIZATION",
    "health",
    "TIME_TO_FIRST_VALUE",
    "STRANGER_CAN_START_ATLAS",
)


def test_as_prod_install_001_scripts_present() -> None:
    assert _SCRIPTS.is_dir(), f"missing {_SCRIPTS}"
    for name in _REQUIRED_SCRIPTS:
        path = _SCRIPTS / name
        assert path.is_file(), f"missing script {path}"


def test_as_prod_install_001_docs_present() -> None:
    assert _DOCS.is_dir(), f"missing {_DOCS}"
    for name in _REQUIRED_DOCS:
        path = _DOCS / name
        assert path.is_file(), f"missing doc {path}"


def test_as_prod_install_001_start_script_contract() -> None:
    text = (_SCRIPTS / "atlas-start.ps1").read_text(encoding="utf-8")
    for token in _START_TOKENS:
        assert token in text, f"atlas-start.ps1 missing token {token!r}"
    # Must not claim release/pilot success stamps.
    assert "RELEASE CERTIFIED = YES" not in text
    assert "PILOT PASS = YES" not in text
    assert "ALPHA_READY=YES" not in text
    # Must not pull Playwright into this path (honesty mentions are OK).
    lowered = text.lower()
    assert "npx playwright" not in lowered
    assert "@playwright" not in lowered
    assert "playwright install" not in lowered
    assert "npm install playwright" not in lowered
    assert (
        "without Playwright" in text
        or "does **not** add Playwright" in text
        or "Playwright is intentionally not" in text
    )


def test_as_prod_install_001_cors_matches_webport_contract() -> None:
    """PROD-ADV-011: launcher must set ATLAS_CORS_ORIGIN from -WebPort and verify meta."""
    text = (_SCRIPTS / "atlas-start.ps1").read_text(encoding="utf-8")
    assert (
        "ATLAS_CORS_ORIGIN = $corsOrigin" in text or "$env:ATLAS_CORS_ORIGIN = $corsOrigin" in text
    )
    assert "http://127.0.0.1:$WebPort" in text
    assert "cors_origin_mismatch" in text
    assert "PROD-ADV-011" in text
    # Must not pin CORS solely to literal 5173 after WebPort is known.
    assert "Starting LIVE_API on 127.0.0.1:$ApiPort (CORS_ORIGIN=$corsOrigin)" in text


def test_as_prod_install_001_stale_cli_guard_contract() -> None:
    """I03 CRITICAL: refuse stale atlas.exe; reinstall from RepoRoot; name missing live."""
    text = (_SCRIPTS / "atlas-start.ps1").read_text(encoding="utf-8")
    assert "function Test-AtlasCliTipCompatible" in text
    assert "function Get-AtlasPipShowField" in text
    assert "function Install-AtlasEditableFromRepo" in text
    assert "editable_wrong_worktree" in text
    assert "not_editable_from_repo" in text
    assert "tip-incompatible" in text
    assert "invalid choice" in text
    # SkipInstall must surface structured product error for incompatible CLI.
    assert "SkipInstall prevents repair" in text
    # Must not short-circuit on any existing atlas.exe without tip checks.
    assert "OK  atlas matched to" not in text
    assert "OK  atlas tip-compatible" in text
    # Health-path product error must name missing live, not only meta connect.
    assert "lacks the 'live' subcommand" in text


def test_as_prod_install_001_preflight_script_contract() -> None:
    text = (_SCRIPTS / "atlas-preflight.ps1").read_text(encoding="utf-8")
    for token in _PREFLIGHT_TOKENS:
        assert token in text, f"atlas-preflight.ps1 missing token {token!r}"


def test_as_prod_install_001_common_product_error_helper() -> None:
    text = (_SCRIPTS / "_AtlasCommon.ps1").read_text(encoding="utf-8")
    assert "Write-AtlasProductError" in text
    assert "WHAT:" in text
    assert "CAUSE:" in text
    assert "ACTION:" in text
    assert "RETRY:" in text
    assert "NOT RELEASE" in text
    assert "Wait-AtlasHttpOk" in text
    assert "Test-AtlasPortFree" in text
    assert "Test-AtlasProcessOwnsPort" in text
    # PROD-ADV-009: ownership must walk npm -> vite grandchildren, not direct children only.
    assert "Get-AtlasDescendantProcessIds" in text
    assert "MaxDepth" in text
    # Invoke-AtlasPython must not leak pip stdout into the returned exit code
    # (PowerShell assignment capture → false install failure).
    assert "Isolate exit code from stdout/stderr" in text
    assert "[int]$LASTEXITCODE" in text
    assert "Write-Host" in text
    # ENV-ISO-002: tip-local .venv preference + venv-aware Scripts resolution.
    assert "Get-AtlasTipVenvPythonPath" in text
    assert "tip-venv (.venv\\Scripts\\python.exe)" in text or "tip-venv" in text
    assert "TipLocal" in text
    assert "function Resolve-AtlasScriptsDir" in text
    assert 'leaf.Equals("Scripts"' in text or 'Equals("Scripts"' in text
    assert "Test-AtlasPythonPathTipSafe" in text
    assert "pythonpath_foreign_shadow" in text
    assert "Test-AtlasImportLocationTipSafe" in text
    assert "import_wrong_worktree" in text


def test_as_prod_install_001_env_iso_start_fail_closed_contract() -> None:
    """ENV-ISO-001/002: tip .venv prefer; no global rewrite; PYTHONPATH/import fail-closed."""
    start = (_SCRIPTS / "atlas-start.ps1").read_text(encoding="utf-8")
    common = (_SCRIPTS / "_AtlasCommon.ps1").read_text(encoding="utf-8")
    assert "Ensure-AtlasTipLocalVenv" in start
    assert "Get-AtlasPythonCommand -RepoRoot" in start
    assert "Refusing editable install into a non-tip interpreter" in start
    assert "ENV-ISO-002" in start
    assert "ENV-ISO-001" in start
    assert "pythonpath_foreign_shadow" in start or "pythonpath_foreign_shadow" in common
    assert "import_wrong_worktree" in start or "import_wrong_worktree" in common
    assert "no global editable rewrite" in start
    # Must not keep the old Scripts\\Scripts footgun as sole resolver in start.
    assert "function Resolve-AtlasScriptsDir" not in start
    assert "function Resolve-AtlasScriptsDir" in common
    assert "function Test-AtlasPathUnderRoot" in common
    preflight = (_SCRIPTS / "atlas-preflight.ps1").read_text(encoding="utf-8")
    assert "Get-AtlasPythonCommand -RepoRoot" in preflight
    assert "tip_local" in preflight


def test_as_prod_install_001_docs_include_adv_findings_when_present() -> None:
    """ADV findings doc is optional on tip; when present must keep honesty tokens."""
    adv = _DOCS / "ADV-FINDINGS.md"
    if not adv.is_file():
        return
    text = adv.read_text(encoding="utf-8")
    assert "PROD-FINDING-" in text
    assert "STRANGER_CAN_START_ATLAS" in text
    assert "NOT RELEASE" in text
    assert "PRODUCTIZATION" in text
    assert "RELEASE CERTIFIED = YES" not in text
    assert "PILOT PASS = YES" not in text
    assert "ALPHA_READY=YES" not in text


def test_as_prod_install_001_docs_honesty_and_stranger_tokens() -> None:
    joined = "\n".join((_DOCS / name).read_text(encoding="utf-8") for name in _REQUIRED_DOCS)
    for token in _DOC_TOKENS:
        assert token in joined, f"install docs missing token {token!r}"
    assert "NOT PILOT" in joined or "NOT PILOT PASS" in joined
    assert "MSI" in joined
    assert "winget" in joined.lower() or "winget" in joined
    stranger = (_DOCS / "STRANGER.md").read_text(encoding="utf-8")
    assert "atlas-start.ps1" in stranger
    assert "STRANGER" in stranger
