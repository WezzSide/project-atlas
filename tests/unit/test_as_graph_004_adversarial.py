"""AS-GRAPH-004 adversarial probes (ADV-G4 band).

Truth boundary: GRAPH QUARANTINE ≠ AUTOMATIC AUTHORITY.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from project_atlas.graph_quarantine import (
    AUTHORITY_LEVEL,
    TRUTH_BOUNDARY,
    GraphQuarantineError,
    materialize_quarantine_store,
    promote_quarantine_path_forbidden,
    promote_quarantine_to_authority_forbidden,
    promote_quarantine_to_relationship_forbidden,
    synthesize_claim_conflict_forbidden,
    write_quarantine_outputs,
)
from project_atlas.graph_relationships import ArtifactRef, RelationshipStoreResult, normalize_edges
from project_atlas.graph_resolution import ResolvedNode
from project_atlas.schema import validate_record

REF = ArtifactRef(relative_path="graphify-out/graph.json", sha256="a" * 64)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _forbidden_census(vault: Path) -> dict[str, tuple[int, str]]:
    relatives = (
        "claims/claim.json",
        "state/current-state/demo.json",
        "state/authoritative-state/demo.json",
        "relationships/nodes.json",
        "generated/query/cache.json",
        "state/global-entities/registry.json",
        "generated/graph/relationships/demo/sentinel.json",
        "generated/graph/relationship-quarantine/demo/sentinel.json",
        "generated/graph/quarantine-candidates/demo/sentinel.json",
        "generated/graph/resolved/demo/sentinel.json",
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


def _orphan_store() -> RelationshipStoreResult:
    nodes = [_resolved("api", "demo:api")]
    edges = [
        {
            "id": "orphan",
            "type": "depends-on",
            "source": "api",
            "target": "missing",
            "reason": "password=super-secret-token",
            "_atlas_artifact_ref": REF.as_dict(),
        }
    ]
    return normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])


def test_adv_g4_001_no_authority_elevation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _forbidden_census(vault)
    store = _orphan_store()
    result = materialize_quarantine_store(store)
    assert result.records[0].as_dict()["authority"]["level"] == AUTHORITY_LEVEL
    with pytest.raises(GraphQuarantineError, match="quarantine-authority-elevation-forbidden"):
        promote_quarantine_to_authority_forbidden(result.records[0])
    write_quarantine_outputs(result, vault=vault)
    _assert_census_stable(vault, before)


def test_adv_g4_002_no_lww_promote_to_relationship() -> None:
    store = _orphan_store()
    result = materialize_quarantine_store(store)
    with pytest.raises(GraphQuarantineError, match="quarantine-relationship-promotion-forbidden"):
        promote_quarantine_to_relationship_forbidden(result.records[0])


def test_adv_g4_003_forbidden_write_prefixes() -> None:
    forbidden = [
        "relationships/x.json",
        "claims/x.json",
        "state/current-state/x.json",
        "state/authoritative-state/x.json",
        "state/global-entities/x.json",
        "generated/query/x.json",
        "generated/graph/relationships/demo/x.json",
        "generated/graph/relationship-quarantine/demo/x.json",
        "generated/graph/quarantine-candidates/demo/x.json",
        "generated/graph/resolved/demo/x.json",
        "generated/ops/health-snapshot.json",
    ]
    for relative in forbidden:
        with pytest.raises(GraphQuarantineError, match="forbidden-write-prefix"):
            promote_quarantine_path_forbidden(relative)


def test_adv_g4_004_secret_reason_redacted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _forbidden_census(vault)
    store = _orphan_store()
    assert store.quarantine[0].reason == "redacted-sensitive-reason"
    result = materialize_quarantine_store(store)
    assert result.records[0].reason == "redacted-sensitive-reason"
    assert "password" not in result.records[0].to_json()
    write_quarantine_outputs(result, vault=vault)
    _assert_census_stable(vault, before)
    for path in (vault / "generated/graph/quarantine/demo").glob("gq-*.json"):
        text = path.read_text(encoding="utf-8")
        assert "password" not in text
        assert "super-secret" not in text
        validate_record(json.loads(text), "graph-quarantine-record")
    receipt = vault / "generated/graph/quarantine/demo/receipt.json"
    validate_record(json.loads(receipt.read_text(encoding="utf-8")), "graph-quarantine-receipt")


def test_adv_g4_005_path_escape_rejected() -> None:
    with pytest.raises(GraphQuarantineError, match=r"path-escape|forbidden-write-prefix"):
        promote_quarantine_path_forbidden("../outside.json")
    with pytest.raises(GraphQuarantineError, match=r"path-escape|forbidden-write-prefix"):
        promote_quarantine_path_forbidden("generated/graph/quarantine/../../claims/x.json")


def test_adv_g4_006_truth_boundary_present() -> None:
    store = _orphan_store()
    result = materialize_quarantine_store(store)
    assert result.records[0].as_dict()["truth_boundary"] == TRUTH_BOUNDARY
    assert result.health.as_dict()["note"] == "GRAPH HEALTH ≠ PROJECT AUTHORITY"
    assert result.health.as_dict()["authority_plane"] == "none"


def test_adv_g4_007_claim_conflict_synthesis_forbidden() -> None:
    store = _orphan_store()
    result = materialize_quarantine_store(store)
    with pytest.raises(GraphQuarantineError, match="claim-conflict-synthesis-forbidden"):
        synthesize_claim_conflict_forbidden(result.records[0])


def test_adv_g4_008_failed_promote_leaves_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = _orphan_store()
    first = materialize_quarantine_store(store)
    write_quarantine_outputs(first, vault=vault)
    health_path = vault / "generated/graph/health/demo/health.json"
    prior_bytes = health_path.read_bytes()

    # Build a changed store so promote must rewrite health/quarantine bytes.
    nodes = [
        ResolvedNode(
            project_id="demo",
            graphify_node_id="api",
            entity_class="unknown",
            resolution_step="graphify_stable",
            status="resolved",
            source_artifact_refs=({"relative_path": "n.jsonl", "sha256": "a" * 64},),
            resolved_entity_id="demo:api",
        )
    ]
    edges = [
        {
            "id": "orphan-a",
            "type": "depends-on",
            "source": "api",
            "target": "missing-a",
            "_atlas_artifact_ref": REF.as_dict(),
        },
        {
            "id": "orphan-b",
            "type": "depends-on",
            "source": "api",
            "target": "missing-b",
            "_atlas_artifact_ref": REF.as_dict(),
        },
    ]
    changed = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    second = materialize_quarantine_store(changed)
    assert second.incremental.input_content_hash != first.incremental.input_content_hash

    calls = {"n": 0}
    real_replace = __import__("os").replace

    def boom(src: str | Path, dst: str | Path) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("injected-promote-failure")
        real_replace(src, dst)

    import project_atlas.graph_quarantine as gq

    monkeypatch.setattr(gq, "_replace_path", boom)
    with pytest.raises(GraphQuarantineError, match="promotion-failed-prior-state-intact"):
        write_quarantine_outputs(second, vault=vault, skip_unchanged=False)
    assert health_path.read_bytes() == prior_bytes
