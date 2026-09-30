"""D-150 — Ask2 false-positive grounding adversarial matrix.

Lexical claim-term entailment (``_question_claim_terms`` / ``_build_candidate``)
must reject nearby-but-unsupported retrieval hits. This module hardens that
contract with a synthetic corpus and adversarial questions — not production
ask-string allowlists in ``ask2.py``.

Categories:

* nonexistent financial / revenue claims
* nonexistent API / service names
* absent products
* absent people / entities
* near-semantic distractors (related vocab, wrong subject)
* lexical-overlap distractors
* true positives that remain ``known`` when every claim term is supported
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.ask2 import (
    _QUESTION_FUNCTION_WORDS,
    _question_claim_terms,
    ask_atlas_2,
)
from project_atlas.schema import validate_record

_ASK2_SRC = Path(__file__).resolve().parents[2] / "src" / "project_atlas" / "ask2.py"

# Synthetic corpus subjects — invented for this matrix only.
_CORPUS: tuple[tuple[str, str], ...] = (
    (
        "helix-revenue-forecast",
        "Helix annual revenue forecast is maintained by finance ops.",
    ),
    (
        "helix-billing-api",
        "Helix Billing API service supports invoice retrieval endpoints.",
    ),
    (
        "helix-ledger-product",
        "Helix Ledger product manages invoice reconciliation workflows.",
    ),
    (
        "jordan-forecast-owner",
        "Jordan Rivera owns the Helix revenue forecast process.",
    ),
    (
        "nimbus-billing-api",
        "Nimbus Billing API service supports invoice retrieval endpoints.",
    ),
    (
        "helix-margin-report",
        "Helix margin report summarizes quarterly operating costs.",
    ),
    (
        "pulse-ledger-product",
        "Pulse Ledger product manages subscription reconciliation workflows.",
    ),
)

# (question, category_id) — unsupported claims that must stay unknown.
_UNSUPPORTED_CASES: tuple[tuple[str, str], ...] = (
    ("Nebula annual revenue forecast", "nonexistent-financial"),
    ("Quasar quarterly revenue target", "nonexistent-financial"),
    ("Helix annual revenue target", "nonexistent-financial-overlap"),
    ("Quasar Billing API service", "nonexistent-api-service"),
    ("Helix Payments Gateway service", "nonexistent-api-service"),
    ("Orbit Ledger product", "absent-product"),
    ("Helix Vault product", "absent-product"),
    ("Morgan Lee revenue forecast", "absent-person"),
    ("Casey Quinn owns Helix Billing", "absent-person"),
    ("Jordan Rivera margin forecast", "near-semantic-wrong-subject"),
    ("Nimbus Ledger product invoice", "near-semantic-wrong-subject"),
    ("Pulse Billing API service", "near-semantic-wrong-subject"),
    ("Helix annual margin forecast", "lexical-overlap-distractor"),
    ("Helix Billing product service", "lexical-overlap-distractor"),
    ("Helix Ledger API invoice", "lexical-overlap-distractor"),
)

_REQUIRED_UNKNOWN_CATEGORIES = frozenset(
    {
        "nonexistent-financial",
        "nonexistent-api-service",
        "absent-product",
        "absent-person",
        "near-semantic-wrong-subject",
        "lexical-overlap-distractor",
    }
)

# (question, expected_record_id) — full claim support must stay known.
_SUPPORTED_CASES: tuple[tuple[str, str], ...] = (
    ("Helix annual revenue forecast", "helix-revenue-forecast"),
    ("Helix Billing API service", "helix-billing-api"),
    ("Helix Ledger product", "helix-ledger-product"),
    ("Jordan Rivera Helix revenue forecast", "jordan-forecast-owner"),
    ("Nimbus Billing API service", "nimbus-billing-api"),
    ("What is the Helix Ledger product?", "helix-ledger-product"),
)


def _wr(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _empty_indexes(vault: Path) -> None:
    indexes = vault / "generated" / "indexes"
    indexes.mkdir(parents=True, exist_ok=True)
    for name in (
        "sources.json",
        "claims.json",
        "concepts.json",
        "conflicts.json",
        "authority.json",
        "provenance.json",
    ):
        (indexes / name).write_text("{}\n", encoding="utf-8", newline="\n")


def _adversarial_corpus_vault(tmp_path: Path) -> Path:
    """Nearby records that share vocab with absent-subject questions."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _empty_indexes(vault)
    record_ids = [rid for rid, _ in _CORPUS]
    _wr(
        vault / "generated" / "indexes" / "concepts.json",
        {
            "by_concept_id": {rid: [rid] for rid in record_ids},
            "by_type": {"capability": list(record_ids)},
            "by_project_id": {"demo": list(record_ids)},
            "by_tag": {},
            "by_relationship_target": {},
        },
    )
    _wr(
        vault / "state" / "concepts" / "demo.json",
        {
            "concepts": [
                {
                    "concept_id": record_id,
                    "type": "capability",
                    "project_id": "demo",
                    "summary": summary,
                    "provenance": [{"source_lineage_id": f"lineage-{record_id}"}],
                }
                for record_id, summary in _CORPUS
            ]
        },
    )
    return vault


def _ask(vault: Path, question: str) -> dict[str, Any]:
    return ask_atlas_2(
        vault,
        question=question,
        project_id="demo",
        kinds=("concept",),
        legacy_scan=False,
    )


def _assert_unsupported_stays_unknown(answer: dict[str, Any]) -> None:
    """Nearby retrieval must not flip status to known without claim support."""
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["retrieval"]["result_count"] > 0, (
        "adversarial cases require nearby retrieval hits to exercise claim filter"
    )
    assert answer["retrieval"]["candidate_count"] == 0
    assert answer["status"] == "unknown"
    assert answer["EVIDENCE"] == []
    assert answer["UNKNOWN"]["is_unknown"] is True
    assert "retrieval-hits-lack-claim-support" in answer["UNKNOWN"]["reasons"]


# --------------------------------------------------------------------------- #
# claim-term unit surface (scaffolding ignored; no production allowlists)
# --------------------------------------------------------------------------- #


def test_d150_interrogative_scaffolding_ignored_in_claim_terms() -> None:
    terms = _question_claim_terms("What is the Helix annual revenue forecast overview please?")
    assert "helix" in terms
    assert "annual" in terms
    assert "revenue" in terms
    assert "forecast" in terms
    for scaffold in ("what", "is", "the", "overview", "please", "project"):
        assert scaffold in _QUESTION_FUNCTION_WORDS
        assert scaffold not in terms


def test_d150_empty_or_scaffold_only_question_has_no_claim_terms() -> None:
    assert _question_claim_terms("What is the project overview?") == frozenset()
    assert _question_claim_terms("Please explain how / why?") == frozenset()


def test_d150_ask2_has_no_production_question_allowlist() -> None:
    """Grounding must stay term-subset based — not hardcoded ask strings."""
    src = _ASK2_SRC.read_text(encoding="utf-8")
    forbidden_fragments = (
        "dark-factory",
        "AUTHENTIC_ESTATE",
        "ALLOWED_QUESTIONS",
        "PRODUCTION_ASK",
    )
    for fragment in forbidden_fragments:
        assert fragment not in src, f"unexpected production coupling: {fragment!r}"
    assert "_question_claim_terms" in src
    assert "_record_supports_required_terms" in src


def test_nl_meta_claim_to_use_is_scaffolding_not_global_stopword() -> None:
    """D-181: claim/use stripped only inside claim-to-use phrases."""
    for word in ("claim", "claims", "claiming", "claimed", "use", "uses", "using", "used"):
        assert word not in _QUESTION_FUNCTION_WORDS

    meta = _question_claim_terms("What database does project-a claim to use?")
    assert "database" in meta
    assert "claim" not in meta
    assert "use" not in meta

    claimed = _question_claim_terms("What database is project-a claimed to use?")
    assert "database" in claimed
    assert "claimed" not in claimed
    assert "use" not in claimed

    relational = _question_claim_terms("Does Helix use PostgreSQL?")
    assert "use" in relational
    assert "helix" in relational
    assert "postgresql" in relational


# --------------------------------------------------------------------------- #
# adversarial false-positive matrix (unsupported → unknown)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("question", "category"),
    _UNSUPPORTED_CASES,
    ids=[category for _, category in _UNSUPPORTED_CASES],
)
def test_d150_unsupported_claim_stays_unknown_despite_nearby_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    category: str,
) -> None:
    assert category in {
        "nonexistent-financial",
        "nonexistent-financial-overlap",
        "nonexistent-api-service",
        "absent-product",
        "absent-person",
        "near-semantic-wrong-subject",
        "lexical-overlap-distractor",
    }
    # Synthetic matrix must not resolve a live authentic estate via env leakage
    # from other tests (e.g. D-148 runner temporarily sets AUTHENTIC_ESTATE_ROOT).
    monkeypatch.delenv("AUTHENTIC_ESTATE_ROOT", raising=False)
    required = _question_claim_terms(question)
    assert required, "adversarial questions must carry discriminative claim terms"
    vault = _adversarial_corpus_vault(tmp_path)
    answer = _ask(vault, question)
    _assert_unsupported_stays_unknown(answer)


# --------------------------------------------------------------------------- #
# true positives (full claim support → known)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("question", "expected_record_id"),
    _SUPPORTED_CASES,
    ids=[
        "tp-financial",
        "tp-api-service",
        "tp-product",
        "tp-person-entity",
        "tp-alt-api",
        "tp-scaffolded-product",
    ],
)
def test_d150_supported_claim_remains_known(
    tmp_path: Path, question: str, expected_record_id: str
) -> None:
    vault = _adversarial_corpus_vault(tmp_path)
    answer = _ask(vault, question)
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["status"] == "known"
    assert answer["UNKNOWN"]["is_unknown"] is False
    assert answer["retrieval"]["candidate_count"] >= 1
    ids = {entry["record_id"] for entry in answer["EVIDENCE"]}
    assert expected_record_id in ids


def test_d150_matrix_categories_cover_required_surface() -> None:
    present = {category for _, category in _UNSUPPORTED_CASES}
    # Collapse financial-overlap into the financial family for the coverage set.
    normalized = {
        "nonexistent-financial" if c.startswith("nonexistent-financial") else c for c in present
    }
    assert normalized >= _REQUIRED_UNKNOWN_CATEGORIES
    assert len(_SUPPORTED_CASES) >= 4


def test_d150_numeric_only_discriminator_stays_unknown(tmp_path: Path) -> None:
    """SHADOW-A-002: numeric tokens alone must not ground unsupported claims."""
    vault = _adversarial_corpus_vault(tmp_path)
    answer = _ask(vault, "2024 revenue forecast")
    _assert_unsupported_stays_unknown(answer)


def test_d150_record_id_slug_without_body_terms_stays_unknown(tmp_path: Path) -> None:
    """SHADOW-A-003: record_id/path slugs must not ground without substantive text."""
    vault = tmp_path / "vault"
    _empty_indexes(vault)
    # Body lacks 'xyzzy'; record_id carries xyzzy-special-token.
    record_id = "xyzzy-special-token"
    _wr(
        vault / "generated" / "indexes" / "concepts.json",
        {
            "by_concept_id": {record_id: [record_id]},
            "by_type": {"capability": [record_id]},
            "by_project_id": {"demo": [record_id]},
            "by_tag": {},
            "by_relationship_target": {},
        },
    )
    _wr(
        vault / "state" / "concepts" / "demo.json",
        {
            "concepts": [
                {
                    "concept_id": record_id,
                    "type": "capability",
                    "project_id": "demo",
                    "summary": "Helix margin report summarizes quarterly operating costs.",
                    "provenance": [{"source_lineage_id": "lineage-margin"}],
                }
            ]
        },
    )
    answer = _ask(vault, "xyzzy special token")
    _assert_unsupported_stays_unknown(answer)
