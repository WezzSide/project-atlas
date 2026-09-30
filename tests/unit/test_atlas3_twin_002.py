"""AT3-002 Project Twin constructors and schemas."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.domain import TWIN_NODES, TWIN_RELATIONSHIPS
from project_atlas.atlas3.twin import make_node, make_relationship

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_vocabulary_counts() -> None:
    assert len(TWIN_NODES) == 19
    assert len(TWIN_RELATIONSHIPS) == 15
    assert "symbol" in TWIN_NODES
    assert "BLOCKS" in TWIN_RELATIONSHIPS


def test_node_and_relationship_require_provenance() -> None:
    with pytest.raises(Atlas3Error) as missing_node:
        make_node(
            node_type="commit",
            node_id="abc",
            project_id="harbor-api",
            evidence_refs=[],
        )
    assert missing_node.value.code == "PROVENANCE_REQUIRED"
    with pytest.raises(Atlas3Error) as missing_rel:
        make_relationship(
            relationship="CONTAINS",
            from_id="repo-1",
            to_id="file-1",
            project_id="harbor-api",
            evidence_refs=[],
        )
    assert missing_rel.value.code == "PROVENANCE_REQUIRED"


def test_constructors_match_shipped_schemas() -> None:
    node = make_node(
        node_type="Project",
        node_id="harbor-api",
        project_id="harbor-api",
        evidence_refs=["src:harbor-api/.atlas-project.yaml"],
    )
    rel = make_relationship(
        relationship="owned_by",
        from_id="agent-1",
        to_id="harbor-api",
        project_id="harbor-api",
        evidence_refs=["evt:owner-1"],
    )
    assert rel["relationship"] == "OWNED_BY"
    node_schema = json.loads(
        (ROOT / "docs/atlas-3/contracts/twin-node.schema.json").read_text(encoding="utf-8")
    )
    rel_schema = json.loads(
        (ROOT / "docs/atlas-3/contracts/twin-relationship.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(node, node_schema)
    jsonschema.validate(rel, rel_schema)
