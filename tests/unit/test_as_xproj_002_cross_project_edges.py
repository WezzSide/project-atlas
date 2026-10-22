"""AS-XPROJ-002 — Cross-project global edge registry (fixture matrix + ADV)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.schema import available_schemas, validate_record
from project_atlas.secrets import scan_text
from project_atlas.xproj_edges import (
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    EdgeQuarantineCandidate,
    GlobalEdgeRecord,
    XprojEdgeError,
    apply_edge_registrations,
    compute_edge_fingerprint,
    inspect_edge_registry,
    load_edge_registry_state,
    promote_edge_path_forbidden,
    register_global_edge,
    write_edge_outputs,
)
from project_atlas.xproj_registry import (
    EvidenceRef,
    GlobalEntityRecord,
    JoinKeyRecord,
    apply_registrations,
    write_registry_outputs,
)

EVIDENCE_A = EvidenceRef(relative_path="sources/arch-a.md", sha256="a" * 64)
EVIDENCE_B = EvidenceRef(relative_path="sources/arch-b.md", sha256="b" * 64)


def _seed_cross_project() -> tuple[dict[str, GlobalEntityRecord], list[JoinKeyRecord]]:
    result = apply_registrations(
        [
            {
                "kind": "entity",
                "global_entity_id": "ge-svc-billing",
                "entity_class": "service",
                "display_name": "Billing",
            },
            {
                "kind": "entity",
                "global_entity_id": "ge-tech-postgres-v1",
                "entity_class": "technology",
                "display_name": "Postgres",
            },
            {
                "kind": "join",
                "project_id": "proj-a",
                "project_local_entity_id": "proj-a:svc:billing",
                "global_entity_id": "ge-svc-billing",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
            {
                "kind": "join",
                "project_id": "proj-b",
                "project_local_entity_id": "proj-b:tech:postgres",
                "global_entity_id": "ge-tech-postgres-v1",
                "evidence_refs": [EVIDENCE_B.as_dict()],
            },
        ]
    )
    entities = {item.global_entity_id: item for item in result.entities}
    return entities, list(result.joins)


def test_xproj_002_schemas_registered() -> None:
    kinds = available_schemas()
    assert "xproj-global-edge" in kinds
    assert "xproj-edge-quarantine" in kinds


def test_xp2_fx_001_happy_path_cross_project_edge() -> None:
    entities, joins = _seed_cross_project()
    outcome = register_global_edge(
        edge_id="xe-dep-billing-postgres-v1",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(outcome, GlobalEdgeRecord)
    assert AUTHORITY_LEVEL == "derived"
    payload = outcome.as_dict()
    assert payload["authority"]["level"] == "derived"
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert payload["package_id"] == PACKAGE_ID
    assert set(outcome.source_project_ids) | set(outcome.target_project_ids) >= {
        "proj-a",
        "proj-b",
    }
    validate_record(payload, "xproj-global-edge")


def test_xp2_fx_002_missing_endpoint_quarantine() -> None:
    entities, joins = _seed_cross_project()
    outcome = register_global_edge(
        edge_id="xe-missing",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-missing",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(outcome, EdgeQuarantineCandidate)
    assert outcome.category == "missing-endpoint-registration"
    assert "winning_choice" not in outcome.as_dict()
    validate_record(outcome.as_dict(), "xproj-edge-quarantine")


def test_xp2_fx_003_name_only_and_fuzzy_forbidden() -> None:
    entities, joins = _seed_cross_project()
    name_only = register_global_edge(
        edge_id="xe-name",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
        mint_from_names=True,
        source_display_name="Billing",
        target_display_name="Postgres",
    )
    assert isinstance(name_only, EdgeQuarantineCandidate)
    assert name_only.category == "name-only-edge-forbidden"

    fuzzy = register_global_edge(
        edge_id="xe-fuzzy",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
        fuzzy=True,
    )
    assert isinstance(fuzzy, EdgeQuarantineCandidate)
    assert fuzzy.category == "fuzzy-edge-forbidden"


def test_xp2_fx_004_intra_project_refused() -> None:
    result = apply_registrations(
        [
            {
                "kind": "entity",
                "global_entity_id": "ge-a",
                "entity_class": "service",
                "display_name": "A",
            },
            {
                "kind": "entity",
                "global_entity_id": "ge-b",
                "entity_class": "technology",
                "display_name": "B",
            },
            {
                "kind": "join",
                "project_id": "proj-a",
                "project_local_entity_id": "local-a",
                "global_entity_id": "ge-a",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
            {
                "kind": "join",
                "project_id": "proj-a",
                "project_local_entity_id": "local-b",
                "global_entity_id": "ge-b",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
        ]
    )
    entities = {item.global_entity_id: item for item in result.entities}
    outcome = register_global_edge(
        edge_id="xe-intra",
        relationship_type="depends-on",
        source_global_entity_id="ge-a",
        target_global_entity_id="ge-b",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=result.joins,
    )
    assert isinstance(outcome, EdgeQuarantineCandidate)
    assert outcome.category == "not-cross-project"


def test_xp2_fx_005_self_loop_forbidden() -> None:
    entities, joins = _seed_cross_project()
    joins = [
        *joins,
        JoinKeyRecord(
            project_id="proj-b",
            project_local_entity_id="proj-b:svc:billing",
            global_entity_id="ge-svc-billing",
            evidence_refs=(EVIDENCE_B,),
        ),
    ]
    outcome = register_global_edge(
        edge_id="xe-loop",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-svc-billing",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(outcome, EdgeQuarantineCandidate)
    assert outcome.category == "self-loop-forbidden"


def test_xp2_fx_006_incompatible_duplicate_no_lww() -> None:
    entities, joins = _seed_cross_project()
    first = register_global_edge(
        edge_id="xe-dep-1",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(first, GlobalEdgeRecord)
    second = register_global_edge(
        edge_id="xe-dep-2",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_B],
        entities=entities,
        joins=joins,
        existing_edges=[first],
    )
    assert isinstance(second, EdgeQuarantineCandidate)
    assert second.category == "incompatible-duplicate-edge"
    assert "winning_choice" not in second.as_dict()


def test_xp2_fx_007_conflicts_with_no_claim_fields() -> None:
    entities, joins = _seed_cross_project()
    outcome = register_global_edge(
        edge_id="xe-conflict-1",
        relationship_type="conflicts-with",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(outcome, GlobalEdgeRecord)
    payload = outcome.as_dict()
    assert "claim_id" not in payload
    assert "winning_choice" not in payload
    assert payload["authority"]["level"] == "derived"
    assert "never synthesizes Core claim conflicts" in payload["authority"]["note"]


def test_xp2_fx_008_deterministic_fingerprint_and_replay() -> None:
    entities, joins = _seed_cross_project()
    batch = [
        {
            "kind": "edge",
            "edge_id": "xe-dep-billing-postgres-v1",
            "relationship_type": "depends-on",
            "source_global_entity_id": "ge-svc-billing",
            "target_global_entity_id": "ge-tech-postgres-v1",
            "evidence_refs": [EVIDENCE_A.as_dict()],
        }
    ]
    first = apply_edge_registrations(batch, entities=entities, joins=joins)
    second = apply_edge_registrations(
        batch, entities=entities, joins=joins, prior_edges=first.edges
    )
    assert first.registered_count == 1
    assert second.registered_count == 0
    fp = compute_edge_fingerprint(
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
    )
    assert first.edges[0].edge_fingerprint == fp
    assert len(fp) == 64


def test_xp2_fx_009_path_policy_and_write_confinement(tmp_path: Path) -> None:
    entities, joins = _seed_cross_project()
    vault = tmp_path / "vault"
    vault.mkdir()
    reg = apply_registrations(
        [
            {
                "kind": "entity",
                "global_entity_id": "ge-svc-billing",
                "entity_class": "service",
                "display_name": "Billing",
            },
            {
                "kind": "entity",
                "global_entity_id": "ge-tech-postgres-v1",
                "entity_class": "technology",
                "display_name": "Postgres",
            },
            {
                "kind": "join",
                "project_id": "proj-a",
                "project_local_entity_id": "proj-a:svc:billing",
                "global_entity_id": "ge-svc-billing",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
            {
                "kind": "join",
                "project_id": "proj-b",
                "project_local_entity_id": "proj-b:tech:postgres",
                "global_entity_id": "ge-tech-postgres-v1",
                "evidence_refs": [EVIDENCE_B.as_dict()],
            },
        ]
    )
    write_registry_outputs(reg, vault=vault)

    edge_result = apply_edge_registrations(
        [
            {
                "kind": "edge",
                "edge_id": "xe-dep-billing-postgres-v1",
                "relationship_type": "depends-on",
                "source_global_entity_id": "ge-svc-billing",
                "target_global_entity_id": "ge-tech-postgres-v1",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            }
        ],
        entities=entities,
        joins=joins,
    )
    written = write_edge_outputs(edge_result, vault=vault)
    assert written
    assert all(path.startswith("state/global-entities/edges/") for path in written)
    loaded = load_edge_registry_state(vault)
    assert len(loaded) == 1
    assert loaded[0].to_json() == edge_result.edges[0].to_json()

    for forbidden in (
        "relationships/x.json",
        "claims/x.json",
        "state/current-state/x.json",
        "state/authoritative-state/x.json",
        "generated/graph/relationships/x.json",
        "state/global-entities/joins/x.json",
        "generated/query/x.json",
        "../escape.json",
    ):
        with pytest.raises(XprojEdgeError):
            promote_edge_path_forbidden(forbidden)


def test_xp2_fx_010_endpoint_guess_and_unknown_type() -> None:
    entities, joins = _seed_cross_project()
    guess = register_global_edge(
        edge_id="xe-guess",
        relationship_type="depends-on",
        source_global_entity_id=None,
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(guess, EdgeQuarantineCandidate)
    assert guess.category == "endpoint-guess-forbidden"

    unknown = register_global_edge(
        edge_id="xe-unk",
        relationship_type="owns",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(unknown, EdgeQuarantineCandidate)
    assert unknown.category == "unknown-relationship-type"


def test_xp2_fx_011_inspect_summary() -> None:
    entities, joins = _seed_cross_project()
    result = apply_edge_registrations(
        [
            {
                "kind": "edge",
                "edge_id": "xe-ok",
                "relationship_type": "depends-on",
                "source_global_entity_id": "ge-svc-billing",
                "target_global_entity_id": "ge-tech-postgres-v1",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
            {
                "kind": "edge",
                "edge_id": "xe-bad",
                "relationship_type": "depends-on",
                "source_global_entity_id": "ge-missing",
                "target_global_entity_id": "ge-tech-postgres-v1",
                "evidence_refs": [EVIDENCE_A.as_dict()],
            },
        ],
        entities=entities,
        joins=joins,
    )
    summary = inspect_edge_registry(result)
    assert summary["registered"] == 1
    assert summary["quarantined"] == 1
    assert summary["authority_level"] == "derived"
    assert summary["package_id"] == PACKAGE_ID


def test_xp2_adv_path_escape_evidence() -> None:
    entities, joins = _seed_cross_project()
    with pytest.raises(XprojEdgeError, match="path-escape"):
        register_global_edge(
            edge_id="xe-escape",
            relationship_type="depends-on",
            source_global_entity_id="ge-svc-billing",
            target_global_entity_id="ge-tech-postgres-v1",
            evidence_refs=[{"relative_path": "../secret.md", "sha256": "c" * 64}],
            entities=entities,
            joins=joins,
        )


def test_xp2_adv_malformed_edge_id() -> None:
    entities, joins = _seed_cross_project()
    outcome = register_global_edge(
        edge_id="!!!",
        relationship_type="depends-on",
        source_global_entity_id="ge-svc-billing",
        target_global_entity_id="ge-tech-postgres-v1",
        evidence_refs=[EVIDENCE_A],
        entities=entities,
        joins=joins,
    )
    assert isinstance(outcome, EdgeQuarantineCandidate)
    assert outcome.category == "edge-id-invalid"


def test_xp2_json_escape_name_only_does_not_persist_secret(tmp_path: Path) -> None:
    """AS-SEC-SCAN-XPROJ-QUAR-JSON-ESC-001: name-only edge after JSON \\u decode."""
    token = "AKIAAAAAAAAAAAAAAAAA"
    raw = (
        '{"edges":[{"kind":"edge","source_display_name":'
        '"\\u0041KIAAAAAAAAAAAAAAAAA","target_display_name":"other"}]}'
    )
    assert scan_text(raw) == []
    payload = json.loads(raw)
    result = apply_edge_registrations(payload["edges"], entities={}, joins=[])
    assert result.quarantine
    assert result.quarantine[0].category == "secret-finding"
    assert result.quarantine[0].inputs_considered["source_display_name"] == "[redacted-scan]"
    vault = tmp_path / "vault"
    vault.mkdir()
    written = write_edge_outputs(result, vault=vault)
    blob = "".join((vault / path).read_text(encoding="utf-8") for path in written)
    assert token not in blob
    assert token not in "".join(written)
    assert scan_text(blob) == []
