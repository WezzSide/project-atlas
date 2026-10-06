"""AS-SEC-SCAN-PORTFOLIO-SOURCEID-JSON-ESC-001 — decoded source ids must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.portfolio import build_portfolio
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_source_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    plant = vault / "state" / "concepts" / "harbor.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "concepts": [\n    {\n      "concept_id": "concept-harbor",\n'
        '      "type": "Project",\n      "project_id": "harbor",\n'
        '      "sources": [{"source_id": "\\u0041KIAAAAAAAAAAAAAAAAA", "path": "README.md"}],\n'
        '      "maturity": "unknown"\n    }\n  ]\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    (vault / "projects" / "harbor").mkdir(parents=True)
    report = vault / "generated" / "reports" / "ingestion-report.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        "{\n"
        '  "classifications": {\n'
        '    "\\u0041KIAAAAAAAAAAAAAAAAA": {"type": "architecture"}\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    assert scan_text(raw) == []
    assert scan_text(report.read_text(encoding="utf-8")) == []
    build_portfolio(vault)
    written = (vault / "generated" / "portfolio" / "documentation-coverage.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []
