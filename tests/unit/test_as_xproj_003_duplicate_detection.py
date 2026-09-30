"""AS-XPROJ-003 — Duplicate / successor detection (fixture matrix + ADV)."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.schema import available_schemas, validate_record
from project_atlas.xproj_duplicates import (
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    DuplicateCandidate,
    XprojDuplicateError,
    both_projects_remain_ingestable,
    claims_authority_paths_untouched,
    detect_project_duplicates,
    inspect_duplicate_detection,
    normalize_canonical_remote_url,
    promote_duplicate_path_forbidden,
    write_duplicate_outputs,
)

PID_A = "proj-acme-widgets-a"
PID_B = "proj-acme-widgets-b"
PID_C = "proj-acme-other"
PID_PRED = "proj-acme-widgets-v1"


def test_xproj_003_schema_registered() -> None:
    assert "xproj-duplicate-candidate" in available_schemas()


def test_normalize_canonical_remote_url_exact() -> None:
    left = normalize_canonical_remote_url("https://github.com/Acme/Widgets.git/")
    right = normalize_canonical_remote_url("https://github.com/acme/widgets")
    assert left == right
    assert left == "https://github.com/acme/widgets"


def test_xp3_fx_001_url_collision_review_candidate() -> None:
    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "canonical_remote_url": "https://github.com/acme/widgets.git"},
            {"project_id": PID_B, "canonical_remote_url": "https://github.com/acme/widgets"},
        ]
    )
    assert result.review_count == 1
    candidate = result.candidates[0]
    assert candidate.category == "canonical-remote-url-collision"
    assert set(candidate.project_ids) == {PID_A, PID_B}
    payload = candidate.as_dict()
    assert payload["authority"]["level"] == AUTHORITY_LEVEL == "derived"
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    assert payload["package_id"] == PACKAGE_ID
    assert payload["autocollapse"] is False
    assert "winning_choice" not in payload
    validate_record(payload, "xproj-duplicate-candidate")
    assert both_projects_remain_ingestable(candidate)


def test_xp3_fx_002_both_projects_remain_independent(tmp_path: Path) -> None:
    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "canonical_remote_url": "https://example.com/r/one.git"},
            {"project_id": PID_B, "canonical_remote_url": "https://example.com/r/one"},
        ]
    )
    written = write_duplicate_outputs(result, vault=tmp_path)
    assert written
    assert all(path.startswith("generated/xproj/duplicate-candidates/") for path in written)
    for candidate in result.candidates:
        assert both_projects_remain_ingestable(candidate)
        assert set(candidate.project_ids) == {PID_A, PID_B}


def test_xp3_fx_003_name_only_fuzzy_llm_forbidden() -> None:
    named = detect_project_duplicates(
        [{"project_id": PID_A}, {"project_id": PID_B}],
        match_by_name=True,
    )
    assert named.review_count == 0
    assert named.rejects[0].category == "name-only-match-forbidden"
    validate_record(named.rejects[0].as_dict(), "xproj-duplicate-candidate")

    fuzzy = detect_project_duplicates(
        [{"project_id": PID_A}, {"project_id": PID_B}],
        fuzzy=True,
    )
    assert fuzzy.rejects[0].category == "fuzzy-match-forbidden"

    llm = detect_project_duplicates(
        [{"project_id": PID_A}, {"project_id": PID_B}],
        llm=True,
    )
    assert llm.rejects[0].category == "llm-match-forbidden"


def test_xp3_fx_004_autocollapse_forbidden() -> None:
    result = detect_project_duplicates(
        [{"project_id": PID_A}, {"project_id": PID_B}],
        rewrite_uuids=True,
    )
    assert result.review_count == 0
    assert result.rejects[0].category == "autocollapse-forbidden"
    assert result.rejects[0].as_dict()["autocollapse"] is False


def test_xp3_fx_005_identity_lock_collision() -> None:
    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "identity_lock_key": "lock-widgets-v1"},
            {"project_id": PID_B, "identity_lock_key": "LOCK-WIDGETS-V1"},
        ]
    )
    assert result.review_count == 1
    assert result.candidates[0].category == "identity-lock-collision"
    validate_record(result.candidates[0].as_dict(), "xproj-duplicate-candidate")


def test_xp3_fx_006_lineage_and_explicit_successor() -> None:
    lineage = detect_project_duplicates(
        [
            {
                "project_id": PID_A,
                "lineage_id": "sline-shared",
                "retired_slot_id": "slot-1",
            },
            {
                "project_id": PID_B,
                "lineage_id": "sline-shared",
                "retired_slot_id": "slot-1",
            },
        ]
    )
    assert any(item.category == "lineage-retired-slot-collision" for item in lineage.candidates)

    successor = detect_project_duplicates(
        [{"project_id": PID_B, "explicit_successor_of": PID_PRED}]
    )
    assert any(item.category == "explicit-successor" for item in successor.candidates)
    assert set(successor.candidates[0].project_ids) == {PID_B, PID_PRED}


def test_xp3_fx_007_monorepo_path_overlap_approved_roots() -> None:
    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "root_path": "portfolio/monorepo/apps/alpha"},
            {"project_id": PID_B, "root_path": "portfolio/monorepo/apps"},
        ],
        approved_monorepo_roots=["portfolio/monorepo"],
    )
    assert result.review_count == 1
    assert result.candidates[0].category == "monorepo-path-prefix-overlap"


def test_xp3_fx_008_monorepo_without_approved_roots_no_emit() -> None:
    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "root_path": "portfolio/monorepo/apps/alpha"},
            {"project_id": PID_B, "root_path": "portfolio/monorepo/apps"},
        ]
    )
    assert result.review_count == 0


def test_xp3_fx_009_name_fields_in_observation_rejected() -> None:
    result = detect_project_duplicates([{"project_id": PID_A, "display_name": "Widgets"}])
    assert result.review_count == 0
    assert any(item.category == "invalid-observation" for item in result.rejects)


def test_xp3_fx_010_deterministic_replay() -> None:
    projects = [
        {"project_id": PID_A, "canonical_remote_url": "https://example.com/widgets.git"},
        {"project_id": PID_B, "canonical_remote_url": "https://example.com/widgets"},
        {"project_id": PID_C, "canonical_remote_url": "https://example.com/other"},
    ]
    first = detect_project_duplicates(projects)
    second = detect_project_duplicates(list(reversed(projects)))
    assert [item.candidate_id for item in first.candidates] == [
        item.candidate_id for item in second.candidates
    ]
    assert inspect_duplicate_detection(first)["autocollapse"] is False


def test_xp3_fx_011_path_policy_and_write_confinement(tmp_path: Path) -> None:
    with pytest.raises(XprojDuplicateError):
        promote_duplicate_path_forbidden("generated/xproj/indexes/x.json")
    with pytest.raises(XprojDuplicateError):
        promote_duplicate_path_forbidden("claims/x.json")
    promote_duplicate_path_forbidden("generated/xproj/duplicate-candidates/xdup-abc.json")
    assert "claims/" in claims_authority_paths_untouched()
    assert "generated/xproj/indexes/" in claims_authority_paths_untouched()

    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "canonical_remote_url": "https://example.com/z"},
            {"project_id": PID_B, "canonical_remote_url": "https://example.com/z"},
        ]
    )
    written = write_duplicate_outputs(result, vault=tmp_path)
    for relative in written:
        assert relative.startswith("generated/xproj/duplicate-candidates/")
        assert ".." not in relative


def test_adv_path_escape_rejected(tmp_path: Path) -> None:
    from project_atlas.xproj_duplicates import _safe_vault_relative

    with pytest.raises(XprojDuplicateError, match=r"write-prefix-forbidden|path-escape"):
        _safe_vault_relative(tmp_path, "generated/indexes/evil.json")
    with pytest.raises(XprojDuplicateError, match=r"path-escape"):
        _safe_vault_relative(tmp_path, "generated/xproj/duplicate-candidates/../evil.json")


def test_adv_claim_mtime_untouched(tmp_path: Path) -> None:
    claims = tmp_path / "claims"
    claims.mkdir()
    claim_file = claims / "claim-1.json"
    claim_file.write_text('{"id":"claim-1"}\n', encoding="utf-8")
    before = claim_file.stat().st_mtime_ns
    result = detect_project_duplicates(
        [
            {"project_id": PID_A, "canonical_remote_url": "https://example.com/same"},
            {"project_id": PID_B, "canonical_remote_url": "https://example.com/same"},
        ]
    )
    write_duplicate_outputs(result, vault=tmp_path)
    after = claim_file.stat().st_mtime_ns
    assert before == after


def test_adv_candidate_shape_no_winner() -> None:
    candidate = DuplicateCandidate(
        candidate_id="xdup-adv",
        category="canonical-remote-url-collision",
        reason="adv",
        project_ids=(PID_A, PID_B),
        signal="canonical-remote-url",
        inputs_considered={"project_ids": [PID_A, PID_B]},
        status="review_candidate",
    )
    assert both_projects_remain_ingestable(candidate)
    assert "winning_choice" not in candidate.as_dict()
    validate_record(candidate.as_dict(), "xproj-duplicate-candidate")
