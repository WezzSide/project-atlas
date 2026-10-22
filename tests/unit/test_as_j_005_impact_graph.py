"""AS-J-005 — Derived impact graph (J5-FR-001..007).

Truth boundary: IMPACT GRAPH ≠ AUTOMATIC AUTHORITY.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from project_atlas.graph_relationships import RelationshipRecord
from project_atlas.impact_graph import (
    AUTHORITY_LEVEL,
    GENERATOR_ID,
    OUTPUT_RELATIVE,
    PACKAGE_ID,
    SCHEMA_KIND,
    TRUTH_BOUNDARY,
    ImpactGraphError,
    compile_impact_graph,
    impacted_entity_ids,
    inspect_impact_graph,
    promote_impact_path_forbidden,
    promote_impact_to_authority_forbidden,
    write_impact_graph,
)
from project_atlas.schema import validate_record
from project_atlas.secrets import scan_text


def _relationship(
    relationship_id: str,
    *,
    rel_type: str = "depends-on",
    source: str = "demo:a",
    target: str = "demo:b",
    project_id: str = "demo",
) -> RelationshipRecord:
    return RelationshipRecord(
        project_id=project_id,
        relationship_id=relationship_id,
        relationship_type=rel_type,
        source_entity_id=source,
        target_entity_id=target,
        source_graphify_id="g-src",
        target_graphify_id="g-tgt",
        link_quality="inferred",
        relationship_fingerprint="c" * 64,
        provenance={
            "graphify_artifact_refs": [
                {"relative_path": "graphify-out/graph.json", "sha256": "a" * 64}
            ],
            "graphify_edge_ids": [relationship_id],
            "source_graphify_ids": ["g-src"],
            "target_graphify_ids": ["g-tgt"],
            "supporting_source_docs": [],
        },
    )


def _seed_vault(vault: Path, records: list[RelationshipRecord]) -> None:
    for record in records:
        directory = vault / "generated" / "graph" / "relationships" / record.project_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{record.relationship_id}.json"
        path.write_text(record.to_json(), encoding="utf-8")


def test_j5_fr_001_package_constants() -> None:
    assert PACKAGE_ID == "AS-J-005"
    assert AUTHORITY_LEVEL == "derived"
    assert TRUTH_BOUNDARY == "IMPACT GRAPH ≠ AUTOMATIC AUTHORITY"
    assert GENERATOR_ID == "atlas-j-005"
    assert SCHEMA_KIND == "impact-graph"
    assert OUTPUT_RELATIVE.as_posix() == "generated/indexes/impact-graph.json"


def test_j5_fr_001_depends_on_reverse_impact(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(
        vault,
        [_relationship("rel-1", source="demo:a", target="demo:b")],
    )
    document = compile_impact_graph(vault)
    assert document["relationship_count"] == 1
    assert impacted_entity_ids(document, changed_entity_id="demo:b") == ["demo:a"]
    assert impacted_entity_ids(document, changed_entity_id="demo:a") == []


def test_j5_fr_001_conflicts_bidirectional(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(
        vault,
        [
            _relationship(
                "rel-cf",
                rel_type="conflicts-with",
                source="demo:x",
                target="demo:y",
            )
        ],
    )
    document = compile_impact_graph(vault)
    assert set(impacted_entity_ids(document, changed_entity_id="demo:x")) == {"demo:y"}
    assert set(impacted_entity_ids(document, changed_entity_id="demo:y")) == {"demo:x"}


def test_j5_fr_001_part_of_bidirectional(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(
        vault,
        [
            _relationship(
                "rel-part",
                rel_type="part-of",
                source="demo:child",
                target="demo:parent",
            )
        ],
    )
    document = compile_impact_graph(vault)
    assert "demo:parent" in impacted_entity_ids(document, changed_entity_id="demo:child")
    assert "demo:child" in impacted_entity_ids(document, changed_entity_id="demo:parent")


def test_j5_fr_003_deterministic_byte_identical(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(
        vault,
        [
            _relationship("rel-z", source="demo:z", target="demo:y", rel_type="derived-from"),
            _relationship("rel-a", source="demo:a", target="demo:b"),
        ],
    )
    first = compile_impact_graph(vault)
    second = compile_impact_graph(vault)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert "generated.at" not in json.dumps(first)
    assert first["generated"]["by"] == GENERATOR_ID


def test_j5_fr_003_schema_validation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault, [_relationship("rel-1")])
    document = compile_impact_graph(vault)
    validate_record(document, "impact-graph")


def test_j5_fr_003_write_under_generated_indexes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault, [_relationship("rel-1")])
    written = write_impact_graph(vault)
    assert written == "generated/indexes/impact-graph.json"
    path = vault / written
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["package_id"] == "AS-J-005"
    assert payload["authority"]["level"] == "derived"
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    write_impact_graph(vault)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_j5_fr_001_absent_relationships(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    document = compile_impact_graph(vault)
    assert document["relationship_count"] == 0
    assert document["edge_count"] == 0
    assert document["entity_count"] == 0
    assert document["entities"] == []
    assert document["edges"] == []
    assert impacted_entity_ids(document, changed_entity_id="demo:missing") == []


def test_j5_fr_004_no_trust_or_winners(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault, [_relationship("rel-1")])
    document = compile_impact_graph(vault)
    raw = json.dumps(document).lower()
    assert "trust_score" not in raw
    assert "authority_winner" not in raw
    assert "confidence_score" not in raw
    info = inspect_impact_graph(document)
    assert info["authority"]["level"] == "derived"
    assert "winner" not in info


def test_j5_fr_004_promote_to_authority_forbidden() -> None:
    with pytest.raises(ImpactGraphError, match="impact-to-authority-forbidden"):
        promote_impact_to_authority_forbidden()


def test_j5_fr_007_forbidden_write_prefixes() -> None:
    forbidden = [
        "generated/graph/relationships/demo/x.json",
        "generated/graph/projections/demo/relationships.md",
        "relationships/demo/edge.json",
        "claims/demo/claim.json",
        "apps/web/index.html",
        "../escape.json",
    ]
    for relative in forbidden:
        with pytest.raises(ImpactGraphError):
            promote_impact_path_forbidden(relative)


def test_j5_self_loop_ignored(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(
        vault,
        [
            _relationship(
                "loop",
                source="demo:a",
                target="demo:a",
            )
        ],
    )
    document = compile_impact_graph(vault)
    assert document["edge_count"] == 0
    assert document["entity_count"] == 0


def test_json_unicode_escape_entity_id_is_not_persisted(tmp_path: Path) -> None:
    """AS-SEC-SCAN-IMPACT-ENTITY-JSON-ESC-001: decoded entity ids must not persist."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    sha = "a" * 64
    vault = tmp_path / "vault"
    rel = vault / "generated" / "graph" / "relationships" / "harbor-api"
    rel.mkdir(parents=True)
    rec = {
        "schema_version": 1,
        "package_id": "AS-GRAPH-003",
        "project_id": "harbor-api",
        "relationship_id": "r1",
        "relationship_type": "depends-on",
        "source_entity_id": token,
        "target_entity_id": "svc-b",
        "link_quality": "inferred",
        "relationship_fingerprint": sha,
        "authority": {"level": "derived"},
        "status": "retained",
        "truth_boundary": "GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY",
        "provenance": {
            "graphify_artifact_refs": [
                {"relative_path": "generated/graph/x.json", "sha256": sha}
            ],
            "graphify_edge_ids": ["e1"],
            "source_graphify_ids": ["n1"],
            "target_graphify_ids": ["n2"],
            "supporting_source_docs": ["docs/a.md"],
        },
    }
    raw = json.dumps(rec).replace('"' + token + '"', '"\\u0041KIAAAAAAAAAAAAAAAAA"')
    (rel / "r1.json").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    document = compile_impact_graph(vault)
    write_impact_graph(vault, document=document)
    written = (vault / "generated" / "indexes" / "impact-graph.json").read_text(
        encoding="utf-8"
    )
    assert token not in written
    assert scan_text(written) == []
