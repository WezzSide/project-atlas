"""AS-2.1-ASK-ATLAS-LIVE-001 web journey honesty gates.

Firewall: apps/web Ask page + hook. Does not mutate LIVE_API or Layer B.
ASK != authority. UI != canonical. LIVE failure != demo_stub.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "apps" / "web"
HOOK = WEB / "src" / "hooks" / "useLiveAsk.ts"
PAGE = WEB / "src" / "pages" / "production" / "AskPage.tsx"
APP = WEB / "src" / "App.tsx"
NAV = WEB / "src" / "components" / "ProdNav.tsx"
KNOWLEDGE = WEB / "src" / "pages" / "production" / "KnowledgePage.tsx"
API_SERVER = REPO_ROOT / "src" / "project_atlas" / "api_server.py"


def test_ask_route_and_nav_exist() -> None:
    assert 'path="/ask"' in APP.read_text(encoding="utf-8")
    assert '{ to: "/ask", label: "Ask" }' in NAV.read_text(encoding="utf-8")
    knowledge = KNOWLEDGE.read_text(encoding="utf-8")
    assert "/ask" in knowledge
    assert "ask (≠ authority)" in knowledge
    assert "/time-machine?project=" in knowledge


def test_hook_uses_read_only_encoded_query() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert "/v1/ask?q=${encodeURIComponent(q)}" in text
    assert "query too long (max 256)" in text
    assert "liveApiFetch" in text
    assert 'method: "POST"' not in text
    assert "method: 'POST'" not in text


def test_hook_does_not_label_live_failure_as_demo_stub() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert 'setDataSource("live_api")' in text
    assert 'setDataSource("demo_stub")' in text
    assert "if (!liveApiDemoOnly())" in text
    assert "setError(`ask HTTP ${resp.status}`)" in text
    assert "setDataSource(null)" in text
    live_fail = text.split("if (resp.ok)")[1].split("} catch")[0]
    assert 'setDataSource("demo_stub")' not in live_fail
    catch_block = text.split("} catch (err: unknown) {")[1].split("return;")[0]
    assert 'setDataSource("demo_stub")' not in catch_block
    demo_block = text.split("if (!liveApiDemoOnly())")[1]
    assert 'setDataSource("demo_stub")' in demo_block


def test_page_keeps_unknown_honest_and_ask_not_authority() -> None:
    text = PAGE.read_text(encoding="utf-8")
    assert "ASK ≠ authority" in text or "ask≠authority" in text
    assert "UNKNOWN stays UNKNOWN" in text
    assert "UNKNOWN — no matching live projections" in text
    assert "Idle is not UNKNOWN" in text
    assert "Health keywords" in text
    assert "not a health verdict" in text
    assert "hint only — ask is vault-wide" in text
    assert "/knowledge?project=" in text
    assert "/time-machine?project=" in text
    assert "canonical_write=false" in text
    assert "ui_canonical=false" in text


def test_package_does_not_require_api_mutation() -> None:
    text = API_SERVER.read_text(encoding="utf-8")
    assert 'path == "/v1/ask"' in text
    assert "ask_atlas_live" in text
