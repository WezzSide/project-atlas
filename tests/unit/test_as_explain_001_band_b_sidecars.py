"""AS-EXPLAIN-001 Band B: graph explain sidecars over public GRAPH-002/003 contracts."""

from __future__ import annotations

import pytest

from project_atlas.explain_graph_sidecars import (
    TRUTH_BOUNDARY,
    ExplainGraphSidecarError,
    build_graph_absent_sidecar,
    build_sidecar_from_identity_explanation,
    build_sidecar_from_relationship,
    build_sidecar_from_resolved_node,
    sidecar_to_json,
)
from project_atlas.graph_relationships import RelationshipRecord
from project_atlas.graph_resolution import (
    IdentityExplanation,
    ResolvedNode,
    StepConsideration,
    resolve_node,
)
from project_atlas.schema import available_schemas, validate_record


def _refs(sha: str = "a" * 64) -> list[dict[str, str]]:
    return [{"relative_path": "graphify-out/nodes.jsonl", "sha256": sha}]


def test_explain_graph_sidecar_schema_registered() -> None:
    assert "explain-graph-sidecar" in available_schemas()


def test_b01_sidecar_from_resolved_node() -> None:
    node = {
        "id": "n-explicit",
        "type": "decision",
        "atlas_entity_id": "demo:decision:ship-it",
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    sidecar = build_sidecar_from_resolved_node(resolved)
    validate_record(sidecar, "explain-graph-sidecar")
    assert sidecar["package"] == "AS-EXPLAIN-001"
    assert sidecar["sidecar_kind"] == "resolved_node"
    assert sidecar["disposition"] == "present"
    assert sidecar["resolved_entity_id"] == "demo:decision:ship-it"
    assert sidecar["truth_boundary"] == TRUTH_BOUNDARY
    assert "trust_score" not in sidecar
    assert "confidence" not in sidecar
    assert sidecar_to_json(sidecar) == sidecar_to_json(sidecar)


def test_b02_sidecar_from_identity_explanation_categorical_label() -> None:
    explanation = IdentityExplanation(
        graphify_node_id="n-1",
        project_id="demo",
        winning_step="explicit_atlas_id",
        considered_steps=(
            StepConsideration(
                step="explicit_atlas_id",
                outcome="matched",
                reason="explicit atlas id present",
            ),
        ),
        confidence="explicit",
    )
    sidecar = build_sidecar_from_identity_explanation(explanation)
    validate_record(sidecar, "explain-graph-sidecar")
    assert sidecar["sidecar_kind"] == "identity_explanation"
    assert sidecar["identity_confidence_label"] == "explicit"
    assert "confidence" not in sidecar


def test_b03_sidecar_from_relationship() -> None:
    record = RelationshipRecord(
        project_id="demo",
        relationship_id="rel-1",
        relationship_type="depends-on",
        source_entity_id="demo:unknown:api",
        target_entity_id="demo:unknown:postgres",
        source_graphify_id="api",
        target_graphify_id="postgres",
        link_quality="supported",
        relationship_fingerprint="b" * 64,
        provenance={
            "graphify_artifact_refs": _refs(),
            "graphify_edge_ids": ["e-1"],
            "source_graphify_ids": ["api"],
            "target_graphify_ids": ["postgres"],
            "supporting_source_docs": [],
        },
    )
    expected = {"graphify-out/nodes.jsonl": "a" * 64}
    sidecar = build_sidecar_from_relationship(record, expected_artifact_hashes=expected)
    validate_record(sidecar, "explain-graph-sidecar")
    assert sidecar["sidecar_kind"] == "relationship"
    assert sidecar["disposition"] == "present"
    assert sidecar["link_quality"] == "supported"
    assert sidecar["artifact_refs"][0]["hash_status"] == "matched"
    # Must not elevate to authority language
    assert sidecar["truth_boundary"] == TRUTH_BOUNDARY


def test_b04_graph_absent_is_structured_not_query_failure() -> None:
    sidecar = build_graph_absent_sidecar(project_id="demo")
    validate_record(sidecar, "explain-graph-sidecar")
    assert sidecar["disposition"] == "absent"
    assert sidecar["sidecar_kind"] == "graph_absent"
    assert "graph_artifacts_absent" in sidecar["omissions"]


def test_b05_hash_mismatch_refuses_payload() -> None:
    resolved = ResolvedNode(
        project_id="demo",
        graphify_node_id="n-1",
        entity_class="decision",
        resolution_step="explicit_atlas_id",
        status="resolved",
        source_artifact_refs=(dict(_refs("a" * 64)[0]),),
        resolved_entity_id="demo:decision:ship-it",
    )
    sidecar = build_sidecar_from_resolved_node(
        resolved,
        expected_artifact_hashes={"graphify-out/nodes.jsonl": "c" * 64},
    )
    validate_record(sidecar, "explain-graph-sidecar")
    assert sidecar["disposition"] == "refused_hash_mismatch"
    assert sidecar["sidecar_kind"] == "hash_refused"
    assert sidecar["resolved_entity_id"] is None
    assert "payload_omitted_hash_mismatch" in sidecar["omissions"]


def test_b06_trust_score_smuggling_rejected() -> None:
    sidecar = build_graph_absent_sidecar(project_id="demo")
    smuggled = dict(sidecar)
    smuggled["confidence"] = 0.42
    with pytest.raises(ExplainGraphSidecarError, match="forbidden subjective score"):
        sidecar_to_json(smuggled)


def test_b07_band_a_receipt_schema_untouched() -> None:
    """Band A CLOSED — explain-receipt remains registered; Band B is additive."""
    kinds = available_schemas()
    assert "explain-receipt" in kinds
    assert "explain-graph-sidecar" in kinds
