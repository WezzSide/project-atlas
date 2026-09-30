"""AS-GRAPH-004 — Durable quarantine / health / incremental (G4 matrix).

Truth boundary: GRAPH QUARANTINE ≠ AUTOMATIC AUTHORITY.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import project_atlas.graph_quarantine as gq
from project_atlas.graph_quarantine import (
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    GraphQuarantineError,
    derive_health_state,
    inspect_quarantine_store,
    load_incremental_state,
    materialize_from_candidates,
    materialize_quarantine_store,
    promote_quarantine_path_forbidden,
    promote_quarantine_to_authority_forbidden,
    promote_quarantine_to_relationship_forbidden,
    synthesize_claim_conflict_forbidden,
    write_quarantine_outputs,
)
from project_atlas.graph_relationships import (
    ArtifactRef,
    RelationshipQuarantine,
    RelationshipRecord,
    RelationshipStoreResult,
    handoff_quarantine_store,
    normalize_edges,
)
from project_atlas.graph_resolution import ResolvedNode
from project_atlas.schema import available_schemas, validate_record

REF = ArtifactRef(relative_path="graphify-out/graph.json", sha256="a" * 64)
FP = "b" * 64


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


def _candidate(
    candidate_id: str,
    category: str,
    *,
    reason: str = "test-reason",
    fingerprint: str | None = FP,
) -> RelationshipQuarantine:
    return RelationshipQuarantine(
        project_id="demo",
        candidate_id=candidate_id,
        category=category,  # type: ignore[arg-type]
        reason=reason,
        relationship_fingerprint=fingerprint,
        graphify_edge_ids=(candidate_id,),
        source_graphify_id="a",
        target_graphify_id="b",
        artifact_refs=(REF,),
    )


def _store_result(
    quarantine: tuple[RelationshipQuarantine, ...] = (),
    *,
    retained: int = 0,
    histogram: dict[str, int] | None = None,
) -> RelationshipStoreResult:
    relationships: tuple[RelationshipRecord, ...] = ()
    if retained:
        relationships = tuple(
            RelationshipRecord(
                project_id="demo",
                relationship_id=f"rel-{index}",
                relationship_type="depends-on",
                source_entity_id="demo:a",
                target_entity_id="demo:b",
                source_graphify_id="a",
                target_graphify_id="b",
                link_quality="inferred",
                relationship_fingerprint=f"{index:064x}",
                provenance={"graphify_edge_ids": [f"e{index}"], "artifact_refs": [REF.as_dict()]},
            )
            for index in range(retained)
        )
    return RelationshipStoreResult(
        project_id="demo",
        relationships=relationships,
        quarantine=quarantine,
        link_quality_histogram=histogram or {"orphaned": len(quarantine)},
    )


def _forbidden_census(vault: Path) -> dict[str, tuple[int, str]]:
    relatives = (
        "claims/claim.json",
        "state/current-state/demo.json",
        "state/authoritative-state/demo.json",
        "relationships/nodes.json",
        "generated/query/cache.json",
        "state/global-entities/registry.json",
        "generated/graph/relationships/demo/keep.json",
        "generated/graph/relationship-quarantine/demo/soft.json",
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


def test_as_graph_004_schemas_registered() -> None:
    kinds = available_schemas()
    assert "graph-quarantine-record" in kinds
    assert "graph-quarantine-receipt" in kinds
    assert "graph-health-snapshot" in kinds
    assert "graph-incremental-state" in kinds


def test_as_graph_004_happy_path_durable_and_schema_valid() -> None:
    store = _store_result((_candidate("orphan-1", "orphaned-endpoint"),), retained=1)
    result = materialize_quarantine_store(store)
    assert result.quarantined_count == 1
    assert result.health.health_state == "degraded"
    assert result.incremental.refreshed is True
    assert result.receipt.as_dict()["authority"]["graphify"] == "derived"
    record = result.records[0]
    assert record.remediation
    validate_record(record.as_dict(), "graph-quarantine-record")
    validate_record(result.health.as_dict(), "graph-health-snapshot")
    validate_record(result.incremental.as_dict(), "graph-incremental-state")
    validate_record(result.receipt.as_dict(), "graph-quarantine-receipt")
    assert result.as_dict()["authority"]["level"] == AUTHORITY_LEVEL
    assert result.as_dict()["truth_boundary"] == TRUTH_BOUNDARY
    assert result.as_dict()["package_id"] == PACKAGE_ID


def test_as_graph_004_category_matrix() -> None:
    categories = (
        "orphaned-endpoint",
        "quarantined-endpoint",
        "cross-project-endpoint",
        "incompatible-duplicate",
        "capacity-rejected",
        "malformed-edge",
    )
    candidates = tuple(
        _candidate(f"c-{category}", category, fingerprint=f"{index:064x}")
        for index, category in enumerate(categories)
    )
    result = materialize_from_candidates(candidates, project_id="demo", retained_count=0)
    assert result.quarantined_count == len(categories)
    assert set(result.health.category_counts) == set(categories)
    for record in result.records:
        validate_record(record.as_dict(), "graph-quarantine-record")
        assert "password" not in record.reason.lower()


def test_as_graph_004_redacts_sensitive_reason() -> None:
    candidate = _candidate("sec", "malformed-edge", reason="edge failed password=super-secret")
    result = materialize_from_candidates([candidate], project_id="demo")
    assert result.records[0].reason == "redacted-sensitive-reason"
    blob = result.to_json()
    assert "super-secret" not in blob


def test_as_graph_004_incompatible_duplicate_not_lww() -> None:
    store = _store_result(
        (_candidate("dup", "incompatible-duplicate", reason="incompatible fingerprints"),),
    )
    result = materialize_quarantine_store(store)
    assert result.records[0].category == "incompatible-duplicate"
    with pytest.raises(GraphQuarantineError, match="relationship-promotion-forbidden"):
        promote_quarantine_to_relationship_forbidden(result.records[0])


def test_as_graph_004_authority_and_claim_conflict_forbidden() -> None:
    record = materialize_from_candidates(
        [_candidate("x", "orphaned-endpoint")],
        project_id="demo",
    ).records[0]
    with pytest.raises(GraphQuarantineError, match="authority-elevation-forbidden"):
        promote_quarantine_to_authority_forbidden(record)
    with pytest.raises(GraphQuarantineError, match="claim-conflict-synthesis-forbidden"):
        synthesize_claim_conflict_forbidden(record)


def test_as_graph_004_health_state_thresholds() -> None:
    assert derive_health_state(retained_count=3, quarantined_count=0) == "healthy"
    assert derive_health_state(retained_count=3, quarantined_count=1) == "degraded"
    assert derive_health_state(retained_count=3, quarantined_count=10) == "degraded"
    assert derive_health_state(retained_count=3, quarantined_count=11) == "unhealthy"
    assert derive_health_state(retained_count=-1, quarantined_count=0) == "unknown"


def test_as_graph_004_health_determinism() -> None:
    candidates = [
        _candidate("b", "orphaned-endpoint", fingerprint="1" * 64),
        _candidate("a", "malformed-edge", fingerprint="2" * 64),
    ]
    first = materialize_from_candidates(candidates, project_id="demo", retained_count=2)
    second = materialize_from_candidates(
        list(reversed(candidates)),
        project_id="demo",
        retained_count=2,
    )
    assert first.health.to_json() == second.health.to_json()
    assert first.incremental.input_content_hash == second.incremental.input_content_hash
    assert [item.quarantine_id for item in first.records] == [
        item.quarantine_id for item in second.records
    ]


def test_as_graph_004_incremental_noop_byte_identical(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = _store_result((_candidate("orphan-1", "orphaned-endpoint"),), retained=1)
    first = materialize_quarantine_store(store)
    written = write_quarantine_outputs(first, vault=vault)
    assert written
    snapshots = {
        rel: (vault.joinpath(rel).stat().st_mtime_ns, _sha256(vault / rel)) for rel in written
    }

    prior = load_incremental_state(vault, project_id="demo")
    second = materialize_quarantine_store(store, prior_state=prior)
    assert second.incremental.refreshed is False
    planned = write_quarantine_outputs(second, vault=vault, skip_unchanged=True)
    assert planned == written
    for rel, (mtime_ns, digest) in snapshots.items():
        assert vault.joinpath(rel).stat().st_mtime_ns == mtime_ns
        assert _sha256(vault / rel) == digest


def test_as_graph_004_write_emits_allowed_prefixes_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    census = _forbidden_census(vault)
    store = _store_result((_candidate("orphan-1", "orphaned-endpoint"),), retained=2)
    result = materialize_quarantine_store(store)
    written = write_quarantine_outputs(result, vault=vault)
    assert any(path.startswith("generated/graph/quarantine/") for path in written)
    assert any(path.startswith("generated/graph/health/") for path in written)
    assert any(path.startswith("generated/graph/incremental/") for path in written)
    assert (vault / "generated/graph/quarantine/demo/receipt.json").is_file()
    _assert_census_stable(vault, census)


def test_as_graph_004_promote_failure_leaves_prior_state(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = _store_result((_candidate("orphan-1", "orphaned-endpoint"),))
    first = materialize_quarantine_store(store)
    write_quarantine_outputs(first, vault=vault)
    prior_files = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*.json")
        if path.is_file() and not path.name.startswith(".")
    }

    changed = _store_result(
        (
            _candidate("orphan-1", "orphaned-endpoint"),
            _candidate("orphan-2", "orphaned-endpoint", fingerprint="c" * 64),
        )
    )
    second = materialize_quarantine_store(changed, prior_state=first.incremental)
    assert second.incremental.refreshed is True

    calls = {"n": 0}
    original = gq._replace_path

    def _flaky(src: str | bytes | Path, dst: str | bytes | Path) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("injected mid-promotion failure")
        original(src, dst)

    gq._replace_path = _flaky  # type: ignore[assignment]
    try:
        with pytest.raises(GraphQuarantineError, match="promotion-failed-prior-state-intact"):
            write_quarantine_outputs(second, vault=vault, skip_unchanged=False)
    finally:
        gq._replace_path = original  # type: ignore[assignment]

    after = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*.json")
        if path.is_file() and not path.name.startswith(".")
    }
    assert after == prior_files


def test_as_graph_004_strict_conflict_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    store = _store_result((_candidate("dup", "incompatible-duplicate"),))
    result = materialize_quarantine_store(store)
    with pytest.raises(GraphQuarantineError, match="strict-conflict-fail-closed"):
        write_quarantine_outputs(result, vault=vault, strict=True)
    assert not any(vault.rglob("*.json"))


def test_as_graph_004_forbidden_write_prefixes() -> None:
    forbidden = (
        "relationships/nodes.json",
        "claims/x.json",
        "state/current-state/demo.json",
        "generated/graph/relationships/demo/x.json",
        "generated/graph/relationship-quarantine/demo/x.json",
        "generated/query/cache.json",
        "../escape.json",
    )
    for relative in forbidden:
        with pytest.raises(GraphQuarantineError):
            promote_quarantine_path_forbidden(relative)


def test_as_graph_004_project_id_mismatch_fail_closed() -> None:
    bad = RelationshipQuarantine(
        project_id="other",
        candidate_id="x",
        category="orphaned-endpoint",
        reason="mismatch",
    )
    with pytest.raises(GraphQuarantineError, match="project-id-mismatch"):
        materialize_from_candidates([bad], project_id="demo")


def test_as_graph_004_handoff_from_graph_003() -> None:
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
    store = normalize_edges(edges, project_id="demo", resolution=nodes, artifact_refs=[REF])
    durable = handoff_quarantine_store(store)
    assert durable.quarantined_count == 1
    assert durable.records[0].category == "orphaned-endpoint"
    assert durable.health.link_quality_histogram.get("orphaned") == 1
    observe = inspect_quarantine_store(durable)
    assert observe["package_id"] == PACKAGE_ID
    assert "secret" not in json.dumps(observe)


def test_as_graph_004_receipt_canonical_override_false() -> None:
    result = materialize_from_candidates([], project_id="demo", retained_count=4)
    assert result.health.health_state == "healthy"
    authority: dict[str, Any] = result.receipt.as_dict()["authority"]
    assert authority["graphify"] == "derived"
    assert authority["canonical_override_allowed"] is False
    assert "generated.at" not in result.receipt.as_dict()
    assert "generated.at" not in result.health.as_dict()


def test_as_graph_004_malformed_prior_state_fail_closed() -> None:
    with pytest.raises(GraphQuarantineError, match="malformed-prior-incremental-state"):
        materialize_from_candidates([], project_id="demo", prior_state={"project_id": "demo"})
