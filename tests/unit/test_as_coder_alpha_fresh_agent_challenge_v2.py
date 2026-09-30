"""AS-CODER-ALPHA-FRESH-AGENT-CHALLENGE-V2 — machine-scored harness tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.fresh_agent_challenge import (
    PACKAGE_ID,
    REQUIRED_SLOTS,
    ExtractedAnswer,
    FreshAgentChallengeError,
    adapt_generated_context,
    build_challenge_pack,
    expected_catalog_for,
    extract_answers_from_pack,
    list_estate_projects,
    locate_demo_estate,
    pack_has_hidden_benchmark,
    run_estate_challenge,
    score_as_dict,
    score_challenge,
    write_challenge_receipt,
)

ESTATE = Path("tests/fixtures/demo/estate")


def _estate() -> Path:
    return locate_demo_estate(Path.cwd())


def test_demo_estate_lists_harbor_projects() -> None:
    projects = list_estate_projects(_estate())
    assert projects == ["harbor-api", "harbor-ops", "harbor-portal"]


def test_harbor_api_pack_covers_required_slots_without_hidden_answers() -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    assert pack["package_id"] == PACKAGE_ID
    assert pack["estate_kind"] == "DEMO_FIXTURE"
    assert pack["honesty"]["authentic_pilot"] is False
    assert pack["honesty"]["demo_fixture_ne_authentic_pilot"] is True
    assert pack["honesty"]["hidden_benchmark_answers"] is False
    assert pack["honesty"]["network_required"] is False
    assert set(pack["slots"]) == set(REQUIRED_SLOTS)
    assert pack_has_hidden_benchmark(pack) is False
    assert "generated_at" not in pack
    catalog = expected_catalog_for("harbor-api")
    score = score_challenge(pack, catalog)
    assert score.context_coverage == 1.0
    assert score.context_accuracy == 1.0
    assert score.stale_context_rate == 0.0
    assert score.unknown_honesty == 1.0
    assert score.cross_project_leak_count == 0
    assert score.hidden_benchmark_in_pack is False
    assert score.network_required is False
    assert score.reexplanation_required is False


def test_superseded_decision_is_not_governing() -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    statuses = {item["id"]: item["status"] for item in pack["decisions"]}
    assert statuses["ADR-001"] == "ACTIVE_GOVERNING"
    assert statuses["ADR-002"] == "SUPERSEDED"
    gov = pack["slots"]["governing_decisions"]["text"]
    assert "ADR-001" in gov
    assert "PostgreSQL 15" in gov
    assert "ADR-002" not in gov
    assert "MySQL" not in gov


def test_unknown_scores_correct_when_evidence_absent() -> None:
    pack = build_challenge_pack(_estate() / "harbor-portal")
    assert pack["slots"]["what_changed"]["status"] == "UNKNOWN"
    assert pack["slots"]["governing_decisions"]["status"] == "UNKNOWN"
    score = score_challenge(pack, expected_catalog_for("harbor-portal"))
    unknown = {
        item.slot: item.unknown_honest
        for item in score.slot_scores
        if item.unknown_honest is not None
    }
    assert unknown
    assert all(unknown.values())
    assert score.unknown_honesty == 1.0


def test_invented_answer_fails_unknown_honesty() -> None:
    pack = build_challenge_pack(_estate() / "harbor-portal")
    extracted = extract_answers_from_pack(pack)
    invented = dict(extracted)
    invented["what_changed"] = ExtractedAnswer(
        "what_changed",
        "known",
        "Portal migrated to PostgreSQL 16 last week",
        ("invented",),
    )
    score = score_challenge(
        pack,
        expected_catalog_for("harbor-portal"),
        answers=invented,
    )
    assert score.unknown_honesty < 1.0
    changed = next(item for item in score.slot_scores if item.slot == "what_changed")
    assert changed.unknown_honest is False
    assert changed.accurate is False


def test_cross_project_leak_is_counted() -> None:
    pack = build_challenge_pack(_estate() / "harbor-portal")
    extracted = extract_answers_from_pack(pack)
    leaked = dict(extracted)
    leaked["identity"] = ExtractedAnswer(
        "identity",
        "known",
        "Harbor Portal also pins PostgreSQL 16 and an on-call pager",
        ("README.md",),
    )
    score = score_challenge(
        pack,
        expected_catalog_for("harbor-portal"),
        answers=leaked,
    )
    assert score.cross_project_leak_count >= 1


def test_harbor_ops_keeps_intentional_unknowns() -> None:
    pack = build_challenge_pack(_estate() / "harbor-ops")
    blob = json.dumps(pack["slots"], sort_keys=True)
    assert "unknown" in blob.casefold()
    assert "PostgreSQL 16" not in blob
    score = score_challenge(pack, expected_catalog_for("harbor-ops"))
    assert score.context_accuracy == 1.0
    assert score.cross_project_leak_count == 0
    assert score.unknown_honesty == 1.0


def test_estate_run_has_zero_cross_project_leaks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = run_estate_challenge(_estate(), vault=vault)
    assert report["honesty"]["demo_fixture_ne_authentic_pilot"] is True
    assert report["metrics"]["CROSS_PROJECT_LEAK_COUNT"] == 0
    assert {item["project_id"] for item in report["projects"]} == {
        "harbor-api",
        "harbor-ops",
        "harbor-portal",
    }
    for item in report["projects"]:
        metrics = item["score"]["metrics"]
        assert metrics["CONTEXT_COVERAGE"] == 1.0
        assert metrics["CONTEXT_ACCURACY"] == 1.0
        assert metrics["STALE_CONTEXT_RATE"] == 0.0
        assert metrics["UNKNOWN_HONESTY"] == 1.0
        assert item["hidden_benchmark_in_pack"] is False
        receipt = vault / "generated" / "ops" / "fresh-agent" / f"{item['project_id']}.json"
        assert receipt.is_file()
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        assert "generated_at" not in payload
        assert payload["package_id"] == PACKAGE_ID


def test_hidden_benchmark_keys_fail_closed() -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    pack["expected_answers"] = {"identity": "cheat"}
    assert pack_has_hidden_benchmark(pack) is True
    from project_atlas.fresh_agent_challenge import _assert_no_hidden_benchmark

    with pytest.raises(FreshAgentChallengeError, match="hidden-benchmark"):
        _assert_no_hidden_benchmark(pack)


def test_self_dogfood_catalog_is_not_authentic_pilot() -> None:
    catalog = expected_catalog_for("project-atlas")
    assert catalog.estate_kind == "SELF_DOGFOOD_DEMO"
    assert catalog.authenticity["authentic_pilot"] is False
    assert catalog.authenticity["demo_fixture_ne_authentic_pilot"] is True
    adapted = adapt_generated_context(
        {
            "project_id": "project-atlas",
            "brief": {
                "purpose": (
                    "Project Atlas is the persistent brain for AI-native projects (Coder Alpha)."
                ),
                "changed": "UNKNOWN",
                "unknown": "UNKNOWN coverage gaps remain",
            },
            "markdown": "persistent brain / Coder Alpha / UNKNOWN stays UNKNOWN",
        }
    )
    assert adapted["estate_kind"] == "SELF_DOGFOOD_DEMO"
    assert adapted["honesty"]["authentic_pilot"] is False
    score = score_challenge(adapted, catalog)
    assert score.unknown_honesty == 1.0
    identity = next(item for item in score.slot_scores if item.slot == "identity")
    assert identity.accurate is True


def test_score_receipt_is_deterministic(tmp_path: Path) -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    score = score_challenge(pack, expected_catalog_for("harbor-api"))
    first = score_as_dict(score)
    second = score_as_dict(score)
    assert first == second
    path = write_challenge_receipt(tmp_path, score)
    again = write_challenge_receipt(tmp_path, score)
    assert path.read_bytes() == again.read_bytes()


def test_unknown_catalog_fails_closed() -> None:
    with pytest.raises(FreshAgentChallengeError, match="no-canonical-catalog"):
        expected_catalog_for("not-a-real-project")


def test_locate_demo_estate_from_repo_root() -> None:
    assert locate_demo_estate(Path.cwd()).name == "estate"
    assert ESTATE.exists()


def test_extractor_is_pack_only() -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    answers = extract_answers_from_pack(pack)
    assert set(answers) == set(REQUIRED_SLOTS)
    assert "PostgreSQL 15" in answers["unknown_conflict"].text
    assert "PostgreSQL 16" in answers["unknown_conflict"].text
    assert answers["what_changed"].status == "known"
    assert "audit" in answers["what_changed"].text.casefold()


def test_pack_project_mismatch_fails() -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    with pytest.raises(FreshAgentChallengeError, match="project-mismatch"):
        score_challenge(pack, expected_catalog_for("harbor-ops"))


def test_no_model_specific_prompt_fields() -> None:
    pack = build_challenge_pack(_estate() / "harbor-api")
    blob = json.dumps(pack, sort_keys=True).casefold()
    for token in ("system prompt", "claude", "gpt-4", "temperature", "you are"):
        assert token not in blob
