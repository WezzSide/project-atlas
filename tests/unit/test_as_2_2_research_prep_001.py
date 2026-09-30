"""AS-2.2-RESEARCH-001 PREP — docs/fixtures presence + truth boundaries."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
ATLAS_22 = DOCS / "atlas-2.2"
RESEARCH = ATLAS_22 / "research"
CONTRACTS = ATLAS_22 / "contracts" / "research"
FIXTURES = ATLAS_22 / "fixtures" / "research"

PIPELINE = (
    "question",
    "hypotheses",
    "evidence",
    "conflicts",
    "synthesis",
    "packs",
)

ASK_FIELDS = (
    "ANSWER",
    "WHY",
    "WHY_NOT",
    "EVIDENCE",
    "AUTHORITY",
    "TEMPORAL_VALIDITY",
    "CONFLICTS",
    "UNKNOWN",
)


def test_package_card_and_adr_present() -> None:
    assert (DOCS / "AS-2.2-RESEARCH-001.md").is_file()
    assert (DOCS / "adr" / "ADR-025-research-workspace-prep.md").is_file()
    text = (DOCS / "AS-2.2-RESEARCH-001.md").read_text(encoding="utf-8")
    assert "question → hypotheses → evidence → conflicts → synthesis → packs" in text
    assert "PREP ONLY" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text or "Not `ATLAS_2_1_RELEASE_CERTIFIED`" in text


def test_research_docs_present() -> None:
    for name in (
        "README.md",
        "ARCHITECTURE.md",
        "CONTRACT.md",
        "ASK-ATLAS-2.md",
        "FIXTURE-PLAN.md",
        "THREAT-ROWS.md",
    ):
        assert (RESEARCH / name).is_file(), name


def test_contract_stubs_present() -> None:
    stubs = [
        "research-question.schema.json",
        "research-hypothesis.schema.json",
        "research-evidence-ref.schema.json",
        "research-conflict.schema.json",
        "research-synthesis.schema.json",
        "research-evidence-pack.schema.json",
        "ask-atlas-2-answer.schema.json",
    ]
    for name in stubs:
        path = CONTRACTS / name
        assert path.is_file(), name
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "PREP STUB" in payload["title"]
        assert payload["$id"].startswith("https://project-atlas.local/prep/atlas-2.2/")


def test_fixtures_pipeline_and_non_authority() -> None:
    chain = json.loads((FIXTURES / "sample-workspace-chain.json").read_text(encoding="utf-8"))
    assert chain["pipeline"] == list(PIPELINE)
    assert chain["question"]["authentic_estate"] is False
    for hyp in chain["hypotheses"]:
        assert hyp["authority_promoted"] is False

    complete = json.loads((FIXTURES / "expected-pack-complete.json").read_text(encoding="utf-8"))
    assert complete["pipeline"] == list(PIPELINE)
    assert complete["status"] == "COMPLETE"
    assert complete["canonical_write"] is False
    assert complete["authority_promoted"] is False
    assert complete["fixture_safe"] is True
    assert complete["estate_facts_invented"] is False
    assert "generated" in complete and "by" in complete["generated"]
    assert "at" not in complete["generated"]

    incomplete = json.loads(
        (FIXTURES / "expected-pack-incomplete.json").read_text(encoding="utf-8")
    )
    assert incomplete["status"] == "INCOMPLETE"
    assert incomplete["evidence_ref_ids"] == []

    conflicts = json.loads(
        (FIXTURES / "expected-conflicts-retained.json").read_text(encoding="utf-8")
    )
    assert conflicts["conflicts"][0]["retained"] is True
    assert conflicts["silent_winner_forbidden"] is True


def test_ask_atlas_2_answer_shape() -> None:
    answer = json.loads((FIXTURES / "expected-ask-atlas-2-answer.json").read_text(encoding="utf-8"))
    for field in ASK_FIELDS:
        assert field in answer, field
    assert answer["canonical_write"] is False
    assert answer["ui_truth"] is False
    assert answer["graph_authority"] is False
    assert answer["llm_authority"] is False


def test_prep_does_not_claim_release_or_pilot() -> None:
    card = (DOCS / "AS-2.2-RESEARCH-001.md").read_text(encoding="utf-8")
    adr = (DOCS / "adr" / "ADR-025-research-workspace-prep.md").read_text(encoding="utf-8")
    for text in (card, adr):
        assert "PREP" in text
        assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
        assert "ATLAS_2_1_RELEASE_CERTIFIED=YES" not in text
        assert "Not authentic" in text or "Not authentic estate PILOT PASS" in text
        assert "authentic" in text.lower()


def test_no_production_schema_promotion() -> None:
    """Prep stubs must not land in installed package schemas."""
    packaged = ROOT / "src" / "project_atlas" / "schemas"
    for name in (
        "research-question.schema.json",
        "research-evidence-pack.schema.json",
        "ask-atlas-2-answer.schema.json",
    ):
        assert not (packaged / name).exists(), name
