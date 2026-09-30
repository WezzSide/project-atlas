"""AS-2.0-WEB-001 — Intelligence UX truth, binding, and read-only guards."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "apps" / "web"
PAGE = WEB / "src" / "pages" / "production" / "IntelligencePage.tsx"
HOOK = WEB / "src" / "hooks" / "useLiveIntelligence.ts"
NAV = WEB / "src" / "components" / "ProdNav.tsx"
APP = WEB / "src" / "App.tsx"
ASK = WEB / "src" / "pages" / "production" / "AskPage.tsx"
ASK_HOOK = WEB / "src" / "hooks" / "useLiveAsk.ts"
API = WEB / "src" / "api" / "liveApi.ts"
HARBOR = "harbor-api"


def test_intelligence_route_and_nav_preserve_1x_surfaces() -> None:
    app = APP.read_text(encoding="utf-8")
    nav = NAV.read_text(encoding="utf-8")
    assert 'path="/intelligence"' in app
    for path in (
        "/projects",
        "/knowledge",
        "/context",
        "/ask",
        "/time-machine",
        "/roadmap",
        "/workspace",
    ):
        assert f'path="{path}"' in app
        assert f'"{path}"' in nav
    assert '{ to: "/intelligence", label: "Intelligence" }' in nav
    assert '"/intelligence"' in nav


def test_intelligence_is_actual_project_binding() -> None:
    page = PAGE.read_text(encoding="utf-8")
    hook = HOOK.read_text(encoding="utf-8")
    assert "useSearchParams" in page
    assert 'params.get("project")' in page
    assert "projectParam && projectParam.trim()" in page
    assert "useLiveIntelligence(projectId, view)" in page
    assert "UNKNOWN / SELECT_SCOPE — select a project" in page
    assert 'projectId ?? "UNKNOWN"' in page
    assert "DEFAULT_PROJECT" not in page
    assert f'?? "{HARBOR}"' not in page
    assert f'"{HARBOR}"' not in page
    assert "No silent fixture default" in page
    assert "actual project binding" in page
    assert "encodeURIComponent(projectId)" in hook


def test_ask_scope_is_unchanged() -> None:
    ask = ASK.read_text(encoding="utf-8")
    hook = ASK_HOOK.read_text(encoding="utf-8")
    assert "useLiveAsk(urlQuery || null)" in ask
    assert "DISPLAY_CLIENT_HINT" not in hook
    assert "function useLiveAsk(query: string | null)" in hook
    assert "/v1/ask" in hook
    assert "project=" not in hook.split("liveApiFetch")[1][:200]


def test_live_failure_is_not_demo() -> None:
    hook = HOOK.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    assert "if (demoSelected)" in hook or "if (liveApiDemoOnly())" in hook
    assert 'setDataSource("live_api")' in hook
    catch = hook.split("Promise.all(jobs)", 1)[1].split(".catch", 1)[1]
    assert "setDataSource(null)" in catch
    assert 'setDataSource("demo_stub")' not in catch
    assert "HTTP_FAILURE" in hook
    assert "Demo was not substituted" in page
    assert "HTTP_FAILURE — live intelligence unavailable" in page


def test_truth_states_are_rendered() -> None:
    page = PAGE.read_text(encoding="utf-8")
    hook = HOOK.read_text(encoding="utf-8")
    for state in (
        "LIVE",
        "DERIVED",
        "UNKNOWN",
        "NO_DATA",
        "CONTESTED",
        "STALE",
        "HTTP_FAILURE",
        "DEMO",
    ):
        assert f'"{state}"' in hook
    assert "truth={truth}" in page
    assert "CONTRADICTION CANDIDATE" in page
    assert "NEEDS REVIEW" in page
    assert "proven falsehood" in page
    assert "73/100" in page
    assert "risk 82%" in page
    assert "Attention is not a score" in page
    assert "selected=null" in page
    assert "No execute" in page


def test_no_canonical_mutation_affordances() -> None:
    page = PAGE.read_text(encoding="utf-8")
    hook = HOOK.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")
    for forbidden in ("Approve", "Resolve", "Accept", 'method: "POST"', "method: 'POST'"):
        assert forbidden not in page
        assert forbidden not in hook
    assert "liveApiFetch" in hook
    assert 'method: "POST"' not in hook
    assert "function liveApiFetch" in api


def test_project_mismatch_is_unknown() -> None:
    page = PAGE.read_text(encoding="utf-8")
    assert "projectMismatch" in page
    assert "UNKNOWN — intelligence project does not match selected project" in page


def test_portfolio_is_explicit_cross_project() -> None:
    page = PAGE.read_text(encoding="utf-8")
    hook = HOOK.read_text(encoding="utf-8")
    assert "Portfolio Intelligence" in page
    assert "explicit cross-project" in page
    assert "does not fetch all projects" in page
    assert "SELECT_SCOPE" in page
    assert '"/v1/portfolio-state"' not in hook
    assert "`/v1/portfolio-state?${projectQ}`" in hook
    assert "if (!projectId)" in hook
