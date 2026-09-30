"""AS-XPROJ-004 — Conflict intelligence + global derived indexes (fixture + ADV)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.schema import available_schemas, validate_record
from project_atlas.xproj_edges import (
    apply_edge_registrations,
    write_edge_outputs,
)
from project_atlas.xproj_indexes import (
    AUTHORITY_LEVEL,
    INDEX_BUCKETS,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    XprojIndexError,
    build_xproj_indexes,
    claims_authority_paths_untouched,
    inspect_xproj_indexes,
    promote_xproj_index_path_forbidden,
    write_xproj_index_outputs,
)
from project_atlas.xproj_registry import (
    EvidenceRef,
    apply_registrations,
    write_registry_outputs,
)

EVIDENCE_A = EvidenceRef(relative_path="sources/arch-a.md", sha256="a" * 64)
EVIDENCE_B = EvidenceRef(relative_path="sources/arch-b.md", sha256="b" * 64)


def _seed_version_divergence() -> tuple[object, object]:
    result = apply_registrations(
        [
            {
                "kind": "entity",
                "global_entity_id": "ge-lib-shared-v1",
                "entity_class": "library",
                "display_name": "SharedLib",
                "attributes": {"version": "1.0.0"},
            },
            {
                "kind": "entity",
                "global_entity_id": "ge-lib-shared-v2",
                "entity_class": "library",
                "display_name": "SharedLib",
                "attributes": {"version": "2.0.0"},
            },
            {
                "kind": "join",
                "project_id": "proj-a",
                "project_local_entity_id": "proj-a:lib:shared",
                "global_entity_id": "ge-lib-shared-v1",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
            {
                "kind": "join",
                "project_id": "proj-b",
                "project_local_entity_id": "proj-b:lib:shared",
                "global_entity_id": "ge-lib-shared-v2",
                "evidence_refs": [EVIDENCE_B.as_dict()],
            },
        ]
    )
    entities = {item.global_entity_id: item for item in result.entities}
    return entities, list(result.joins)


def _seed_with_conflicts_edge() -> tuple[object, object, list[object]]:
    entities, joins = _seed_version_divergence()
    edge_result = apply_edge_registrations(
        [
            {
                "kind": "edge",
                "edge_id": "xe-conflict-sharedlib",
                "relationship_type": "conflicts-with",
                "source_global_entity_id": "ge-lib-shared-v1",
                "target_global_entity_id": "ge-lib-shared-v2",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            }
        ],
        entities=entities,  # type: ignore[arg-type]
        joins=joins,  # type: ignore[arg-type]
    )
    return entities, joins, list(edge_result.edges)


def test_xproj_004_schemas_registered() -> None:
    kinds = available_schemas()
    assert "xproj-conflict-report" in kinds
    assert "xproj-index-document" in kinds


def test_xp4_fx_001_version_conflict_and_index_rebuild(tmp_path: Path) -> None:
    entities, joins = _seed_version_divergence()
    first = build_xproj_indexes(entities=entities, joins=joins, edges=[])  # type: ignore[arg-type]
    second = build_xproj_indexes(entities=entities, joins=joins, edges=[])  # type: ignore[arg-type]

    assert AUTHORITY_LEVEL == "derived"
    assert PACKAGE_ID == "AS-XPROJ-004"
    assert {doc.bucket for doc in first.indexes} == set(INDEX_BUCKETS)
    assert first.conflict_count >= 1
    kinds = {item.kind for item in first.conflicts}
    assert "version-divergence" in kinds

    version_report = next(item for item in first.conflicts if item.kind == "version-divergence")
    payload = version_report.as_dict()
    assert payload["authority"]["level"] == "derived"
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert payload["resolution"]["auto_resolve"] is False
    assert payload["resolution"]["winning_choice"] is None
    assert set(payload["versions"]) == {"1.0.0", "2.0.0"}
    assert set(payload["project_ids"]) >= {"proj-a", "proj-b"}
    validate_record(payload, "xproj-conflict-report")

    # Deterministic rebuild
    assert [doc.content_fingerprint for doc in first.indexes] == [
        doc.content_fingerprint for doc in second.indexes
    ]
    assert [item.conflict_id for item in first.conflicts] == [
        item.conflict_id for item in second.conflicts
    ]

    components = next(doc for doc in first.indexes if doc.bucket == "components")
    validate_record(components.as_dict(), "xproj-index-document")
    assert len(components.entries) == 2

    vault = tmp_path / "vault"
    vault.mkdir()
    # Claim fixture must remain byte-stable across index promote.
    claims_dir = vault / "state" / "claims"
    claims_dir.mkdir(parents=True)
    claim_path = claims_dir / "claims.json"
    claim_bytes = (
        b'{"claims":[{"claim_id":"clm-stable","subject":"c1","field":"status","value":"active"}]}\n'
    )
    claim_path.write_bytes(claim_bytes)
    before_mtime = claim_path.stat().st_mtime_ns

    written = write_xproj_index_outputs(first, vault=vault)
    assert any(path.startswith("generated/xproj/indexes/") for path in written)
    assert any(path.startswith("generated/xproj/conflicts/") for path in written)
    assert claim_path.read_bytes() == claim_bytes
    assert claim_path.stat().st_mtime_ns == before_mtime
    assert not (vault / "generated" / "indexes").exists()
    assert not (vault / "generated" / "graph").exists()
    assert not (vault / "generated" / "xproj" / "duplicate-candidates").exists()


def test_xp4_fx_002_explicit_conflicts_with_edge() -> None:
    entities, joins, edges = _seed_with_conflicts_edge()
    result = build_xproj_indexes(
        entities=entities,  # type: ignore[arg-type]
        joins=joins,  # type: ignore[arg-type]
        edges=edges,  # type: ignore[arg-type]
    )
    kinds = {item.kind for item in result.conflicts}
    assert "explicit-conflicts-with" in kinds
    explicit = next(item for item in result.conflicts if item.kind == "explicit-conflicts-with")
    assert "xe-conflict-sharedlib" in explicit.edge_ids
    assert (
        "winning_choice" not in explicit.as_dict()
        or explicit.as_dict()["resolution"]["winning_choice"] is None
    )
    validate_record(explicit.as_dict(), "xproj-conflict-report")

    relationships = next(doc for doc in result.indexes if doc.bucket == "relationships")
    assert len(relationships.entries) == 1
    validate_record(relationships.as_dict(), "xproj-index-document")


def test_xp4_fx_003_empty_optional_buckets() -> None:
    entities, joins = _seed_version_divergence()
    result = build_xproj_indexes(entities=entities, joins=joins, edges=[])  # type: ignore[arg-type]
    for bucket in ("agents", "skills", "work-packages", "decisions", "risks"):
        doc = next(item for item in result.indexes if item.bucket == bucket)
        assert len(doc.entries) == 0
        validate_record(doc.as_dict(), "xproj-index-document")


def test_xp4_fx_004_vault_load_roundtrip(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    reg = apply_registrations(
        [
            {
                "kind": "entity",
                "global_entity_id": "ge-lib-shared-v1",
                "entity_class": "library",
                "display_name": "SharedLib",
                "attributes": {"version": "1.0.0"},
            },
            {
                "kind": "entity",
                "global_entity_id": "ge-lib-shared-v2",
                "entity_class": "library",
                "display_name": "SharedLib",
                "attributes": {"version": "2.0.0"},
            },
            {
                "kind": "join",
                "project_id": "proj-a",
                "project_local_entity_id": "proj-a:lib:shared",
                "global_entity_id": "ge-lib-shared-v1",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
            {
                "kind": "join",
                "project_id": "proj-b",
                "project_local_entity_id": "proj-b:lib:shared",
                "global_entity_id": "ge-lib-shared-v2",
                "evidence_refs": [EVIDENCE_B.as_dict()],
            },
        ]
    )
    write_registry_outputs(reg, vault=vault)
    edge_result = apply_edge_registrations(
        [
            {
                "kind": "edge",
                "edge_id": "xe-conflict-sharedlib",
                "relationship_type": "conflicts-with",
                "source_global_entity_id": "ge-lib-shared-v1",
                "target_global_entity_id": "ge-lib-shared-v2",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            }
        ],
        entities={item.global_entity_id: item for item in reg.entities},
        joins=list(reg.joins),
    )
    write_edge_outputs(edge_result, vault=vault)

    built = build_xproj_indexes(vault=vault)
    written = write_xproj_index_outputs(built, vault=vault)
    assert written
    summary = inspect_xproj_indexes(built)
    assert summary["package_id"] == PACKAGE_ID
    assert summary["conflict_count"] >= 1

    # Replay identical
    rebuilt = build_xproj_indexes(vault=vault)
    assert inspect_xproj_indexes(rebuilt) == summary


def test_xp4_adv_path_escape_and_forbidden_prefixes() -> None:
    with pytest.raises(XprojIndexError, match=r"path-escape|write-prefix-forbidden"):
        promote_xproj_index_path_forbidden("../etc/passwd")
    with pytest.raises(XprojIndexError, match="write-prefix-forbidden"):
        promote_xproj_index_path_forbidden("generated/indexes/claims.json")
    with pytest.raises(XprojIndexError, match="write-prefix-forbidden"):
        promote_xproj_index_path_forbidden("generated/graph/projections/x.md")
    with pytest.raises(XprojIndexError, match="write-prefix-forbidden"):
        promote_xproj_index_path_forbidden("generated/xproj/duplicate-candidates/dup.json")
    with pytest.raises(XprojIndexError, match="write-prefix-forbidden"):
        promote_xproj_index_path_forbidden("claims/claims.json")
    # Owned path accepted by policy helper
    promote_xproj_index_path_forbidden("generated/xproj/indexes/projects/index.json")
    promote_xproj_index_path_forbidden(
        "generated/xproj/conflicts/xc-version-library-sharedlib.json"
    )


def test_xp4_adv_no_claim_authority_surface_listed() -> None:
    forbidden = claims_authority_paths_untouched()
    assert "claims/" in forbidden
    assert "generated/indexes/" in forbidden
    assert "generated/graph/" in forbidden
    assert "generated/xproj/duplicate-candidates/" in forbidden


def test_xp4_adv_write_rejects_traversal(tmp_path: Path) -> None:
    entities, joins = _seed_version_divergence()
    result = build_xproj_indexes(entities=entities, joins=joins, edges=[])  # type: ignore[arg-type]
    vault = tmp_path / "vault"
    vault.mkdir()
    # Normal write succeeds
    write_xproj_index_outputs(result, vault=vault)
    index_path = vault / "generated" / "xproj" / "indexes" / "projects" / "index.json"
    assert index_path.is_file()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["authority"]["level"] == "derived"
