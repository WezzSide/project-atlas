"""AS-ID-001 identity contract tests."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from project_atlas.discovery import _sha256 as discovery_sha256
from project_atlas.domain import PathHistoryEntry, SourceLineageRecord
from project_atlas.lineage import (
    UnresolvedIdentityError,
    build_project_registry,
    migrate_v1_records_with_receipts,
)
from project_atlas.schema import validate_record
from project_atlas.source_identity import (
    canonical_source_sha256,
    canonicalize_project_path,
    lineage_id,
    validate_project_uuid,
)


def test_project_uuid_requires_uuidv4() -> None:
    value = str(uuid.uuid4())
    assert validate_project_uuid(value) == value
    with pytest.raises(ValueError):
        validate_project_uuid(str(uuid.uuid1()))
    with pytest.raises(ValueError):
        validate_project_uuid("not-a-uuid")


def test_canonical_path_is_host_independent_and_nfc_normalized() -> None:
    assert canonicalize_project_path("docs\\Cafe\u0301.md") == "docs/Caf\u00e9.md"
    with pytest.raises(ValueError):
        canonicalize_project_path("../outside.md")
    with pytest.raises(ValueError):
        canonicalize_project_path("/absolute.md")
    with pytest.raises(ValueError):
        canonicalize_project_path("C:/absolute.md")


def test_discovery_sha256_normalizes_text_line_endings(tmp_path: Path) -> None:
    """AS-ID-001: CRLF vs LF must not change canonical source identity."""
    lf_file = tmp_path / "doc.md"
    crlf_file = tmp_path / "doc_crlf.md"
    lf_file.write_bytes(b"# Heading\nline one\nline two\n")
    crlf_file.write_bytes(b"# Heading\r\nline one\r\nline two\r\n")
    assert discovery_sha256(lf_file) == discovery_sha256(crlf_file)


def test_discovery_sha256_preserves_binary_bytes(tmp_path: Path) -> None:
    """Binary files are hashed as-is; no line-ending normalization applies."""
    binary = tmp_path / "data.bin"
    binary.write_bytes(b"\r\n\r\n")
    assert discovery_sha256(binary) == hashlib.sha256(b"\r\n\r\n").hexdigest()


def test_canonical_source_hash_normalizes_crlf_split_across_chunk_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-005: streaming normalization handles a CRLF split between chunks."""
    from project_atlas import source_identity

    monkeypatch.setattr(source_identity, "_HASH_CHUNK_SIZE", 4)
    crlf_file = tmp_path / "split.md"
    lf_file = tmp_path / "canonical.md"
    crlf_file.write_bytes(b"abc\r\ndef\r\n")
    lf_file.write_bytes(b"abc\ndef\n")
    assert canonical_source_sha256(crlf_file) == canonical_source_sha256(lf_file)


def test_lineage_id_uses_amended_formula() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000001"
    material = "atlas/source-lineage/v1|" + project_uuid + "|docs/README.md|" + "a" * 64 + "|1"
    expected = "sline-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    assert lineage_id(project_uuid, "docs/README.md", "a" * 64, 1) == expected


def test_source_registry_v2_is_strict_and_schema_locked() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000001"
    record = SourceLineageRecord(
        source_id="source-1",
        source_lineage_id=lineage_id(project_uuid, "README.md", "a" * 64, 1),
        lineage_generation=1,
        canonical_project_id=project_uuid,
        first_seen_path="README.md",
        current_path="README.md",
        path_history=[PathHistoryEntry(path="README.md", from_sequence=1)],
        first_content_sha256="a" * 64,
        current_content_sha256="a" * 64,
        first_seen_sequence=1,
        document_lifecycle="verified",
        source_change_state="new",
    )
    validate_record(record, "source-registry")
    with pytest.raises(ValueError):
        SourceLineageRecord.model_validate({**record.model_dump(), "schema_version": 1})
    with pytest.raises(ValueError):
        validate_record(
            {**record.model_dump(), "canonical_project_id": str(uuid.uuid1())},
            "source-registry",
        )


def test_project_identity_lock_is_single_winner_and_releases(tmp_path: Path) -> None:
    from project_atlas.source_identity import IdentityLockError, ProjectIdentityLock

    lock_path = tmp_path / ".atlas" / "identity.lock"
    first = ProjectIdentityLock(lock_path, wait_seconds=0.01, poll_seconds=0.001)
    second = ProjectIdentityLock(lock_path, wait_seconds=0.01, poll_seconds=0.001)
    first.acquire()
    try:
        with pytest.raises(IdentityLockError):
            second.acquire()
        assert lock_path.is_file()
    finally:
        first.release()
    second.acquire()
    second.release()
    assert not lock_path.exists()


def test_copy_gets_distinct_lineage_and_ambiguous_restore_fails_closed() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000041"
    first = {
        "source_id": "source-first",
        "path": "README.md",
        "sha256": "a" * 64,
    }
    initial = build_project_registry(project_uuid, [first], [])
    copied = build_project_registry(
        project_uuid,
        [first, {"source_id": "source-copy", "path": "COPY.md", "sha256": "a" * 64}],
        initial,
    )
    identities = {item["source_lineage_id"] for item in copied}
    assert len(identities) == 2
    tombstoned = build_project_registry(project_uuid, [], copied)
    assert all(item["source_change_state"] == "deleted" for item in tombstoned)
    with pytest.raises(ValueError, match="unresolved-identity"):
        build_project_registry(
            project_uuid,
            [{"source_id": "source-new", "path": "README.md", "sha256": "a" * 64}],
            tombstoned,
        )


def test_migration_builds_chain_receipt_and_is_order_independent() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000051"
    first = {
        "source_id": "source-old",
        "path": "README.md",
        "sha256": "a" * 64,
        "first_seen": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T00:00:00Z",
        "document_lifecycle": "verified",
        "source_change_state": "renamed",
    }
    moved = {
        "source_id": "source-moved",
        "path": "docs/README.md",
        "sha256": "a" * 64,
        "first_seen": "2026-01-02T00:00:00Z",
        "last_seen": "2026-01-03T00:00:00Z",
        "renamed_from": "README.md",
        "document_lifecycle": "verified",
        "source_change_state": "unchanged",
    }
    migrated, receipts = migrate_v1_records_with_receipts([moved, first], project_uuid)
    migrated_again, receipts_again = migrate_v1_records_with_receipts([first, moved], project_uuid)
    assert migrated == migrated_again
    assert receipts == receipts_again
    assert len(migrated) == 1
    validate_record(migrated[0], "source-registry")
    assert receipts[0]["chain_members"] == ["source-old", "source-moved"]
    assert receipts[0]["ordering_key"] == {
        "first_seen": "2026-01-01T00:00:00Z",
        "canonical_origin_path": "README.md",
        "first_content_sha256": "a" * 64,
    }
    assert receipts[0]["continuity_chain"] == ["source-old", "source-moved"]
    assert receipts[0]["evidence_edges"][0]["relationship"] == "renamed_from"


def test_migration_rejects_cycles_and_missing_history() -> None:
    common = {
        "sha256": "a" * 64,
        "document_lifecycle": "verified",
        "source_change_state": "renamed",
    }
    with pytest.raises(ValueError, match="cycle"):
        migrate_v1_records_with_receipts(
            [
                {**common, "source_id": "source-a", "path": "A.md", "renamed_from": "B.md"},
                {**common, "source_id": "source-b", "path": "B.md", "renamed_from": "A.md"},
            ],
            "00000000-0000-4000-8000-000000000061",
        )
    with pytest.raises(ValueError, match="incomplete"):
        migrate_v1_records_with_receipts(
            [{**common, "source_id": "source-a", "path": "A.md", "renamed_from": "missing.md"}],
            "00000000-0000-4000-8000-000000000061",
        )


def test_case_rename_and_retired_path_change_are_distinct_outcomes() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000071"
    first = build_project_registry(
        project_uuid,
        [{"source_id": "source-readme", "path": "README.md", "sha256": "a" * 64}],
        [],
    )
    renamed = build_project_registry(
        project_uuid,
        [{"source_id": "source-readme-new", "path": "readme.md", "sha256": "a" * 64}],
        first,
    )
    assert renamed[0]["source_change_state"] == "renamed"
    assert renamed[0]["source_lineage_id"] == first[0]["source_lineage_id"]
    deleted = build_project_registry(project_uuid, [], first)
    with pytest.raises(UnresolvedIdentityError) as first_error:
        build_project_registry(
            project_uuid,
            [{"source_id": "source-new", "path": "README.md", "sha256": "b" * 64}],
            deleted,
        )
    with pytest.raises(UnresolvedIdentityError) as second_error:
        build_project_registry(
            project_uuid,
            [{"source_id": "source-new", "path": "README.md", "sha256": "b" * 64}],
            deleted,
        )
    assert first_error.value.finding == second_error.value.finding


def test_identical_retired_path_reoccupation_restores_unique_lineage() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000075"
    initial = build_project_registry(
        project_uuid,
        [{"source_id": "source-old", "path": "slot.md", "sha256": "a" * 64}],
        [],
    )
    deleted = build_project_registry(project_uuid, [], initial)
    restored = build_project_registry(
        project_uuid,
        [{"source_id": "source-restored", "path": "slot.md", "sha256": "a" * 64}],
        deleted,
    )
    assert restored[0]["source_lineage_id"] == initial[0]["source_lineage_id"]
    assert restored[0]["source_change_state"] == "restored"


def test_multiple_retired_generations_at_one_slot_fail_closed() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000076"
    first = build_project_registry(
        project_uuid,
        [{"source_id": "source-old", "path": "slot.md", "sha256": "a" * 64}],
        [],
    )[0]
    first_deleted = SourceLineageRecord(
        **{
            **first,
            "source_change_state": "deleted",
            "document_lifecycle": "historical",
        }
    ).model_dump(mode="json")
    second = SourceLineageRecord(
        **{
            **first,
            "source_id": "source-second",
            "source_lineage_id": lineage_id(project_uuid, "slot.md", "a" * 64, 2),
            "lineage_generation": 2,
            "source_change_state": "deleted",
            "document_lifecycle": "historical",
        }
    ).model_dump(mode="json")
    with pytest.raises(UnresolvedIdentityError):
        build_project_registry(
            project_uuid,
            [{"source_id": "source-new", "path": "slot.md", "sha256": "a" * 64}],
            [first_deleted, second],
        )


def test_registry_lineage_collision_fails_closed() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000081"
    record = build_project_registry(
        project_uuid,
        [{"source_id": "source-one", "path": "one.md", "sha256": "a" * 64}],
        [],
    )[0]
    with pytest.raises(ValueError, match="collision"):
        build_project_registry(
            project_uuid,
            [{"source_id": "source-one", "path": "one.md", "sha256": "a" * 64}],
            [record, record],
        )


def test_retired_slot_explicit_new_generation_populates_relationships() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000091"
    initial = build_project_registry(
        project_uuid, [{"source_id": "old", "path": "slot.md", "sha256": "a" * 64}], []
    )
    deleted = build_project_registry(project_uuid, [], initial)
    old_id = initial[0]["source_lineage_id"]
    resolution = {
        "outcome": "create_new_generation",
        "authority": "curator_approved",
        "candidate_lineage_ids": [old_id],
        "reason": "curator classified the reoccupation as a new source",
    }
    result = build_project_registry(
        project_uuid,
        [
            {
                "source_id": "new",
                "path": "slot.md",
                "sha256": "a" * 64,
                "lineage_resolution": resolution,
            }
        ],
        deleted,
    )
    created = next(item for item in result if item["source_id"] == "new")
    prior = next(item for item in result if item["source_id"] == "old")
    assert created["lineage_generation"] == 2
    assert created["supersedes_lineage"] == old_id
    assert prior["superseded_by_lineage"] == created["source_lineage_id"]


def test_resolution_contract_rejects_invalid_selection_and_replays_finding() -> None:
    project_uuid = "00000000-0000-4000-8000-000000000092"
    initial = build_project_registry(
        project_uuid, [{"source_id": "old", "path": "slot.md", "sha256": "a" * 64}], []
    )
    deleted = build_project_registry(project_uuid, [], initial)
    invalid = {
        "outcome": "create_new_generation",
        "authority": "system_proven",
        "candidate_lineage_ids": [initial[0]["source_lineage_id"]],
        "selected_lineage_id": initial[0]["source_lineage_id"],
        "reason": "invalid selection",
    }
    with pytest.raises(ValueError, match="invalid lineage resolution"):
        build_project_registry(
            project_uuid,
            [
                {
                    "source_id": "new",
                    "path": "slot.md",
                    "sha256": "b" * 64,
                    "lineage_resolution": invalid,
                }
            ],
            deleted,
        )
    unresolved = {
        "outcome": "unresolved",
        "authority": "system_proven",
        "candidate_lineage_ids": [initial[0]["source_lineage_id"]],
        "reason": "insufficient continuity evidence",
    }
    args = [
        {
            "source_id": "new",
            "path": "slot.md",
            "sha256": "b" * 64,
            "lineage_resolution": unresolved,
        }
    ]
    with pytest.raises(UnresolvedIdentityError) as first:
        build_project_registry(project_uuid, args, deleted)
    with pytest.raises(UnresolvedIdentityError) as second:
        build_project_registry(project_uuid, args, deleted)
    assert first.value.finding == second.value.finding
    assert first.value.finding["finding_type"] == "unresolved-source-identity"
    assert first.value.finding["observed_content_sha256"] == "b" * 64
    assert first.value.finding["resolution_required"] is True
