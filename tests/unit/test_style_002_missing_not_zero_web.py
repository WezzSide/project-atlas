"""STYLE-002 (SM-TRUTH) — missing != zero / clean, web source gates.

Firewall: apps/web UI + this test only. No API contract or backend change.
Missing / failed / unread sources must render UNKNOWN or unavailable, never a
reassuring ``0`` / ``false`` / "no blockers" / "no receipts on disk".
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "apps" / "web"
PAGES = WEB / "src" / "pages" / "production"
HELPER = WEB / "src" / "lib" / "missingState.ts"
NODE_GATE = WEB / "scripts" / "test-missing-not-zero.mjs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    return re.sub(r"\s+", " ", _read(path))


def test_shared_helper_exists_and_never_defaults_to_zero() -> None:
    text = _read(HELPER)
    for name in ("countOrUnknown", "lengthOrUnknown", "boolOrUnknown", "listState"):
        assert f"export function {name}" in text
    assert "?? 0" not in text
    assert "?? false" not in text


def test_knowledge_truth_counts_are_unknown_when_missing() -> None:
    text = _read(PAGES / "KnowledgePage.tsx")
    assert not re.search(r"_count \?\? 0", text)
    assert "countOrUnknown(truth?.pending_review_count)" in text
    assert "countOrUnknown(truth?.conflict_count)" in text
    assert "lengthOrUnknown(truth?.evidence)" in text
    assert text.index("{pendingMissing ? (") < text.index("No pending reviews recorded")
    assert text.index("{conflictsMissing ? (") < text.index("No unresolved conflicts recorded")


def test_roadmap_clean_copy_is_guarded_by_missing_state() -> None:
    text = _read(PAGES / "RoadmapPage.tsx")
    assert "loaded: Boolean(roadmap)" in text
    assert text.index("{blockersMissing ? (") < text.index("No derived blockers")
    assert text.index("{unknownsMissing ? (") < text.index("No UNKNOWN signals")
    assert text.index("{pathMissing ? (") < text.index("no remaining-work path")


def test_ops_fetch_failure_does_not_claim_empty_disk() -> None:
    text = _flat(PAGES / "OpsHealthPage.tsx")
    assert text.count("no ops receipts on disk") == 1
    assert text.index("receiptError || !inventory ? (") < text.index("no ops receipts on disk")
    assert "receipt inventory could not be read" in text
    assert "completion_claimed ?? false" not in text
    assert "completion_claimed=false (UI policy" in text


def test_read_status_panel_does_not_default_missing_source_to_live() -> None:
    text = _read(WEB / "src" / "components" / "ReadStatusPanel.tsx")
    assert not re.search(r'data_source \?\?[^;]*"live_api"', text)
    assert "DATA SOURCE UNKNOWN" in text
    assert 'source === "live_api"' in text


def test_discovery_counts_do_not_default_to_zero() -> None:
    text = _read(PAGES / "DiscoveryPage.tsx")
    assert not re.search(r"counts\?\.\w+ \?\? 0", text)
    assert text.count("countOrUnknown(view.counts?.") == 4


@pytest.mark.parametrize("page", ["MissionControlPage.tsx", "WorkspacePage.tsx"])
def test_lens_forced_constants_are_labelled_ui_policy(page: str) -> None:
    text = _read(PAGES / page)
    assert "_available ?? false" not in text
    assert "pilot_estate_rows.length : 0" not in text
    assert "authentic_pilot ?? false" not in text
    assert text.count("({PILOT_UI_POLICY_NOTE})") == 2
    hook = _read(WEB / "src" / "hooks" / "useLiveMissionWorkspace.ts")
    assert "export const PILOT_UI_POLICY_NOTE" in hook
    assert "UI policy" in hook


def test_node_runtime_gate_passes_when_node_available() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    version = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 22:
        pytest.skip(f"node {version} cannot import .ts (needs type stripping)")
    result = subprocess.run([node, str(NODE_GATE)], capture_output=True, text=True, check=False)
    if "ERR_UNKNOWN_FILE_EXTENSION" in result.stderr:
        pytest.skip(f"node {version} has type stripping disabled")
    assert result.returncode == 0, result.stderr[-2000:]
    assert "STYLE-002 missing-not-zero gates PASS" in result.stdout
