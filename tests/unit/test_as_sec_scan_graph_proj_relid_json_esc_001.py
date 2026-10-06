"""AS-SEC-SCAN-GRAPH-PROJ-RELID-JSON-ESC-001 — decoded relationship ids must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.graph_projections import (
    materialize_projections_from_vault,
    write_projection_outputs,
)
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_relationship_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    rel = vault / "generated" / "graph" / "relationships" / "harbor-api" / "rel.json"
    rel.parent.mkdir(parents=True)
    raw = (
        "{\n"
        '  "status": "retained",\n'
        f'  "relationship_id": "{ESC}",\n'
        '  "project_id": "harbor-api",\n'
        '  "relationship_type": "depends-on",\n'
        '  "source_entity_id": "svc-a",\n'
        '  "target_entity_id": "svc-b",\n'
        '  "link_quality": "inferred",\n'
        '  "relationship_fingerprint": "abcd1234abcd1234abcd1234abcd1234"\n'
        "}\n"
    )
    rel.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    bundle = materialize_projections_from_vault(vault, project_id="harbor-api")
    write_projection_outputs(bundle, vault=vault)
    written = (
        vault / "generated" / "graph" / "projections" / "harbor-api" / "relationships.md"
    ).read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
    assert "redacted-sensitive" in written


def test_json_unicode_escape_source_entity_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    rel = vault / "generated" / "graph" / "relationships" / "harbor-api" / "rel.json"
    rel.parent.mkdir(parents=True)
    raw = (
        "{\n"
        '  "status": "retained",\n'
        '  "relationship_id": "rel-safe",\n'
        '  "project_id": "harbor-api",\n'
        '  "relationship_type": "depends-on",\n'
        f'  "source_entity_id": "{ESC}",\n'
        '  "target_entity_id": "svc-b",\n'
        '  "link_quality": "inferred",\n'
        '  "relationship_fingerprint": "abcd1234abcd1234abcd1234abcd1234"\n'
        "}\n"
    )
    rel.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    bundle = materialize_projections_from_vault(vault, project_id="harbor-api")
    write_projection_outputs(bundle, vault=vault)
    written = (
        vault / "generated" / "graph" / "projections" / "harbor-api" / "relationships.md"
    ).read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
