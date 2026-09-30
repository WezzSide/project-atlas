"""AS-PROD-ONBOARD-001 — first-run onboard docs + thin helper presence tests.

No network. Asserts docs/scripts exist and carry honesty + chain tokens.
Doctor CLI is out of scope here (Cloud #254). Package: PRODUCTIZATION /
NOT RELEASE / NOT PILOT. ALPHA_READY must remain unset/NO.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts" / "windows"
_DOCS = _REPO_ROOT / "docs" / "productization" / "onboard"
_INSTALL_DOCS = _REPO_ROOT / "docs" / "productization" / "install"

_REQUIRED_DOCS = (
    "README.md",
    "FIRST-RUN.md",
    "HONESTY.md",
    "CHECKLIST.md",
)

_DOC_TOKENS = (
    "AS-PROD-ONBOARD-001",
    "PRODUCTIZATION",
    "NOT RELEASE",
    "NOT PILOT",
    "ALPHA_READY",
    "preflight",
    "atlas-start.ps1",
    "atlas-preflight.ps1",
    "doctor",
    "STRANGER",
)

_ONBOARD_SCRIPT_TOKENS = (
    "AS-PROD-ONBOARD-001",
    "PRODUCTIZATION",
    "NOT RELEASE",
    "NOT PILOT",
    "ALPHA_READY",
    "atlas-preflight.ps1",
    "atlas-start.ps1",
    "NOT IMPLEMENTED",
    "#254",
    "Playwright",
    "WHAT:",
    "CAUSE:",
    "ACTION:",
    "RETRY:",
)


def test_as_prod_onboard_001_docs_present() -> None:
    assert _DOCS.is_dir(), f"missing {_DOCS}"
    for name in _REQUIRED_DOCS:
        path = _DOCS / name
        assert path.is_file(), f"missing doc {path}"


def test_as_prod_onboard_001_docs_honesty_and_chain_tokens() -> None:
    joined = "\n".join((_DOCS / name).read_text(encoding="utf-8") for name in _REQUIRED_DOCS)
    for token in _DOC_TOKENS:
        assert token in joined, f"onboard docs missing token {token!r}"
    # Docs may mention forbidden stamps only as negatives; never affirm them.
    assert "RELEASE CERTIFIED = YES" not in joined
    assert "PILOT PASS = YES" not in joined
    # Must link to install docs / scripts without owning doctor implementation.
    assert "AS-PROD-INSTALL" in joined or "productization/install" in joined
    assert "#254" in joined or "PROD-DOCTOR" in joined
    honesty = (_DOCS / "HONESTY.md").read_text(encoding="utf-8")
    assert "ALPHA_READY = NO" in honesty or "ALPHA_READY=NO" in honesty
    first_run = (_DOCS / "FIRST-RUN.md").read_text(encoding="utf-8")
    assert "atlas-onboard.ps1" in first_run
    assert "future" in first_run.lower() or "Future" in first_run


def test_as_prod_onboard_001_checklist_has_doctor_deferred() -> None:
    text = (_DOCS / "CHECKLIST.md").read_text(encoding="utf-8")
    assert "doctor" in text.lower()
    assert "Playwright" in text or "playwright" in text.lower()
    assert "atlas-stop.ps1" in text
    assert "ALPHA_READY" in text


def test_as_prod_onboard_001_onboard_script_present_and_contract() -> None:
    path = _SCRIPTS / "atlas-onboard.ps1"
    assert path.is_file(), f"missing script {path}"
    text = path.read_text(encoding="utf-8")
    for token in _ONBOARD_SCRIPT_TOKENS:
        assert token in text, f"atlas-onboard.ps1 missing token {token!r}"
    assert "ALPHA_READY=YES" not in text
    assert "RELEASE CERTIFIED = YES" not in text
    assert "PILOT PASS = YES" not in text
    # Orchestrates existing scripts only — no doctor CLI invocation.
    lowered = text.lower()
    assert "atlas doctor" not in lowered or "future): atlas doctor" in lowered
    assert "npx playwright" not in lowered
    assert "@playwright" not in lowered
    assert "playwright install" not in lowered
    assert "doctor.py" in text or "#254" in text
    # Must call both existing helpers by name.
    assert "atlas-preflight.ps1" in text
    assert "atlas-start.ps1" in text


def test_as_prod_onboard_001_does_not_own_doctor_module() -> None:
    """Onboard lane must not ship Core doctor.py (owned by #254)."""
    doctor = _REPO_ROOT / "src" / "project_atlas" / "doctor.py"
    readme = (_DOCS / "README.md").read_text(encoding="utf-8")
    assert "Does **not** implement" in readme or "NOT IMPLEMENTED" in readme
    script = (_SCRIPTS / "atlas-onboard.ps1").read_text(encoding="utf-8")
    # Must not invoke a doctor subcommand; placeholder note is OK.
    assert "atlas doctor --" not in script.lower()
    assert "& atlas doctor" not in script.lower()
    assert "python -m project_atlas.doctor" not in script.lower()
    # Soft awareness: if doctor.py exists on tip, onboard docs still say deferred.
    if doctor.is_file():
        honesty = (_DOCS / "HONESTY.md").read_text(encoding="utf-8")
        assert "NOT IMPLEMENTED HERE" in honesty or "#254" in honesty


def test_as_prod_onboard_001_install_sibling_still_present() -> None:
    """Onboard links install; install docs from AS-PROD-INSTALL-001 must exist."""
    assert _INSTALL_DOCS.is_dir(), f"missing install docs {_INSTALL_DOCS}"
    assert (_INSTALL_DOCS / "STRANGER.md").is_file()
    assert (_SCRIPTS / "atlas-preflight.ps1").is_file()
    assert (_SCRIPTS / "atlas-start.ps1").is_file()
