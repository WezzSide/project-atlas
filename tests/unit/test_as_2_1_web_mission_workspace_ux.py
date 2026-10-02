"""AS-2.1-WEB-MISSION-WORKSPACE-UX — LIVE/DEMO/FIXTURE mode visibility gates.

Firewall: apps/web Mission/Workspace UI + this test only.
Exclusion: does not edit API server or shared schema roots.
No PILOT invent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "apps" / "web"
MISSION = WEB / "src" / "pages" / "production" / "MissionControlPage.tsx"
WORKSPACE = WEB / "src" / "pages" / "production" / "WorkspacePage.tsx"
SWITCHER = WEB / "src" / "components" / "LensModeSwitcher.tsx"
HOOK = WEB / "src" / "hooks" / "useLiveMissionWorkspace.ts"
TYPES = WEB / "src" / "types.ts"
MISSION_FIXTURE = WEB / "public" / "sample-mission-control.fixture.json"
WORKSPACE_FIXTURE = WEB / "public" / "sample-workspace.fixture.json"
MISSION_DEMO = WEB / "public" / "sample-mission-control.json"
WORKSPACE_DEMO = WEB / "public" / "sample-workspace.json"
API_SERVER = REPO_ROOT / "src" / "project_atlas" / "api_server.py"
SCHEMAS = REPO_ROOT / "src" / "project_atlas" / "schemas"
STYLES = WEB / "src" / "styles.css"
SIGNAL_RACK = WEB / "src" / "pages" / "design-lab" / "SignalRackPage.tsx"


def test_lens_mode_switcher_exposes_live_demo_fixture() -> None:
    text = SWITCHER.read_text(encoding="utf-8")
    assert 'id: "live"' in text
    assert 'id: "demo"' in text
    assert 'id: "fixture"' in text
    assert "LIVE" in text
    assert "DEMO" in text
    assert "FIXTURE" in text
    assert "mode-switcher" in text


def test_mission_and_workspace_pages_wire_mode_switcher() -> None:
    for page in (MISSION, WORKSPACE):
        text = page.read_text(encoding="utf-8")
        assert "LensModeSwitcher" in text
        assert "resolveLensMode" in text
        assert "lens_mode={mode}" in text
        assert "LIVE-first" in text or "LIVE-first" in text.replace("\n", " ")
        assert "DEMO STUB" in text
        assert "FIXTURE" in text
        assert "no PILOT invent" in text
        assert "APPLICATION ACCEPTED = YES" in text
        assert "Exclusion: apps/web UI only" in text


def test_hook_is_live_first_without_silent_demo_fallback() -> None:
    text = HOOK.read_text(encoding="utf-8")
    assert "no silent invent" in text
    assert "sample-mission-control.fixture.json" in text
    assert "sample-workspace.fixture.json" in text
    assert "stampFixture" in text
    assert "choose DEMO or FIXTURE" in text


def test_data_source_includes_fixture() -> None:
    text = TYPES.read_text(encoding="utf-8")
    assert '"fixture"' in text
    assert "demo_stub" in text
    assert "live_api" in text


def test_fixture_samples_flags_only_no_pilot_invent() -> None:
    for path in (MISSION_FIXTURE, WORKSPACE_FIXTURE, MISSION_DEMO, WORKSPACE_DEMO):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["ui_canonical"] is False
        assert payload["graph_authority"] is False
        assert payload["unknown_equals_healthy"] is False
        assert payload["authentic_pilot"] is False
        assert payload["pilot_estate_rows"] == []
        assert payload["rollup"] == "unknown"


def test_fixture_data_source_labeled() -> None:
    mission = json.loads(MISSION_FIXTURE.read_text(encoding="utf-8"))
    workspace = json.loads(WORKSPACE_FIXTURE.read_text(encoding="utf-8"))
    assert mission["data_source"] == "fixture"
    assert workspace["data_source"] == "fixture"
    assert mission["fixture_isolated"] is True
    assert workspace["fixture_isolated"] is True


def test_exclusion_api_and_schema_roots_untouched_by_this_package() -> None:
    """Guardrail note: this package must not require API/schema edits.

    Existence of API/schema roots is allowed; this test only asserts the UX
    package artifacts stay under apps/web (+ this unit test).
    """
    assert API_SERVER.is_file()
    assert SCHEMAS.is_dir()
    assert SWITCHER.is_relative_to(WEB)
    assert MISSION.is_relative_to(WEB)
    assert WORKSPACE.is_relative_to(WEB)
    assert HOOK.is_relative_to(WEB)


def test_live_mode_selection_is_not_rendered_as_ok_green() -> None:
    """STYLE-001 (SM-SEPARATION): selection is not health.

    No CSS rule for selection state (``.mode`` / ``.mode-*``) may use the
    health token ``--atlas-ok``; LIVE can be selected while the live read
    failed. Selection stays conveyed by ``aria-pressed`` + neutral active style.
    """
    css = re.sub(r"/\*.*?\*/", "", STYLES.read_text(encoding="utf-8"), flags=re.S)
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "var(--atlas-ok)" in body:
            assert not re.search(r"\.mode\b|\.mode-", selector), selector.strip()
    assert ".mode-switcher .mode.active" in css
    assert "aria-pressed" in SWITCHER.read_text(encoding="utf-8")


def test_signal_rack_listed_projects_lamp_is_not_ok() -> None:
    text = SIGNAL_RACK.read_text(encoding="utf-8")
    assert not re.search(r'lamp-dot ok"[^>]*/>\s*Projects', text)
    assert re.search(r'lamp-dot neutral"[^>]*/>\s*Projects', text)
