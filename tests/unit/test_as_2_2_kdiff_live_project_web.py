"""AS-2.2-KDIFF-001 live project Web journey honesty gates.

Firewall: Time Machine web hook + page. kdiff != authority.
LIVE failure != demo_stub. Failed load != empty UNKNOWN catalog.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "apps" / "web"
HOOK = WEB / "src" / "hooks" / "useLiveTimeMachine.ts"
PAGE = WEB / "src" / "pages" / "production" / "TimeMachinePage.tsx"
PROJECTS = WEB / "src" / "pages" / "production" / "ProjectsPage.tsx"
KNOWLEDGE = WEB / "src" / "pages" / "production" / "KnowledgePage.tsx"


def test_hook_scopes_and_encodes_project() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert 'TIME_MACHINE_PROJECT = "harbor-api"' in text
    assert "encodeURIComponent(projectId)" in text
    assert "/v1/conflicts?project=${project}" in text
    assert "/v1/kdiff?project=${project}&as_of=" in text
    assert "/v1/kdiff?project=${project}&from=" in text
    assert 'method: "POST"' not in text
    assert "method: 'POST'" not in text


def test_hook_does_not_label_live_failure_as_demo_stub() -> None:
    text = HOOK.read_text(encoding="utf-8")
    http_fail = text.split("if (!conflictsResp.ok")[1].split("return;")[0]
    assert "setDataSource(null)" in http_fail
    assert 'setDataSource("demo_stub")' not in http_fail
    assert 'setDataSource(liveApiDemoOnly() ? "demo_stub" : null)' in text


def test_page_binds_url_and_does_not_call_empty_error_unknown() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert 'params.get("project")' in text
    assert 'params.get("from")' in text
    assert 'params.get("to")' in text
    assert "kdiff≠authority" in text
    assert "liveReady" in text
    assert "Unavailable — not an empty conflict catalog." in text
    assert "unknown — no conflict rows" in text
    assert "/time-machine?project=" in PROJECTS.read_text(encoding="utf-8")
    assert "/time-machine?project=" in KNOWLEDGE.read_text(encoding="utf-8")
    assert "usingControlledDemoDefault" in text
    assert "fixture_class=controlled_demo_fixture" in text
    assert "authentic_history_claim=no" in text
    assert "project_query=explicit" in text
    assert "AUTHENTIC_HISTORY_CLAIM=NO" in text
