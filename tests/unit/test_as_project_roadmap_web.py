"""AS-PROJECT-ROADMAP-001 web lens — project routing + live-failure honesty."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "apps" / "web"
HOOK = WEB / "src" / "hooks" / "useLiveRoadmap.ts"
PAGE = WEB / "src" / "pages" / "production" / "RoadmapPage.tsx"
NAV = WEB / "src" / "components" / "ProdNav.tsx"

HARBOR = "harbor-api"


def test_roadmap_page_follows_project_query() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "useSearchParams" in text
    assert 'params.get("project")' in text
    assert "useLiveRoadmap(projectId)" in text
    assert "roadmap-project" in text
    assert "UNKNOWN — roadmap project does not match selected project" in text
    assert "roadmap≠authority" in text


def test_roadmap_hook_does_not_label_live_failure_as_demo() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert "if (liveApiDemoOnly())" in text
    assert 'setDataSource("live_api")' in text
    catch = text.split(".catch", 1)[1]
    assert "setDataSource(null)" in catch
    assert 'setDataSource("demo_stub")' not in catch
    assert "roadmap HTTP" in text


def test_no_query_does_not_default_to_harbor_api() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "DEFAULT_PROJECT" not in text
    assert '?? "harbor-api"' not in text
    assert "?? DEFAULT_PROJECT" not in text
    assert "projectParam && projectParam.trim()" in text
    assert "useLiveRoadmap(projectId)" in text
    assert 'projectId ?? "UNKNOWN"' in text
    assert "unknown — select a project" in text


def test_explicit_dark_factory_query_still_uses_live_hook() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert 'params.get("project")' in text
    assert "useLiveRoadmap(projectId)" in text
    hook = HOOK.read_text(encoding="utf-8")
    assert "liveApiFetch(`/v1/roadmap?project=${encodeURIComponent(projectId)}`)" in hook
    assert "if (!projectId)" in hook


def test_selector_writes_query_project() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "function onSelectProject(next: string)" in text
    assert 'nextParams.set("project", next)' in text
    assert 'nextParams.delete("project")' in text
    assert "setParams(nextParams, { replace: true })" in text


def test_project_mismatch_is_unknown() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "projectMismatch" in text
    assert "briefProject !== projectId" in text
    assert "UNKNOWN — roadmap project does not match selected project" in text


def test_harbor_api_valid_only_when_explicitly_selected() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "DEFAULT_PROJECT" not in text
    assert f'"{HARBOR}"' not in text
    assert f"'{HARBOR}'" not in text
    assert 'params.get("project")' in text
    assert "useLiveRoadmap(projectId)" in text
    hook = HOOK.read_text(encoding="utf-8")
    assert "encodeURIComponent(projectId)" in hook


def test_composed_journey_paths_are_project_aware() -> None:
    nav = NAV.read_text(encoding="utf-8")
    for path in (
        "/knowledge",
        "/context",
        "/ask",
        "/time-machine",
        "/roadmap",
    ):
        assert f'"{path}"' in nav
    assert "PROJECT_AWARE_PATHS" in nav
    assert "projectAwareHref" in nav
    assert "from=/to=" in nav
    page = PAGE.read_text(encoding="utf-8")
    assert "No silent fixture default" in page
