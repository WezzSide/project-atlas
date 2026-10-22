"""AS-GRAPH-002 — Deterministic graph entity resolution (derived-only)."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

import pytest

from project_atlas.claim_identity import v2_claim_id
from project_atlas.graph_acceptance import accept_graphify_artifacts
from project_atlas.graph_resolution import (
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    GraphResolutionError,
    MappingTable,
    inspect_resolution,
    load_accepted_nodes,
    resolve_from_acceptance,
    resolve_node,
    resolve_nodes,
    write_resolution_outputs,
)
from project_atlas.schema import available_schemas, validate_record
from project_atlas.secrets import scan_text
from project_atlas.source_identity import lineage_id, validate_project_uuid

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "graphify-present"
PROJECT_UUID = "12345678-1234-4234-8234-123456789abc"


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
    return [
        {
            "relative_path": "graphify-out/nodes.jsonl",
            "sha256": "a" * 64,
        }
    ]


def test_graph_002_schemas_registered() -> None:
    kinds = available_schemas()
    assert "graph-resolved-node" in kinds
    assert "graph-identity-explanation" in kinds


def test_precedence_explicit_atlas_id() -> None:
    node = {
        "id": "n-explicit",
        "type": "decision",
        "atlas_entity_id": "demo:decision:ship-it",
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "resolved"
    assert resolved.resolution_step == "explicit_atlas_id"
    assert resolved.resolved_entity_id == "demo:decision:ship-it"
    payload = resolved.as_dict()
    assert payload["authority"]["level"] == AUTHORITY_LEVEL
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    validate_record(payload, "graph-resolved-node")
    assert resolved.explanation is not None
    assert resolved.explanation.confidence == "explicit"
    validate_record(resolved.explanation.as_dict(), "graph-identity-explanation")


def test_precedence_durable_core_source_lineage() -> None:
    sline = lineage_id(PROJECT_UUID, "docs/arch.md", "b" * 64, 1)
    node = {"id": "n-lineage", "type": "document", "source_lineage_id": sline}
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "resolved"
    assert resolved.resolution_step == "durable_core_id"
    assert resolved.resolved_entity_id == sline
    assert resolved.explanation is not None
    assert resolved.explanation.confidence == "durable-core"


def test_precedence_durable_core_claim_id() -> None:
    cid = v2_claim_id(PROJECT_UUID, "sline-abc", "decision", "decision", "id:x")
    assert cid.startswith("claim-")
    node = {"id": "n-claim", "type": "decision", "claim_id": cid}
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "resolved"
    assert resolved.resolution_step == "durable_core_id"
    assert resolved.resolved_entity_id == cid


def test_precedence_durable_core_project_uuid() -> None:
    uuid = validate_project_uuid(PROJECT_UUID)
    node = {"id": "n-proj", "type": "component", "project_uuid": uuid}
    resolved = resolve_node(
        node,
        project_id="demo",
        source_artifact_refs=_refs(),
        local_project_uuid=uuid,
    )
    assert resolved.status == "resolved"
    assert resolved.resolution_step == "durable_core_id"
    assert resolved.resolved_entity_id == uuid
    assert resolved.entity_class == "project"


def test_precedence_mapping_table() -> None:
    table = {"n-map": {"resolved_entity_id": "demo:requirement:r1", "entity_class": "requirement"}}
    node = {"id": "n-map", "type": "unknown-type"}
    resolved = resolve_node(
        node, project_id="demo", mapping_table=table, source_artifact_refs=_refs()
    )
    assert resolved.status == "resolved"
    assert resolved.resolution_step == "mapping_table"
    assert resolved.resolved_entity_id == "demo:requirement:r1"
    assert resolved.entity_class == "requirement"
    assert resolved.explanation is not None
    assert resolved.explanation.confidence == "mapped"


def test_precedence_graphify_stable() -> None:
    node = {"id": "api", "type": "component"}
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "resolved"
    assert resolved.resolution_step == "graphify_stable"
    assert resolved.resolved_entity_id == "demo:unknown:api"
    assert resolved.entity_class == "unknown"
    assert resolved.explanation is not None
    assert resolved.explanation.confidence == "graphify-stable"


def test_ambiguous_explicit_quarantine() -> None:
    node = {
        "id": "n-amb",
        "type": "decision",
        "atlas_entity_id": "demo:decision:a",
        "atlas_id": "demo:decision:b",
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "ambiguous-identity"
    assert "resolved_entity_id" not in resolved.as_dict()
    validate_record(resolved.as_dict(), "graph-resolved-node")


def test_unresolved_quarantine() -> None:
    node = {"id": "???", "type": "document"}
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "unresolved-identity"
    assert resolved.resolution_step == "none"
    assert resolved.explanation is not None
    assert resolved.explanation.confidence == "none"
    assert resolved.explanation.winning_step == "none"


def test_cross_project_forbidden() -> None:
    node = {
        "id": "n-xproj",
        "type": "project",
        "atlas_entity_id": "other-project:project:root",
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "cross-project-resolution-forbidden"


def test_multiple_durable_identities_quarantine() -> None:
    sline = lineage_id(PROJECT_UUID, "docs/a.md", "c" * 64, 1)
    cid = v2_claim_id(PROJECT_UUID, sline, "decision", "decision", "id:y")
    node = {
        "id": "n-multi",
        "type": "decision",
        "source_lineage_id": sline,
        "claim_id": cid,
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "ambiguous-identity"


def test_mapping_table_malformed_fail_closed() -> None:
    with pytest.raises(GraphResolutionError, match="mapping-table-malformed"):
        MappingTable.from_mapping(
            "demo",
            {
                "mappings": [
                    {"graphify_node_id": "a", "resolved_entity_id": "demo:a"},
                    {"graphify_node_id": "a", "resolved_entity_id": "demo:b"},
                ]
            },
        )


def test_mapping_table_cross_project_fail_closed() -> None:
    with pytest.raises(GraphResolutionError, match="cross-project-resolution-forbidden"):
        MappingTable.from_mapping(
            "demo",
            {"project_id": "other", "mappings": []},
        )


def test_derived_authority_only() -> None:
    resolved = resolve_node(
        {"id": "n1", "type": "requirement"},
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.as_dict()["authority"]["level"] == "derived"
    assert resolved.as_dict()["package_id"] == PACKAGE_ID


def test_malformed_node_hard_reject() -> None:
    with pytest.raises(GraphResolutionError, match="malformed-accepted-node"):
        resolve_node({"type": "document"}, project_id="demo")


def test_provenance_required_on_emit() -> None:
    resolved = resolve_node(
        {"id": "n-prov", "type": "document"},
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.source_artifact_refs
    assert resolved.as_dict()["source_artifact_refs"][0]["sha256"] == "a" * 64


def test_determinism_replay() -> None:
    nodes = [
        {"id": "z", "type": "document"},
        {"id": "a", "type": "decision", "atlas_entity_id": "demo:decision:a"},
        {"id": "m", "type": "component"},
    ]
    table = {"m": "demo:document:mapped"}
    first = resolve_nodes(
        nodes, project_id="demo", mapping_table=table, source_artifact_refs=_refs()
    )
    second = resolve_nodes(
        nodes, project_id="demo", mapping_table=table, source_artifact_refs=_refs()
    )
    assert first.to_json() == second.to_json()
    assert [n.graphify_node_id for n in first.nodes] == ["a", "m", "z"]


def test_path_escape_and_relationships_forbidden(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = resolve_nodes(
        [{"id": "n1", "type": "document"}],
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    # Normal write under derived paths succeeds.
    written = write_resolution_outputs(result, vault=vault)
    assert any(path.startswith("generated/graph/resolved/demo/") for path in written)
    assert (vault / "generated/graph/resolved/demo/explanations").is_dir()

    from project_atlas.graph_resolution import _safe_vault_relative

    with pytest.raises(GraphResolutionError, match="path-escape"):
        _safe_vault_relative(vault, "../escape.json")
    with pytest.raises(GraphResolutionError, match="path-policy-forbidden"):
        _safe_vault_relative(vault, "relationships/nodes.json")
    with pytest.raises(GraphResolutionError, match="path-policy-forbidden"):
        _safe_vault_relative(vault, "state/authoritative-state/x.json")
    with pytest.raises(GraphResolutionError, match="path-policy-forbidden"):
        _safe_vault_relative(vault, "generated/graph/acceptance/x.json")


def test_no_truth_layer_mutation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for relative in (
        "claims/claim.json",
        "state/current-state/demo.json",
        "state/authoritative-state/demo.json",
        "relationships/nodes.json",
        "generated/query/cache.json",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"sentinel":true}\n', encoding="utf-8")

    before = {
        relative: ((vault / relative).stat().st_mtime_ns, _sha256(vault / relative))
        for relative in (
            "claims/claim.json",
            "state/current-state/demo.json",
            "state/authoritative-state/demo.json",
            "relationships/nodes.json",
            "generated/query/cache.json",
        )
    }
    time.sleep(0.01)
    result = resolve_nodes(
        [{"id": "iso", "type": "document"}],
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    write_resolution_outputs(result, vault=vault)
    for relative, (mtime_ns, digest) in before.items():
        path = vault / relative
        assert path.stat().st_mtime_ns == mtime_ns
        assert _sha256(path) == digest


def test_as_id_001_consume_only_formula_unchanged() -> None:
    expected = lineage_id(PROJECT_UUID, "src/a.md", "d" * 64, 2)
    again = lineage_id(PROJECT_UUID, "src/a.md", "d" * 64, 2)
    assert expected == again
    assert expected.startswith("sline-")
    node = {"id": "n-id", "type": "document", "source_lineage_id": expected}
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.resolved_entity_id == expected


def test_consume_graph_001_acceptance(tmp_path: Path) -> None:
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    manifest = _manifest_for(root)
    receipt, result = resolve_from_acceptance(
        project_root=root,
        manifest=manifest,
        strict=True,
    )
    assert receipt.accepted_count == 4
    assert result.resolved_count >= 1
    assert all(item.as_dict()["authority"]["level"] == "derived" for item in result.nodes)
    summary = inspect_resolution(result)
    assert summary["package_id"] == PACKAGE_ID
    assert summary["truth_boundary"] == TRUTH_BOUNDARY
    assert "winning_steps" in summary


def test_load_accepted_nodes_dedupes_envelope_and_jsonl(tmp_path: Path) -> None:
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    manifest = _manifest_for(root)
    receipt = accept_graphify_artifacts(project_root=root, manifest=manifest, strict=True)
    nodes, refs = load_accepted_nodes(project_root=root, receipt=receipt)
    assert refs
    ids = [_id for _id in (n.get("id") or n.get("node_id") for n in nodes)]
    assert len(ids) == len(set(ids))
    assert "api" in ids


def test_explicit_outranks_mapping_and_graphify() -> None:
    node = {
        "id": "api",
        "type": "component",
        "atlas_entity_id": "demo:document:api-canonical",
    }
    resolved = resolve_node(
        node,
        project_id="demo",
        mapping_table={"api": "demo:document:from-map"},
        source_artifact_refs=_refs(),
    )
    assert resolved.resolution_step == "explicit_atlas_id"
    assert resolved.resolved_entity_id == "demo:document:api-canonical"
    assert resolved.explanation is not None
    skipped = [c for c in resolved.explanation.considered_steps if c.outcome == "skipped"]
    assert any(c.step == "mapping_table" for c in skipped)
    assert any(c.step == "graphify_stable" for c in skipped)


def test_graph_001_regression_still_accepts(tmp_path: Path) -> None:
    """AS-GRAPH-002 must not redefine or break AS-GRAPH-001 acceptance."""
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    receipt = accept_graphify_artifacts(
        project_root=root,
        manifest=_manifest_for(root),
        strict=True,
    )
    assert receipt.accepted_count == 4
    assert receipt.semantic_status == "disabled"
    validate_record(receipt.as_dict(), "graph-acceptance-receipt")


def test_optional_write_validates_under_atlas_validate(tmp_path: Path) -> None:
    from project_atlas.scaffold import create_scaffold
    from project_atlas.validation import validate

    vault = tmp_path / "vault"
    create_scaffold(vault)
    result = resolve_nodes(
        [{"id": "doc1", "type": "document"}],
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    write_resolution_outputs(result, vault=vault)
    report = validate(vault)
    graph_errors = [e for e in report["errors"] if "graph resolution" in e]
    assert graph_errors == []


def test_yaml_escaped_project_id_is_not_persisted(tmp_path: Path) -> None:
    """AS-SEC-SCAN-GRAPH-YAML-PROJECTID-001: YAML \\u project.id must not persist."""
    from project_atlas.graph_acceptance import GraphAcceptanceError
    from project_atlas.scaffold import create_scaffold

    token = "AKIAAAAAAAAAAAAAAAAA"
    project_root = tmp_path / "proj"
    out = project_root / "graphify-out"
    out.mkdir(parents=True)
    marker = project_root / ".atlas-project.yaml"
    raw_marker = 'schema_version: 1\nproject:\n  id: "\\u0041KIAAAAAAAAAAAAAAAAA"\n'
    marker.write_text(raw_marker, encoding="utf-8")
    nodes_path = out / "nodes.jsonl"
    nodes_path.write_text('{"id":"n1","type":"document"}\n', encoding="utf-8")
    sha = hashlib.sha256(nodes_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "sources": [
            {
                "source_id": "source-nodes",
                "path": "graphify-out/nodes.jsonl",
                "media_type": "application/json",
                "sha256": sha,
                "size_bytes": nodes_path.stat().st_size,
                "classification_state": "unclassified",
                "authority": {"level": "derived"},
            }
        ],
    }
    vault = tmp_path / "vault"
    create_scaffold(vault)
    assert scan_text(raw_marker) == []
    assert token not in raw_marker
    with pytest.raises((GraphAcceptanceError, GraphResolutionError), match="secret-content"):
        _receipt, resolution = resolve_from_acceptance(
            project_root=project_root,
            manifest=manifest,
            strict=False,
        )
        write_resolution_outputs(resolution, vault=vault)
    leaked = [
        path.as_posix()
        for path in vault.rglob("*")
        if path.is_file() and token in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == []
    assert not (vault / "generated" / "graph" / "resolved" / token).exists()
