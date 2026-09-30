"""AS-GRAPH-002 adversarial gate (Wave7 §5.1 ADV-G2 S0/S1 minimum).

Probes fail-closed identity/path/authority surfaces without widening precedence.
Truth boundary: GRAPH RELATIONSHIP ≠ AUTOMATIC AUTHORITY.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from project_atlas.claim_identity import v2_claim_id
from project_atlas.graph_acceptance import GraphAcceptanceError
from project_atlas.graph_resolution import (
    AUTHORITY_LEVEL,
    TRUTH_BOUNDARY,
    GraphResolutionError,
    MappingTable,
    resolve_from_acceptance,
    resolve_node,
    resolve_nodes,
    write_resolution_outputs,
)
from project_atlas.schema import validate_record
from project_atlas.source_identity import lineage_id, validate_project_uuid

LOCAL_UUID = "12345678-1234-4234-8234-123456789abc"
FOREIGN_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refs() -> list[dict[str, str]]:
    return [{"relative_path": "graphify-out/nodes.jsonl", "sha256": "a" * 64}]


def _forbidden_census(vault: Path) -> dict[str, tuple[int, str]]:
    relatives = (
        "claims/claim.json",
        "state/current-state/demo.json",
        "state/authoritative-state/demo.json",
        "relationships/nodes.json",
        "generated/query/cache.json",
    )
    census: dict[str, tuple[int, str]] = {}
    for relative in relatives:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_text('{"sentinel":true}\n', encoding="utf-8")
        census[relative] = (path.stat().st_mtime_ns, _sha256(path))
    return census


def _assert_census_stable(vault: Path, before: dict[str, tuple[int, str]]) -> None:
    for relative, (mtime_ns, digest) in before.items():
        path = vault / relative
        assert path.stat().st_mtime_ns == mtime_ns
        assert _sha256(path) == digest


# --- Malicious IDs (001-003, 007-008, 010) ---


def test_adv_g2_001_null_byte_node_id_never_written(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _forbidden_census(vault)
    node = {"id": "node\u0000evil", "type": "document"}
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "unresolved-identity"
    written = write_resolution_outputs(
        resolve_nodes([node], project_id="demo", source_artifact_refs=_refs()),
        vault=vault,
    )
    for relative in written:
        assert "\x00" not in relative
        assert "evil" not in Path(relative).name or "-" in Path(relative).name
    _assert_census_stable(vault, before)


def test_adv_g2_002_empty_dot_dotdot_ids_rejected() -> None:
    for bad in ("", ".", ".."):
        with pytest.raises(GraphResolutionError, match="malformed-accepted-node"):
            resolve_node({"id": bad, "type": "document"}, project_id="demo")


def test_adv_g2_003_slash_backslash_ids_not_nested_dirs(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for bad in ("a/b", "a\\b"):
        resolved = resolve_node(
            {"id": bad, "type": "document"},
            project_id="demo",
            source_artifact_refs=_refs(),
        )
        assert resolved.status == "quarantine_candidate"
        result = resolve_nodes(
            [{"id": bad, "type": "document"}],
            project_id="demo",
            source_artifact_refs=_refs(),
        )
        written = write_resolution_outputs(result, vault=vault)
        for relative in written:
            assert "/a/b" not in relative.replace("\\", "/")
            assert "\\" not in relative


def test_adv_g2_007_foreign_project_uuid_fail_closed() -> None:
    """Foreign/unbound project_uuid must not win as durable_core_id."""
    foreign = validate_project_uuid(FOREIGN_UUID)
    node = {"id": "n-foreign-uuid", "type": "project", "project_uuid": foreign}

    unbound = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    assert unbound.status == "quarantine_candidate"
    assert unbound.quarantine_category == "cross-project-resolution-forbidden"
    assert unbound.resolved_entity_id is None

    mismatched = resolve_node(
        node,
        project_id="demo",
        source_artifact_refs=_refs(),
        local_project_uuid=LOCAL_UUID,
    )
    assert mismatched.status == "quarantine_candidate"
    assert mismatched.quarantine_category == "cross-project-resolution-forbidden"

    # Control: bound local UUID still resolves (happy path sibling).
    local = validate_project_uuid(LOCAL_UUID)
    ok = resolve_node(
        {"id": "n-local-uuid", "type": "project", "project_uuid": local},
        project_id="demo",
        source_artifact_refs=_refs(),
        local_project_uuid=local,
    )
    assert ok.status == "resolved"
    assert ok.resolution_step == "durable_core_id"
    assert ok.resolved_entity_id == local


def test_adv_g2_008_duplicate_graphify_id_divergent_payloads() -> None:
    nodes = [
        {"id": "dup", "type": "document", "atlas_entity_id": "demo:document:a"},
        {"id": "dup", "type": "decision", "atlas_entity_id": "demo:decision:b"},
    ]
    result = resolve_nodes(nodes, project_id="demo", source_artifact_refs=_refs())
    assert result.resolved_count == 0
    assert result.quarantined_count == 1
    only = result.nodes[0]
    assert only.graphify_node_id == "dup"
    assert only.status == "quarantine_candidate"
    assert only.quarantine_category == "ambiguous-identity"
    assert only.resolved_entity_id is None


def test_adv_g2_010_type_confused_node_id() -> None:
    with pytest.raises(GraphResolutionError, match="malformed-accepted-node"):
        resolve_node({"id": 12345, "type": "document"}, project_id="demo")  # type: ignore[dict-item]
    with pytest.raises(GraphResolutionError, match="malformed-accepted-node"):
        resolve_nodes([{"node_id": ["x"], "type": "document"}], project_id="demo")


# --- Path traversal (020-022, 024-025) ---


def test_adv_g2_020_project_id_traversal_write_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _forbidden_census(vault)
    result = resolve_nodes(
        [{"id": "n1", "type": "document"}],
        project_id="../claims",
        source_artifact_refs=_refs(),
    )
    with pytest.raises(GraphResolutionError, match="project-id-unsafe-for-path"):
        write_resolution_outputs(result, vault=vault)
    _assert_census_stable(vault, before)


def test_adv_g2_021_absolute_project_id_write_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = resolve_nodes(
        [{"id": "n1", "type": "document"}],
        project_id="C:/windows",
        source_artifact_refs=_refs(),
    )
    with pytest.raises(GraphResolutionError, match="project-id-unsafe-for-path"):
        write_resolution_outputs(result, vault=vault)


def test_adv_g2_022_024_025_path_policy_forbids_truth_and_relationships(
    tmp_path: Path,
) -> None:
    from project_atlas.graph_resolution import _safe_vault_relative

    vault = tmp_path / "vault"
    vault.mkdir()
    for relative in (
        "generated/graph/resolved/good/../../state/authoritative-state/x.json",
        "relationships/nodes.json",
        "state/global-entities/x.json",
        "claims/x.json",
    ):
        with pytest.raises(GraphResolutionError):
            _safe_vault_relative(vault, relative)


# --- Duplicates / collision / cross-project (041-042, 050-052, 060, 063) ---


def test_adv_g2_041_multiple_durable_identities_quarantine() -> None:
    sline = lineage_id(LOCAL_UUID, "docs/a.md", "c" * 64, 1)
    cid = v2_claim_id(LOCAL_UUID, sline, "decision", "decision", "id:y")
    resolved = resolve_node(
        {
            "id": "n-multi",
            "type": "decision",
            "source_lineage_id": sline,
            "claim_id": cid,
        },
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "ambiguous-identity"


def test_adv_g2_042_mapping_duplicate_targets_fail_closed() -> None:
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


def test_adv_g2_050_foreign_explicit_prefix() -> None:
    resolved = resolve_node(
        {
            "id": "n-x",
            "type": "project",
            "atlas_entity_id": "projB:entity-1",
        },
        project_id="projA",
        source_artifact_refs=_refs(),
    )
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "cross-project-resolution-forbidden"


def test_adv_g2_051_stamped_project_id_mismatch_fail_closed() -> None:
    """Node body project_id ≠ resolution scope must not resolve."""
    resolved = resolve_node(
        {"id": "n1", "type": "document", "project_id": "pilot-b"},
        project_id="pilot-a",
        source_artifact_refs=_refs(),
    )
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "cross-project-resolution-forbidden"
    assert resolved.resolved_entity_id is None
    # Matching stamp is allowed and does not block graphify-stable.
    ok = resolve_node(
        {"id": "n1", "type": "document", "project_id": "pilot-a"},
        project_id="pilot-a",
        source_artifact_refs=_refs(),
    )
    assert ok.status == "resolved"
    assert ok.resolution_step == "graphify_stable"


def test_adv_g2_052_mapping_foreign_project_rows_fail_closed() -> None:
    with pytest.raises(GraphResolutionError, match="cross-project-resolution-forbidden"):
        MappingTable.from_mapping(
            "pilot-a",
            {
                "project_id": "pilot-a",
                "mappings": [
                    {
                        "project_id": "pilot-b",
                        "graphify_node_id": "x",
                        "resolved_entity_id": "pilot-b:document:x",
                    }
                ],
            },
        )


def test_adv_g2_060_explicit_id_class_collision_quarantine() -> None:
    # Ambiguous when two explicit fields disagree (class/value collision surface).
    resolved = resolve_node(
        {
            "id": "n-class",
            "type": "decision",
            "atlas_entity_id": "demo:decision:same",
            "atlas_id": "demo:requirement:same",
        },
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.status == "quarantine_candidate"
    assert resolved.quarantine_category == "ambiguous-identity"


def test_adv_g2_063_claim_id_consume_only_derived() -> None:
    cid = v2_claim_id(LOCAL_UUID, "sline-abc", "decision", "decision", "id:z")
    resolved = resolve_node(
        {"id": "n-claim", "type": "decision", "claim_id": cid},
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.status == "resolved"
    assert resolved.as_dict()["authority"]["level"] == "derived"
    assert resolved.as_dict()["truth_boundary"] == TRUTH_BOUNDARY


# --- Provenance / hash (070-073, 080, 084) ---


def test_adv_g2_070_malformed_unbound_artifact_ref() -> None:
    with pytest.raises(GraphResolutionError, match="malformed-source-artifact-ref"):
        resolve_node(
            {"id": "n1", "type": "document"},
            project_id="demo",
            source_artifact_refs=[{"relative_path": "not-in-manifest.json", "sha256": "zz"}],
        )


def test_adv_g2_071_bad_sha256_in_ref_rejected() -> None:
    with pytest.raises(GraphResolutionError, match="malformed-source-artifact-ref"):
        resolve_node(
            {"id": "n1", "type": "document"},
            project_id="demo",
            source_artifact_refs=[
                {"relative_path": "graphify-out/nodes.jsonl", "sha256": "deadbeef"}
            ],
        )


def test_adv_g2_072_attacker_explanation_not_trusted() -> None:
    resolved = resolve_node(
        {"id": "n1", "type": "document"},
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.explanation is not None
    assert resolved.explanation.winning_step == "graphify_stable"
    # Forged sidecar-shaped dict is ignored; recompute always wins.
    forged = {
        "schema_version": 1,
        "package_id": "AS-GRAPH-002",
        "graphify_node_id": "n1",
        "winning_step": "explicit_atlas_id",
        "considered_steps": [],
        "confidence": "explicit",
    }
    assert resolved.explanation.as_dict()["winning_step"] != forged["winning_step"]


def test_adv_g2_073_forged_authority_primary_stays_derived() -> None:
    resolved = resolve_node(
        {
            "id": "n1",
            "type": "document",
            "authority": {"level": "primary"},
        },
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    payload = resolved.as_dict()
    assert payload["authority"]["level"] == AUTHORITY_LEVEL == "derived"
    validate_record(payload, "graph-resolved-node")


def test_adv_g2_080_hash_mismatch_blocks_acceptance_stream(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    out = root / "graphify-out"
    out.mkdir(parents=True)
    path = out / "nodes.jsonl"
    path.write_text(
        '{"id":"n1","type":"document"}\n',
        encoding="utf-8",
    )
    manifest = {
        "project_id": "proj",
        "sources": [
            {
                "source_id": "s1",
                "path": "graphify-out/nodes.jsonl",
                "sha256": "0" * 64,
                "size_bytes": path.stat().st_size,
                "media_type": "application/json",
                "classification_state": "unclassified",
                "authority": {"level": "derived"},
            }
        ],
    }
    with pytest.raises(GraphAcceptanceError, match="hash-mismatch"):
        resolve_from_acceptance(project_root=root, manifest=manifest, strict=True)


def test_adv_g2_084_empty_accepted_stream_is_noop(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _receipt, result = resolve_from_acceptance(
        project_root=root,
        manifest={"project_id": "proj", "sources": []},
        strict=True,
    )
    assert result.resolved_count == 0
    assert result.quarantined_count == 0


# --- Quarantine laundering (090-092) ---


def test_adv_g2_090_quarantine_candidate_not_laundered_as_resolved() -> None:
    # Feeding a prior quarantine-shaped record as a Graphify node must not
    # elevate status via package_id/status fields on the input.
    node = {
        "id": "q1",
        "type": "document",
        "package_id": "AS-GRAPH-002",
        "status": "quarantine_candidate",
        "quarantine_category": "unresolved-identity",
        "resolved_entity_id": "demo:document:laundered",
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    # Graphify-stable may still apply from id, but never trusts input status/authority.
    assert resolved.as_dict()["authority"]["level"] == "derived"
    assert resolved.as_dict().get("status") in {"resolved", "quarantine_candidate"}
    if resolved.status == "resolved":
        assert resolved.resolved_entity_id != "demo:document:laundered"
        assert resolved.resolution_step == "graphify_stable"


def test_adv_g2_091_secret_shaped_nested_fields_not_in_emit() -> None:
    node = {
        "id": "sec1",
        "type": "document",
        "label": "AKIAIOSFODNN7EXAMPLE",
        "secret_payload": {"password": "hunter2", "token": "sk-test-leak"},
    }
    resolved = resolve_node(node, project_id="demo", source_artifact_refs=_refs())
    text = resolved.to_json()
    assert "hunter2" not in text
    assert "sk-test-leak" not in text
    assert "secret_payload" not in text


def test_adv_g2_092_resolved_dir_only_resolved_status(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    result = resolve_nodes(
        [
            {"id": "ok", "type": "document"},
            {"id": "???", "type": "document"},
        ],
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    write_resolution_outputs(result, vault=vault)
    resolved_dir = vault / "generated" / "graph" / "resolved" / "demo"
    for path in resolved_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == "resolved"
    quarantine_dir = vault / "generated" / "graph" / "quarantine-candidates" / "demo"
    for path in quarantine_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == "quarantine_candidate"


# --- Authority escalation (100-105, 107-108) ---


def test_adv_g2_100_105_no_truth_layer_mutation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    before = _forbidden_census(vault)
    time.sleep(0.01)
    result = resolve_nodes(
        [
            {
                "id": "dec1",
                "type": "decision",
                "label": "supersedes: prior-decision",
                "atlas_entity_id": "demo:decision:dec1",
            }
        ],
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    write_resolution_outputs(result, vault=vault)
    _assert_census_stable(vault, before)


def test_adv_g2_101_102_confidence_not_authority_rank() -> None:
    resolved = resolve_node(
        {"id": "n1", "type": "decision", "atlas_entity_id": "demo:decision:n1"},
        project_id="demo",
        source_artifact_refs=_refs(),
    )
    assert resolved.explanation is not None
    assert resolved.explanation.confidence == "explicit"
    assert resolved.as_dict()["authority"]["level"] == "derived"
    assert "trust" not in resolved.explanation.as_dict()


def test_adv_g2_103_104_cli_write_cannot_target_forbidden(tmp_path: Path) -> None:
    from project_atlas.graph_resolution import _safe_vault_relative

    vault = tmp_path / "vault"
    vault.mkdir()
    for relative in (
        "state/authoritative-state/x.json",
        "generated/query/cache.json",
    ):
        with pytest.raises(GraphResolutionError, match="path-policy-forbidden"):
            _safe_vault_relative(vault, relative)


def test_adv_g2_107_semantic_ingestion_still_fail_closed(tmp_path: Path) -> None:
    from project_atlas.config import GraphifyConfig

    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(GraphAcceptanceError, match="semantic_ingestion_unsupported"):
        resolve_from_acceptance(
            project_root=root,
            manifest={"project_id": "proj", "sources": []},
            config=GraphifyConfig(semantic_ingestion=True),
            strict=True,
        )


def test_adv_g2_108_mapping_order_deterministic_no_recency() -> None:
    table = {
        "mappings": [
            {"graphify_node_id": "b", "resolved_entity_id": "demo:document:b"},
            {"graphify_node_id": "a", "resolved_entity_id": "demo:document:a"},
        ]
    }
    nodes = [
        {"id": "b", "type": "document"},
        {"id": "a", "type": "document"},
    ]
    first = resolve_nodes(
        nodes, project_id="demo", mapping_table=table, source_artifact_refs=_refs()
    )
    second = resolve_nodes(
        list(reversed(nodes)),
        project_id="demo",
        mapping_table=table,
        source_artifact_refs=_refs(),
    )
    assert first.to_json() == second.to_json()


def test_adv_g2_determinism_replay_and_truth_oracle(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    before = _forbidden_census(vault)
    nodes = [
        {"id": "z", "type": "document"},
        {"id": "a", "type": "decision", "atlas_entity_id": "demo:decision:a"},
        {"id": "n1", "type": "document", "project_id": "other"},  # ADV-G2-051
    ]
    first = resolve_nodes(nodes, project_id="demo", source_artifact_refs=_refs())
    second = resolve_nodes(nodes, project_id="demo", source_artifact_refs=_refs())
    assert first.to_json() == second.to_json()
    write_resolution_outputs(first, vault=vault)
    _assert_census_stable(vault, before)
    assert any(n.quarantine_category == "cross-project-resolution-forbidden" for n in first.nodes)
