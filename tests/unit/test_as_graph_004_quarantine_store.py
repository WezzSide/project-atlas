"""AS-GRAPH-004 — Durable quarantine store / health / incremental (G4-FX matrix)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from project_atlas.graph_quarantine import (
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    GraphQuarantineError,
    compute_input_content_hash,
    derive_health_state,
    inspect_quarantine_store,
    load_incremental_state,
    materialize_from_candidates,
    materialize_quarantine_store,
    write_quarantine_outputs,
)
from project_atlas.graph_relationships import (
    ArtifactRef,
    RelationshipStoreResult,
    handoff_quarantine_store,
    normalize_edges,
)
from project_atlas.graph_resolution import ResolvedNode
from project_atlas.schema import available_schemas, validate_record

REF = ArtifactRef(relative_path="graphify-out/graph.json", sha256="a" * 64)


def _resolved(graphify_id: str, entity_id: str) -> ResolvedNode:
    return ResolvedNode(
        project_id="demo",
        graphify_node_id=graphify_id,
        entity_class="unknown",
        resolution_step="graphify_stable",
        status="resolved",
        source_artifact_refs=({"relative_path": "n.jsonl", "sha256": "a" * 64},),
        resolved_entity_id=entity_id,
    )


def _quarantined(graphify_id: str, category: str = "unresolved-identity") -> ResolvedNode:
    return ResolvedNode(
        project_id="demo",
        graphify_node_id=graphify_id,
        entity_class="unknown",
        resolution_step="none",
        status="quarantine_candidate",
        source_artifact_refs=({"relative_path": "n.jsonl", "sha256": "a" * 64},),
        quarantine_category=category,  # type: ignore[arg-type]
    )


def _store_with_orphan() -> RelationshipStoreResult:
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
    return normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])


def _store_mixed() -> RelationshipStoreResult:
    nodes = [
        _resolved("a", "demo:a"),
        _resolved("b", "demo:b"),
        _quarantined("qnode"),
    ]
    edges = [
        {
            "id": "ok",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "confidence": "high",
            "source_documents": ["docs/arch.md"],
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "bad",
            "type": "depends-on",
            "source": "a",
            "target": "qnode",
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "orphan",
            "type": "part-of",
            "source": "a",
            "target": "missing",
            "_atlas_artifact_ref": REF.as_dict(),
        },
    ]
    return normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])


def test_graph_004_schemas_registered() -> None:
    kinds = available_schemas()
    assert "graph-quarantine-record" in kinds
    assert "graph-quarantine-receipt" in kinds
    assert "graph-health-snapshot" in kinds
    assert "graph-incremental-state" in kinds


def test_g4_fx_001_durable_quarantine_from_soft_candidates() -> None:
    store = _store_with_orphan()
    assert store.quarantined_count == 1
    result = materialize_quarantine_store(store)
    assert result.quarantined_count == 1
    record = result.records[0]
    payload = record.as_dict()
    assert payload["package_id"] == PACKAGE_ID
    assert payload["authority"]["level"] == AUTHORITY_LEVEL
    assert payload["status"] == "quarantined"
    assert payload["source_package_id"] == "AS-GRAPH-003"
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert "remediation" in payload
    assert "generated" in payload and "at" not in payload["generated"]
    validate_record(payload, "graph-quarantine-record")
    validate_record(result.receipt.as_dict(), "graph-quarantine-receipt")
    assert result.receipt.as_dict()["authority"]["canonical_override_allowed"] is False


def test_g4_fx_002_health_counters_deterministic() -> None:
    store = _store_mixed()
    first = materialize_quarantine_store(store)
    second = materialize_quarantine_store(store)
    assert first.health.to_json() == second.health.to_json()
    assert first.health.retained_count == 1
    assert first.health.quarantined_count == 2
    assert first.health.health_state == "degraded"
    assert first.health.category_counts["orphaned-endpoint"] == 1
    assert first.health.category_counts["quarantined-endpoint"] == 1
    validate_record(first.health.as_dict(), "graph-health-snapshot")
    assert derive_health_state(retained_count=1, quarantined_count=0) == "healthy"
    assert derive_health_state(retained_count=1, quarantined_count=11) == "unhealthy"


def test_g4_fx_003_incremental_skip_when_hash_unchanged(tmp_path: Path) -> None:
    store = _store_with_orphan()
    first = materialize_quarantine_store(store)
    assert first.incremental.refreshed is True
    validate_record(first.incremental.as_dict(), "graph-incremental-state")

    vault = tmp_path / "vault"
    vault.mkdir()
    written = write_quarantine_outputs(first, vault=vault)
    assert any(path.endswith("health.json") for path in written)
    assert any("/incremental/" in path for path in written)

    health_path = vault / "generated/graph/health/demo/health.json"
    before = (health_path.stat().st_mtime_ns, hashlib.sha256(health_path.read_bytes()).hexdigest())

    prior = load_incremental_state(vault, project_id="demo")
    assert prior is not None
    second = materialize_quarantine_store(store, prior_state=prior)
    assert second.incremental.refreshed is False
    assert second.incremental.input_content_hash == first.incremental.input_content_hash
    skipped = write_quarantine_outputs(second, vault=vault, skip_unchanged=True)
    assert skipped
    after = (health_path.stat().st_mtime_ns, hashlib.sha256(health_path.read_bytes()).hexdigest())
    assert after == before


def test_g4_fx_004_incremental_refresh_when_hash_changes(tmp_path: Path) -> None:
    store_a = _store_with_orphan()
    first = materialize_quarantine_store(store_a)
    vault = tmp_path / "vault"
    vault.mkdir()
    write_quarantine_outputs(first, vault=vault)
    prior = load_incremental_state(vault, project_id="demo")

    store_b = _store_mixed()
    second = materialize_quarantine_store(store_b, prior_state=prior)
    assert second.incremental.refreshed is True
    assert second.incremental.input_content_hash != first.incremental.input_content_hash
    assert second.quarantined_count == 2
    written = write_quarantine_outputs(second, vault=vault)
    quarantine_records = [
        path for path in written if "/quarantine/" in path and not path.endswith("/receipt.json")
    ]
    assert len(quarantine_records) == 2
    assert any(path.endswith("/receipt.json") for path in written)


def test_g4_fx_005_replay_byte_identical() -> None:
    store = _store_mixed()
    a = materialize_quarantine_store(store)
    b = materialize_quarantine_store(store)
    assert a.to_json() == b.to_json()
    assert compute_input_content_hash(store) == a.incremental.input_content_hash


def test_g4_fx_006_handoff_from_graph_relationships() -> None:
    store = _store_with_orphan()
    result = handoff_quarantine_store(store)
    assert result.quarantined_count == 1
    inspected = inspect_quarantine_store(result)
    assert inspected["package_id"] == PACKAGE_ID
    assert inspected["authority"] == AUTHORITY_LEVEL
    assert "password" not in json.dumps(inspected)


def test_g4_fx_007_empty_quarantine_healthy(tmp_path: Path) -> None:
    nodes = [_resolved("a", "demo:a"), _resolved("b", "demo:b")]
    edges = [
        {
            "id": "e1",
            "type": "depends-on",
            "source": "a",
            "target": "b",
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    store = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    result = materialize_quarantine_store(store)
    assert result.quarantined_count == 0
    assert result.health.health_state == "healthy"
    vault = tmp_path / "vault"
    vault.mkdir()
    written = write_quarantine_outputs(result, vault=vault)
    assert written == [
        "generated/graph/health/demo/health.json",
        "generated/graph/incremental/demo/state.json",
        "generated/graph/quarantine/demo/receipt.json",
    ]


def test_g4_fx_008_project_id_unsafe_fail_closed() -> None:
    store = _store_with_orphan()
    with pytest.raises(GraphQuarantineError, match="project-id-unsafe"):
        materialize_from_candidates(store.quarantine, project_id="../escape")
