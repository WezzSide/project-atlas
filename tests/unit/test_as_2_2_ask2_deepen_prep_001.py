"""AS-2.2-ASK2-DEEPEN-PREP-001 — docs/contracts/fixtures only (no src mutation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from _atlas_2_2_maturity import assert_prep_branch_scope

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs" / "atlas-2.2" / "ask-atlas-2"
CONTRACTS = PREP / "contracts"
FIXTURES = PREP / "fixtures"
RESEARCH_ASK = ROOT / "docs" / "atlas-2.2" / "research" / "ASK-ATLAS-2.md"
RESEARCH_ANSWER_STUB = (
    ROOT / "docs" / "atlas-2.2" / "contracts" / "research" / "ask-atlas-2-answer.schema.json"
)

DOCS = {
    "package": PREP / "AS-2.2-ASK2-DEEPEN-PREP-001.md",
    "architecture": PREP / "ARCHITECTURE.md",
    "contract": PREP / "CONTRACT.md",
    "invariants": PREP / "INVARIANTS.md",
    "fixture_plan": PREP / "FIXTURE-PLAN.md",
    "adr": PREP / "adr" / "ADR-2.2-ASK2-001-answer-lens-deepen-prep.md",
}

SCHEMA_FILES = {
    "answer": "ask2-deepen-answer-view.schema.json",
    "chain": "ask2-citation-chain.schema.json",
    "lens": "ask2-lens-projection.schema.json",
    "action": "ask2-forbidden-action.schema.json",
}

FIXTURE_SCHEMA = {
    "deepen-answer-complete.sample.json": "answer",
    "citation-chain.sample.json": "chain",
    "lens-projection-web.sample.json": "lens",
    "negative-live-mutate.expect.json": "action",
    "negative-llm-authority.expect.json": "action",
    "negative-canonical-write.expect.json": "action",
}

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


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _load_json(CONTRACTS / SCHEMA_FILES[name])  # type: ignore[return-value]


def test_prep_docs_exist_without_readme() -> None:
    for path in DOCS.values():
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "AS-2.2-ASK2" in text or "Ask Atlas 2" in text
        assert "PREP" in text.upper() or "prep" in text
    assert not (PREP / "README.md").exists()
    assert not any(PREP.rglob("README.md"))


def test_unique_path_deepens_beyond_research_ask2() -> None:
    """Deepen tree must be distinct from research-ask2 paths."""
    assert RESEARCH_ASK.is_file()
    assert RESEARCH_ANSWER_STUB.is_file()
    assert PREP.is_dir()
    assert PREP.resolve() != RESEARCH_ASK.parent.resolve()
    package = DOCS["package"].read_text(encoding="utf-8")
    assert "ask-atlas-2/**" in package or "ask-atlas-2/" in package
    assert "research-ask2" in package.lower() or "RESEARCH-001" in package
    assert "deepen" in package.lower()
    # Deepen stubs must not collide with research stub filenames.
    for filename in SCHEMA_FILES.values():
        assert not (ROOT / "docs" / "atlas-2.2" / "contracts" / "research" / filename).exists()


def test_contract_stubs_exist_and_are_not_package_data() -> None:
    package_schemas = ROOT / "src" / "project_atlas" / "schemas"
    for filename in SCHEMA_FILES.values():
        stub = CONTRACTS / filename
        assert stub.is_file(), stub
        body = stub.read_text(encoding="utf-8")
        assert "PREP STUB" in body
        assert "ask2" in body.lower() or "ask atlas" in body.lower()
        assert not (package_schemas / filename).exists()


def test_fixtures_validate_against_prep_stubs() -> None:
    for fixture_name, schema_key in FIXTURE_SCHEMA.items():
        fixture_path = FIXTURES / fixture_name
        assert fixture_path.is_file(), fixture_path
        instance = _load_json(fixture_path)
        schema = _schema(schema_key)
        jsonschema.validate(instance=instance, schema=schema)


def test_package_card_non_claims() -> None:
    text = DOCS["package"].read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "**NO**" in text
    assert "ATLAS_2_2_INTELLIGENCE_IMPLEMENTATION_UNLOCKED" in text
    assert "ask_atlas_live" in text
    assert "do not mutate" in text.lower() or "NONE" in text


def test_invariants_document_fail_closed_walls() -> None:
    text = DOCS["invariants"].read_text(encoding="utf-8")
    assert "live" in text.lower()
    assert "ask_atlas_live" in text
    assert "UI ≠ canonical" in text or "UI≠canonical" in text
    assert "LLM ≠ authority" in text or "LLM≠authority" in text
    assert "ATLAS_2_1_RELEASE_CERTIFIED" in text
    assert "NO" in text


def test_deepen_answer_retains_research_fields_and_adds_depth() -> None:
    answer = _load_json(FIXTURES / "deepen-answer-complete.sample.json")
    assert isinstance(answer, dict)
    assert answer["package_id"] == "AS-2.2-ASK2-DEEPEN-PREP-001"
    for field in ASK_FIELDS:
        assert field in answer, field
    assert answer["canonical_write"] is False
    assert answer["ui_truth"] is False
    assert answer["graph_authority"] is False
    assert answer["llm_authority"] is False
    assert answer["live_path_owned"] is False
    assert answer["authority"]["level"] == "derived"
    assert answer["evidence_class"] == "fixture-only"
    assert answer["pilot_roots"] == 0
    assert answer["citation_chain_id"]
    surfaces = {lens["surface"] for lens in answer["lenses"]}
    assert surfaces == {"web", "mcp", "cli"}


def test_citation_chain_orders_evidence_hypothesis_pack() -> None:
    chain = _load_json(FIXTURES / "citation-chain.sample.json")
    assert isinstance(chain, dict)
    roles = [node["role"] for node in chain["nodes"]]
    assert "evidence" in roles
    assert "hypothesis" in roles
    assert "pack" in roles
    assert chain["authority"]["level"] == "derived"


def test_negative_actions_are_rejected_forbidden() -> None:
    cases = {
        "negative-live-mutate.expect.json": (
            "live_path_mutate",
            "ask2-live-mutate-forbidden",
        ),
        "negative-llm-authority.expect.json": (
            "llm_authority_stamp",
            "ask2-llm-authority-forbidden",
        ),
        "negative-canonical-write.expect.json": (
            "canonical_write",
            "ask2-canonical-write-forbidden",
        ),
    }
    for name, (kind, error) in cases.items():
        payload = _load_json(FIXTURES / name)
        assert isinstance(payload, dict)
        assert payload["kind"] == kind
        assert payload["status"] == "rejected_forbidden"
        assert payload["expected_error"] == error
        assert payload["authority"]["level"] == "derived"
        assert payload["live_path_owned"] is False


def test_lens_schema_rejects_missing_required_fields() -> None:
    schema = _schema("lens")
    bad = _load_json(FIXTURES / "lens-projection-web.sample.json")
    assert isinstance(bad, dict)
    bad["fields_present"] = ["ANSWER", "WHY"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=schema)


def test_no_runtime_ask_atlas_live_mutation() -> None:
    """Capability-maturity-scoped 2.2 prep guard (D-INTEGRATE-007A).

    Keyed on docs/atlas-2.2/PACKAGE-MATURITY.json: 'ask-atlas-2' must not
    mutate its production surface while prep-frozen; an implementation-
    unlocked capability may legitimately mutate its own surface.
    """
    assert_prep_branch_scope("ask-atlas-2")
