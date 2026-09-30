"""AS-2.0-UX-001 entry gate + thin contract freeze.

Asserts docs/AS-2.0-UX-001.md presence, READY + dependency freeze, ADR-008/010
invariants, and read-only ops-health / impact-lens adapter contracts.
No UI rewrite; no canonical writes.
"""

from __future__ import annotations

from pathlib import Path

from project_atlas.web_api import impact_graph_summary, read_status

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = REPO_ROOT / "docs" / "AS-2.0-UX-001.md"
ADR_DIR = REPO_ROOT / "docs" / "adr"
SIGNOFF = REPO_ROOT / "docs" / "AS-WEB-ACCEPT-GOVERNOR-SIGNOFF.md"
WEB_API_INIT = REPO_ROOT / "src" / "project_atlas" / "web_api" / "__init__.py"


def test_entry_gate_doc_ready_with_deps_and_boundaries() -> None:
    text = ENTRY.read_text(encoding="utf-8")
    assert "AS-2.0-UX-001" in text
    assert "**READY**" in text
    assert "WEB APPLICATION ACCEPTED" in text
    assert "ADR-008" in text
    assert "ADR-010" in text
    assert "J-005" in text
    assert "### IN" in text
    assert "### OUT" in text
    assert "### FORBIDDEN" in text
    assert "No canonical writes" in text or "no canonical writes" in text.lower()
    assert "UI ≠ canonical" in text
    assert "Graph ≠ authority" in text
    assert "Unknown ≠ healthy" in text


def test_web_accepted_yes_and_adrs_present() -> None:
    signoff = SIGNOFF.read_text(encoding="utf-8")
    assert "**WEB APPLICATION ACCEPTED** | **YES**" in signoff
    for name in (
        "ADR-008-atlas-web-application.md",
        "ADR-010-atlas-web-ux.md",
    ):
        assert (ADR_DIR / name).is_file(), f"missing ADR: {name}"


def test_adr_008_and_010_freeze_invariants() -> None:
    adr008 = (ADR_DIR / "ADR-008-atlas-web-application.md").read_text(encoding="utf-8")
    adr010 = (ADR_DIR / "ADR-010-atlas-web-ux.md").read_text(encoding="utf-8")
    assert "UI ≠ canonical" in adr008 or ("UI" in adr008 and "canonical" in adr008.lower())
    assert "Graph ≠ authority" in adr008 or (
        "graph" in adr008.lower() and "authority" in adr008.lower()
    )
    assert "unknown" in adr008.lower() and "healthy" in adr008.lower()
    for mode in ("overview", "projects", "ops", "impact"):
        assert mode in adr010
    assert "Impact" in adr010
    assert "Graph ≠ authority" in adr010 or "authority" in adr010.lower()


def test_web_api_remains_read_only_import_boundary() -> None:
    text = WEB_API_INIT.read_text(encoding="utf-8")
    assert "Read-only" in text or "read-only" in text
    import_lines = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith(("from ", "import ")) and "__future__" not in line
    ]
    forbidden = (
        "knowledge_compiler",
        "ingestion",
        "semantic_compiler",
        "write_plan",
        "_promote",
    )
    for line in import_lines:
        for token in forbidden:
            assert token not in line, f"web_api must not import writer: {token}"
        assert "project_atlas.web_api" in line


def test_ops_health_read_adapter_preserves_unknown_and_non_authority(
    tmp_path: Path,
) -> None:
    """Thin ops lens contract: absent vault → unknown, never healthy/authority."""
    status = read_status(tmp_path / "missing-vault")
    assert status["ui_canonical"] is False
    assert status["graph_authority"] is False
    assert status["unknown_equals_healthy"] is False
    assert status["health"]["rollup"] == "unknown"
    assert status["health"]["available"] is False
    assert status["health"]["authority_plane"] == "none"
    assert status["read_plane"] == "unread"


def test_impact_lens_read_adapter_never_claims_authority(tmp_path: Path) -> None:
    """Thin impact lens contract: missing J-005 projection → unknown, Graph≠authority."""
    vault = tmp_path / "vault"
    vault.mkdir()
    summary = impact_graph_summary(vault)
    assert summary["available"] is False
    assert summary["graph_authority"] is False
    assert summary["node_count"] == 0
    assert summary["edge_count"] == 0
    assert "authority" in summary["note"].lower() or "UNKNOWN" in summary["note"]
