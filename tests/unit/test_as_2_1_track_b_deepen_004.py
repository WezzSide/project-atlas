"""AS-2.1 Track B deepen-004: graph LIVE hook + docs guard."""

from __future__ import annotations

from pathlib import Path


def test_live_graph_hook_present() -> None:
    root = Path(__file__).resolve().parents[2]
    hook = (root / "apps" / "web" / "src" / "hooks" / "useLiveGraph.ts").read_text(encoding="utf-8")
    assert "demo_stub" in hook
    assert "/v1/graph" in hook
    assert "graph_authority" not in hook or "derived" in hook
    page = (root / "apps" / "web" / "src" / "pages" / "production" / "GraphPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "Graph ≠ authority" in page or "Graph != authority" in page
    assert "useLiveGraph" in page


def test_threat_model_delta_host_and_demo() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "docs" / "atlas-2.1" / "THREAT-MODEL-DELTA.md").read_text(encoding="utf-8")
    assert "T-2.1-09" in text
    assert "T-2.1-10" in text
    assert "T-2.1-12" in text
