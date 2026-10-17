"""AS-SEC-SCAN-INDEXES-JSON-ESC-001 — decoded identity keys must not persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.indexes import build_indexes
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_project_id_is_not_indexed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    plant = vault / "state" / "concepts" / "harbor.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "concepts": [\n    {\n      "concept_id": "concept-harbor",\n'
        '      "type": "Project",\n      "project_id": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '      "tags": [],\n      "relationships": []\n    }\n  ]\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    (vault / "projects" / "harbor").mkdir(parents=True)
    (vault / "projects" / "harbor" / "project.md").write_text("# harbor\n", encoding="utf-8")
    assert scan_text(raw) == []
    build_indexes(vault)
    written = (vault / "generated" / "indexes" / "concepts.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_source_path_is_not_indexed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    plant = vault / "state" / "sources.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "sources": [\n    {\n      "source_id": "src-harbor",\n'
        '      "source_lineage_id": "lin-harbor",\n'
        '      "canonical_project_id": "harbor",\n'
        '      "current_path": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '      "path_history": []\n    }\n  ]\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    build_indexes(vault)
    written = (vault / "generated" / "indexes" / "sources.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
    payload = json.loads(written)
    assert TOKEN not in payload.get("by_current_path", {})


def test_json_unicode_escape_conflict_project_id_is_not_indexed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    plant = vault / "review" / "conflicts" / "harbor.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "entries": [\n    {\n      "conflict_id": "c-harbor",\n'
        '      "project_id": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '      "claim_ids": ["a", "b"],\n      "claims": []\n    }\n  ]\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    build_indexes(vault)
    written = (vault / "generated" / "indexes" / "conflicts.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_review_project_id_is_not_indexed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    plant = vault / "review" / "pending" / "harbor.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "entries": [\n    {\n      "review_id": "r-harbor",\n'
        '      "project_id": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '      "category": "gap",\n      "source_ids": []\n    }\n  ]\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    build_indexes(vault)
    written = (vault / "generated" / "indexes" / "reviews.json").read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
