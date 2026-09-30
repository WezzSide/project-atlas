"""AS-GRAPH-003 — Canonical derived relationship store (G3-FX matrix)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from project_atlas.graph_relationships import (
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    ArtifactRef,
    GraphRelationshipError,
    normalize_edges,
    normalize_relationship_type,
    relationship_fingerprint,
    store_from_acceptance,
    write_relationship_outputs,
)
from project_atlas.graph_resolution import ResolvedNode
from project_atlas.schema import available_schemas, validate_record

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "graphify-present"
REF = ArtifactRef(relative_path="graphify-out/graph.json", sha256="a" * 64)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_for(root: Path, project_id: str = "graphify-present") -> dict[str, object]:
    sources: list[dict[str, object]] = []
    for path in sorted((root / "graphify-out").iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        sources.append(
            {
                "source_id": f"source-{path.stem}",
                "path": relative,
                "media_type": "application/json",
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "classification_state": "unclassified",
                "authority": {"level": "derived"},
            }
        )
    return {"schema_version": 1, "project_id": project_id, "sources": sources}


def _refs() -> list[dict[str, str]]:
    return [{"relative_path": "graphify-out/nodes.jsonl", "sha256": "a" * 64}]


def _resolved(
    graphify_id: str,
    entity_id: str,
    *,
    project_id: str = "demo",
) -> ResolvedNode:
    return ResolvedNode(
        project_id=project_id,
        graphify_node_id=graphify_id,
        entity_class="unknown",
        resolution_step="graphify_stable",
        status="resolved",
        source_artifact_refs=(dict(_refs()[0]),),
        resolved_entity_id=entity_id,
    )


def _quarantined(
    graphify_id: str,
    category: str = "unresolved-identity",
    *,
    project_id: str = "demo",
) -> ResolvedNode:
    return ResolvedNode(
        project_id=project_id,
        graphify_node_id=graphify_id,
        entity_class="unknown",
        resolution_step="none",
        status="quarantine_candidate",
        source_artifact_refs=(dict(_refs()[0]),),
        quarantine_category=category,  # type: ignore[arg-type]
    )


def test_graph_003_schemas_registered() -> None:
    kinds = available_schemas()
    assert "graph-relationship" in kinds
    assert "graph-relationship-quarantine" in kinds


def test_g3_fx_001_happy_path_derived_edges() -> None:
    nodes = [
        _resolved("api", "demo:unknown:api"),
        _resolved("postgres", "demo:unknown:postgres"),
    ]
    edges = [
        {
            "id": "e1",
            "type": "depends-on",
            "source": "api",
            "target": "postgres",
            "confidence": "high",
            "source_documents": ["docs/arch.md"],
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 1
    assert result.quarantined_count == 0
    record = result.relationships[0]
    payload = record.as_dict()
    assert payload["authority"]["level"] == AUTHORITY_LEVEL
    assert payload["package_id"] == PACKAGE_ID
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert payload["link_quality"] == "verified"
    assert payload["relationship_type"] == "depends-on"
    validate_record(payload, "graph-relationship")


def test_g3_fx_002_link_quality_matrix() -> None:
    nodes = [
        _resolved("a", "demo:a"),
        _resolved("b", "demo:b"),
        _resolved("c", "demo:c"),
        _resolved("d", "demo:d"),
    ]
    edges = [
        {
            "id": "verified",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "confidence": "primary",
            "source_documents": ["doc.md"],
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "supported",
            "type": "documents",
            "source": "a",
            "target": "c",
            "source_documents": ["doc.md"],
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "inferred",
            "type": "validates",
            "source": "a",
            "target": "d",
            "_atlas_artifact_ref": REF.as_dict(),
        },
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    hist = result.link_quality_histogram
    assert hist["verified"] == 1
    assert hist["supported"] == 1
    assert hist["inferred"] == 1
    qualities = {item.link_quality for item in result.relationships}
    assert qualities == {"verified", "supported", "inferred"}


def test_g3_fx_003_missing_endpoint_orphaned() -> None:
    nodes = [_resolved("api", "demo:api")]
    edges = [
        {
            "id": "orphan",
            "type": "depends-on",
            "source": "api",
            "target": "missing",
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 0
    assert result.quarantined_count == 1
    assert result.quarantine[0].category == "orphaned-endpoint"
    assert result.link_quality_histogram["orphaned"] == 1
    validate_record(result.quarantine[0].as_dict(), "graph-relationship-quarantine")


def test_g3_fx_004_compatible_fingerprint_collapse() -> None:
    nodes = [_resolved("a", "demo:a"), _resolved("b", "demo:b")]
    edges = [
        {
            "id": "e-a",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "source_documents": ["one.md"],
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "e-b",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "source_documents": ["two.md"],
            "_atlas_artifact_ref": {
                "relative_path": "graphify-out/edges.json",
                "sha256": "b" * 64,
            },
        },
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 1
    assert result.quarantined_count == 0
    prov = result.relationships[0].provenance
    assert set(prov["graphify_edge_ids"]) == {"e-a", "e-b"}
    assert set(prov["supporting_source_docs"]) == {"one.md", "two.md"}
    assert len(prov["graphify_artifact_refs"]) == 2


def test_g3_fx_005_incompatible_duplicate_no_lww() -> None:
    nodes = [_resolved("a", "demo:a"), _resolved("b", "demo:b")]
    edges = [
        {
            "id": "e1",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "e2",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "_atlas_artifact_ref": REF.as_dict(),
            "_atlas_force_incompatible": True,
        },
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 0
    assert result.quarantined_count == 1
    assert result.quarantine[0].category == "incompatible-duplicate"


def test_g3_fx_006_quarantined_endpoint_rejected() -> None:
    nodes = [
        _resolved("api", "demo:api"),
        _quarantined("bad", "ambiguous-identity"),
    ]
    edges = [
        {
            "id": "e",
            "type": "depends-on",
            "source": "api",
            "target": "bad",
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 0
    assert result.quarantine[0].category == "quarantined-endpoint"


def test_g3_fx_007_cross_project_endpoint_fail_closed() -> None:
    nodes = [_resolved("api", "demo:api"), _resolved("db", "demo:db")]
    edges = [
        {
            "id": "e",
            "type": "depends-on",
            "source": "api",
            "target": "db",
            "project_id": "other-project",
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 0
    assert result.quarantine[0].category == "cross-project-endpoint"


def test_g3_fx_008_replay_idempotent(tmp_path: Path) -> None:
    nodes = [_resolved("api", "demo:api"), _resolved("db", "demo:db")]
    edges = [
        {
            "id": "e",
            "type": "depends-on",
            "source": "api",
            "target": "db",
            "confidence": "high",
            "source_documents": ["arch.md"],
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    first = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    second = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert first.to_json() == second.to_json()

    vault = tmp_path / "vault"
    vault.mkdir()
    written_a = write_relationship_outputs(first, vault=vault)
    bytes_a = {rel: (vault / rel).read_bytes() for rel in written_a}
    written_b = write_relationship_outputs(second, vault=vault)
    assert written_a == written_b
    for rel in written_b:
        assert (vault / rel).read_bytes() == bytes_a[rel]


def test_g3_fx_009_forbidden_tree_census(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    relatives = (
        "claims/claim.json",
        "state/current-state/demo.json",
        "state/authoritative-state/demo.json",
        "relationships/nodes.json",
        "generated/query/cache.json",
    )
    before: dict[str, tuple[int, str]] = {}
    for relative in relatives:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"sentinel":true}\n', encoding="utf-8")
        before[relative] = (path.stat().st_mtime_ns, _sha256(path))

    nodes = [_resolved("api", "demo:api"), _resolved("db", "demo:db")]
    edges = [
        {
            "id": "e",
            "type": "conflicts-with",
            "source": "api",
            "target": "db",
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    result = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    assert result.retained_count == 1
    assert result.relationships[0].relationship_type == "conflicts-with"
    written = write_relationship_outputs(result, vault=vault)
    assert written
    assert all(rel.startswith("generated/graph/relationships/") for rel in written)

    for relative, (mtime_ns, digest) in before.items():
        path = vault / relative
        assert path.stat().st_mtime_ns == mtime_ns
        assert _sha256(path) == digest
    # No Core claim conflict synthesis from conflicts-with.
    assert not (vault / "claims").joinpath("conflict.json").exists()


def test_g3_fx_010_edge_fanout_capacity_fail_closed() -> None:
    nodes = [_resolved("api", "demo:api"), _resolved("db", "demo:db")]
    edges = [
        {
            "id": f"e-{index}",
            "type": "depends-on",
            "source": "api",
            "target": "db",
            "_atlas_artifact_ref": REF.as_dict(),
        }
        for index in range(5)
    ]
    with pytest.raises(GraphRelationshipError, match="edge-capacity-exceeded"):
        normalize_edges(
            edges,
            project_id="demo",
            resolution=nodes,
            artifact_refs=[REF],
            max_edges=3,
        )


def test_unknown_type_maps_to_extension_not_silent_remap() -> None:
    assert normalize_relationship_type("invokes") == ("extension", "invokes")
    assert normalize_relationship_type("depends_on") == ("depends-on", None)


def test_fingerprint_deterministic() -> None:
    a = relationship_fingerprint(
        relationship_type="depends-on",
        source_entity_id="x",
        target_entity_id="y",
    )
    b = relationship_fingerprint(
        relationship_type="depends-on",
        source_entity_id="x",
        target_entity_id="y",
    )
    assert a == b
    assert len(a) == 64


def test_store_from_acceptance_fixture() -> None:
    root = FIXTURE
    _receipt, _resolution, store = store_from_acceptance(
        project_root=root,
        manifest=_manifest_for(root),
        strict=True,
    )
    assert store.project_id == "graphify-present"
    assert store.retained_count >= 1
    assert any(item.link_quality == "verified" for item in store.relationships)
    assert any(item.category == "orphaned-endpoint" for item in store.quarantine)
    for record in store.relationships:
        validate_record(record.as_dict(), "graph-relationship")
        assert record.as_dict()["authority"]["level"] == "derived"
