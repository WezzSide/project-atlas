"""AS-PROJECT-ROADMAP-001 ProdNav project-context preservation.

These cases failed on a9770ce: static /roadmap + DEFAULT_PROJECT=harbor-api.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[2]
NAV = REPO_ROOT / "apps" / "web" / "src" / "components" / "ProdNav.tsx"
PAGE = REPO_ROOT / "apps" / "web" / "src" / "pages" / "production" / "RoadmapPage.tsx"

DARK_FACTORY = "dark-factory-02ee94d0"
PROJECT_ATLAS = "project-atlas"
HARBOR = "harbor-api"
PROJECT_AWARE = {
    "/knowledge",
    "/context",
    "/ask",
    "/time-machine",
    "/roadmap",
    "/workspace",
}


def _nav_text() -> str:
    return NAV.read_text(encoding="utf-8")


def _project_aware_href(path: str, project: str | None) -> str:
    if not project or path not in PROJECT_AWARE:
        return path
    return f"{path}?project={quote(project, safe='')}"


def test_prod_nav_reads_current_project_query() -> None:
    text = _nav_text()
    assert "useSearchParams" in text
    assert 'params.get("project")' in text
    assert "projectAwareHref" in text
    assert "PROJECT_AWARE_PATHS" in text
    for path in PROJECT_AWARE:
        assert f'"{path}"' in text
    assert HARBOR not in text
    assert PROJECT_ATLAS not in text
    assert DARK_FACTORY not in text


def test_case1_dark_factory_nav_preserves_roadmap_project() -> None:
    href = _project_aware_href("/roadmap", DARK_FACTORY)
    assert f"project={DARK_FACTORY}" in href
    assert HARBOR not in href
    text = _nav_text()
    assert "encodeURIComponent(project)" in text
    assert '{ to: "/roadmap", label: "Roadmap" }' in text


def test_case2_project_atlas_nav_preserves_roadmap_project() -> None:
    href = _project_aware_href("/roadmap", PROJECT_ATLAS)
    assert f"project={PROJECT_ATLAS}" in href
    assert HARBOR not in href


def test_case3_no_project_context_does_not_issue_harbor_api() -> None:
    href = _project_aware_href("/roadmap", None)
    assert href == "/roadmap"
    assert "project=" not in href
    assert HARBOR not in href
    page = PAGE.read_text(encoding="utf-8")
    assert "DEFAULT_PROJECT" not in page
    assert f'"{HARBOR}"' not in page
    assert f"'{HARBOR}'" not in page


def test_nav_does_not_copy_from_to_onto_roadmap() -> None:
    text = _nav_text()
    assert "projectAwareHref" in text
    assert "never from=/to=" in text
    assert 'params.get("from")' not in text
    assert 'params.get("to")' not in text
    helper = text.split("export function projectAwareHref", 1)[1].split(
        "export function ProdNav",
        1,
    )[0]
    assert "from" not in helper
    assert "to=" not in helper
    assert "encodeURIComponent(project)" in helper


def test_composed_journey_preserves_each_project_aware_href() -> None:
    for path in ("/knowledge", "/context", "/ask", "/time-machine", "/roadmap"):
        href = _project_aware_href(path, DARK_FACTORY)
        assert href == f"{path}?project={DARK_FACTORY}"
        bare = _project_aware_href(path, None)
        assert bare == path
        assert HARBOR not in href
    text = _nav_text()
    assert re.search(r"PROJECT_AWARE_PATHS = new Set\(\[", text)
    assert '{ to: "/knowledge", label: "Knowledge" }' in text
    assert '{ to: "/time-machine", label: "Time Machine" }' in text
    assert '{ to: "/roadmap", label: "Roadmap" }' in text
