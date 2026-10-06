"""AS-SEC-SCAN-HANDOFF-MANIFEST-PATH-JSON-ESC-001 — decoded manifest paths must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.agent_handoff import export_agent_context
from project_atlas.inventory_drift import CONNECT_MANIFEST
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_manifest_path_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    plant = vault / CONNECT_MANIFEST
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "sources": [\n    {\n      "path": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        f'      "sha256": "{"a" * 64}",\n'
        '      "likely_project": "harbor-api",\n'
        '      "project_id": "harbor-api"\n    }\n  ]\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    export_agent_context(vault, "harbor-api", refresh_brief=False)
    written = (vault / "generated" / "ops" / "agent-context" / "harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []
