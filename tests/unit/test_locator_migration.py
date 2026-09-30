"""Unit tests for locator refinement and alias handling (AS-EXT-001A, §7.10)."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.claim_identity import extract_claims, v2_claim_id
from project_atlas.locator_migration import (
    DiscontinuityRecord,
    LocatorMigration,
    MigrationClass,
    RefinedClaimIdentity,
    build_alias_map_payload,
    classify_mapping_set,
)
from project_atlas.schema import validate_record

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "as-ext-001a"
MIGRATED_AT = "migration-evidence-reference"
COMMIT = "6d874751d3ed9cb05433a8d50ab372a997418d84"


def _identity(claim_id: str, locator: str, field: str = "status") -> RefinedClaimIdentity:
    return RefinedClaimIdentity(
        claim_id=claim_id,
        project_identity="project-atlas",
        source_lineage_id="sline-abc123",
        claim_type="roadmap-status",
        field=field,
        stable_semantic_locator=locator,
        source_commit=COMMIT,
        source_path="docs/evidence/example.yaml",
    )


def _payload(migrations):  # type: ignore[no-untyped-def]
    return build_alias_map_payload(
        "project-atlas",
        migrations,
        migrated_at=MIGRATED_AT,
        source_commits_scanned=1,
    )


def test_one_to_one_becomes_alias_candidate() -> None:
    """§7.10/§10: aliases only for provable one-to-one mappings."""
    migration = LocatorMigration(
        old_claim_id="claim-old00001",
        old_locator="heading:package-summary",
        new=(_identity("claim-new00001", "yamlpath:status"),),
    )
    payload, discontinuities = _payload([migration])
    validate_record(payload, "claim-alias")
    assert discontinuities == ()
    assert len(payload["aliases"]) == 1
    alias = payload["aliases"][0]
    assert alias["v1_claim_id"] == "claim-old00001"
    assert alias["v2_claim_id"] == "claim-new00001"
    assert alias["stable_semantic_locator"] == "yamlpath:status"
    assert payload["ambiguous"] == []
    assert payload["audit"]["output_aliases"] == 1


def test_one_to_many_stays_ambiguous() -> None:
    """§7.10/§10: one-to-many mappings remain ambiguous, never promoted."""
    migration = LocatorMigration(
        old_claim_id="claim-old00002",
        old_locator="heading:legacy-certification-note",
        new=(
            _identity("claim-new00002", "heading:legacy-certification-note~1"),
            _identity("claim-new00003", "heading:legacy-certification-note~2"),
        ),
    )
    payload, _ = _payload([migration])
    assert payload["aliases"] == []
    assert len(payload["ambiguous"]) == 1
    entry = payload["ambiguous"][0]
    assert entry["v1_claim_id"] == "claim-old00002"
    assert "one-to-many" in entry["reason"]
    assert len(entry["records"]) == 2
    validate_record(payload, "claim-alias")


def test_many_to_one_flagged_semantic_collapse() -> None:
    shared = _identity("claim-new00004", "yamlpath:status")
    migrations = [
        LocatorMigration("claim-old00003", "heading:a", (shared,)),
        LocatorMigration("claim-old00004", "heading:b", (shared,)),
    ]
    classes = classify_mapping_set(migrations)
    assert classes == {
        "claim-old00003": MigrationClass.MANY_TO_ONE,
        "claim-old00004": MigrationClass.MANY_TO_ONE,
    }
    payload, _ = _payload(migrations)
    assert payload["aliases"] == []
    assert all("semantic collapse" in item["reason"] for item in payload["ambiguous"])


def test_many_to_many_flagged_manual() -> None:
    first = LocatorMigration(
        "claim-old00005",
        "heading:a",
        (_identity("claim-new00005", "yamlpath:a"), _identity("claim-new00006", "yamlpath:b")),
    )
    second = LocatorMigration(
        "claim-old00006",
        "heading:b",
        (_identity("claim-new00006", "yamlpath:b"),),
    )
    classes = classify_mapping_set([first, second])
    assert classes["claim-old00005"] is MigrationClass.MANY_TO_MANY
    assert classes["claim-old00006"] is MigrationClass.MANY_TO_ONE
    payload, _ = _payload([first, second])
    assert payload["aliases"] == []
    assert any("manual or profile-specific" in item["reason"] for item in payload["ambiguous"])


def test_no_stable_old_locator_records_discontinuity() -> None:
    """§7.10: NO STABLE OLD LOCATOR = new identity + explicit discontinuity."""
    migration = LocatorMigration(
        old_claim_id=None,
        old_locator=None,
        new=(_identity("claim-new00007", "yamlpath:status"),),
    )
    classes = classify_mapping_set([migration])
    assert classes == {"claim-new00007": MigrationClass.NO_STABLE_OLD_LOCATOR}
    payload, discontinuities = _payload([migration])
    assert payload["aliases"] == []
    assert payload["ambiguous"] == []
    assert discontinuities == (
        DiscontinuityRecord(
            new_claim_id="claim-new00007",
            old_locator=None,
            reason=("no stable old locator: new identity with explicit historical discontinuity"),
        ),
    )
    validate_record(payload, "claim-alias")


def test_synthetic_one_to_many_fixture_scenario() -> None:
    """Fixture 18: one legacy heading locator refines to many new identities."""
    text = (FIXTURES / "synthetic" / "one-to-many-migration.md").read_text(encoding="utf-8")
    claims = extract_claims(text, reject_unresolved=True, withhold_unresolvable=True)
    old_claim_id = "claim-legacydup"
    refined = tuple(
        _identity(
            v2_claim_id(
                "project-atlas",
                "sline-abc123",
                str(claim["claim_type"]),
                str(claim["field"]),
                str(claim["locator"]),
            ),
            str(claim["locator"]),
            field=str(claim["field"]),
        )
        for claim in claims
        if claim["field"] == "roadmap"
    )
    assert len(refined) == 2  # two status statements under one heading
    migration = LocatorMigration(
        old_claim_id=old_claim_id,
        old_locator="heading:legacy-certification-note",
        new=refined,
    )
    payload, _ = _payload([migration])
    assert payload["aliases"] == []
    assert len(payload["ambiguous"][0]["records"]) == 2


def test_duplicate_old_id_entries_aggregated() -> None:
    """Duplicate migration entries sharing one historical id must aggregate
    into a single decision: the set-level truth is ONE OLD → MANY NEW, so the
    id lands in ambiguous exactly once with all sorted candidate records —
    never in both aliases and ambiguous (CORE3-012 mutual exclusion)."""
    shared_old = "claim-old00007"
    migrations = [
        LocatorMigration(shared_old, "heading:a", (_identity("claim-new00008", "yamlpath:a"),)),
        LocatorMigration(
            shared_old,
            "heading:a",
            (
                _identity("claim-new00009", "yamlpath:b"),
                _identity("claim-new00010", "yamlpath:c"),
            ),
        ),
    ]
    classes = classify_mapping_set(migrations)
    assert classes == {shared_old: MigrationClass.ONE_TO_MANY}
    payload, _ = _payload(migrations)
    assert payload["aliases"] == []
    assert len(payload["ambiguous"]) == 1
    entry = payload["ambiguous"][0]
    assert entry["v1_claim_id"] == shared_old
    assert [item["v2_claim_id"] for item in entry["records"]] == [
        "claim-new00008",
        "claim-new00009",
        "claim-new00010",
    ]
    assert payload["audit"]["input_claims"] == 1
    validate_record(payload, "claim-alias")


def test_payload_deterministic() -> None:
    migrations = [
        LocatorMigration(
            "claim-old00008",
            "heading:x",
            (
                _identity("claim-new00011", "yamlpath:b"),
                _identity("claim-new00010", "yamlpath:a"),
            ),
        ),
        LocatorMigration(
            "claim-old00009", "heading:y", (_identity("claim-new00012", "yamlpath:c"),)
        ),
    ]
    first, _ = _payload(migrations)
    second, _ = _payload(list(reversed(migrations)))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    # Candidate records inside an ambiguous entry are sorted by new claim id.
    records = first["ambiguous"][0]["records"]
    assert [item["v2_claim_id"] for item in records] == ["claim-new00010", "claim-new00011"]
