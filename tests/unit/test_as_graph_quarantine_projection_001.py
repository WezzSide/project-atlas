"""AS-GRAPH-QUARANTINE-PROJECTION-001 — quarantined rows are not derived truth."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.graph_projections import (
    load_relationships_from_vault,
    materialize_projections_from_vault,
)
from project_atlas.graph_relationships import RelationshipRecord


def test_quarantined_relationship_is_not_projected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    rel_dir = vault / "generated" / "graph" / "relationships" / "demo"
    rel_dir.mkdir(parents=True)
    retained = RelationshipRecord(
        project_id="demo",
        relationship_id="rel-ok",
        relationship_type="depends-on",
        source_entity_id="demo:a",
        target_entity_id="demo:b",
        source_graphify_id="a",
        target_graphify_id="b",
        link_quality="inferred",
        relationship_fingerprint="c" * 64,
        provenance={},
    )
    (rel_dir / "rel-ok.json").write_text(retained.to_json(), encoding="utf-8")
    quarantined = {
        "project_id": "demo",
        "relationship_id": "rel-q",
        "relationship_type": "depends-on",
        "source_entity_id": "demo:a",
        "target_entity_id": "demo:b",
        "source_graphify_id": "a",
        "target_graphify_id": "b",
        "link_quality": "inferred",
        "relationship_fingerprint": "d" * 64,
        "status": "quarantined",
        "provenance": {},
    }
    (rel_dir / "rel-q.json").write_text(json.dumps(quarantined), encoding="utf-8")
    loaded = load_relationships_from_vault(vault, project_id="demo")
    assert [row.relationship_id for row in loaded] == ["rel-ok"]
    bundle = materialize_projections_from_vault(vault, project_id="demo")
    assert "rel-q" not in bundle.relationships_md
    assert "rel-ok" in bundle.relationships_md
