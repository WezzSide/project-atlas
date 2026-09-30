"""D-178 / ASK2_NO_GROUNDING — semantic acceptance matrix.

When admissible grounded claims exist in project scope, Ask2 must not silently
degrade to UNKNOWN / null / zero-conflict. True no-evidence stays UNKNOWN.
Cross-project and forged authority stay excluded. D-150 leftover-noun
entailment remains in force.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_atlas.ask2 import (
    _QUESTION_ATTRIBUTE_FILLERS,
    _question_claim_terms,
    ask_atlas_2,
)
from project_atlas.schema import validate_record

SECRET_TOKEN = "AKIAIOSFODNN7EXAMPLE"
HARBOR_DB_QUESTION = "Which PostgreSQL major version does harbor-api use?"
DATABASE_QUESTION = "What database does harbor-api claim to use?"


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


def _claim(
    claim_id: str,
    *,
    project_id: str,
    value: str,
    subject: str = "doc:harbor-api-datastore",
    field: str = "engine",
    source_id: str = "src-ds",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "claim_id": claim_id,
        "project_id": project_id,
        "subject": subject,
        "field": field,
        "value": value,
        "provenance": [{"ref": f"sources/{source_id}.md", "source_id": source_id}],
    }
    if extra:
        row.update(extra)
    return row


def _index_claims(vault: Path, claims: list[dict[str, Any]]) -> None:
    by_id = {str(row["claim_id"]): [str(row["claim_id"])] for row in claims}
    by_field: dict[str, list[str]] = {}
    by_project: dict[str, list[str]] = {}
    for row in claims:
        cid = str(row["claim_id"])
        field = str(row.get("field") or "engine")
        by_field.setdefault(field, []).append(cid)
        project = str(row.get("project_id") or "")
        if project:
            by_project.setdefault(project, []).append(cid)
    _wr(
        vault / "generated" / "indexes" / "claims.json",
        {
            "by_claim_id": by_id,
            "by_field": by_field,
            "by_concept_id": {},
            "by_source_lineage_id": {},
            "by_project_id": by_project,
        },
    )


def _claims_vault(
    tmp_path: Path,
    claims: list[dict[str, Any]],
    *,
    conflicts: list[dict[str, Any]] | None = None,
    portfolio: dict[str, str] | None = None,
    name: str = "vault",
) -> Path:
    vault = tmp_path / name
    vault.mkdir()
    _empty_indexes(vault)
    _index_claims(vault, claims)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in claims:
        grouped.setdefault(str(row["project_id"]), []).append(row)
    for project_id, rows in grouped.items():
        _wr(
            vault / "state" / "claims" / f"{project_id}.json",
            {"claims": rows},
        )
    if conflicts:
        _wr(vault / "review" / "conflicts" / "conflicts.json", {"entries": conflicts})
    if portfolio is not None:
        _wr(
            vault / "generated" / "portfolio" / "stale-knowledge.json",
            {
                "sources": [
                    {"source_id": sid, "freshness": label}
                    for sid, label in sorted(portfolio.items())
                ]
            },
        )
    return vault


def _ask(vault: Path, question: str, *, project_id: str = "harbor-api") -> dict[str, Any]:
    answer = ask_atlas_2(
        vault,
        question=question,
        project_id=project_id,
        kinds=("claim",),
        legacy_scan=False,
    )
    validate_record(answer, "ask-atlas-2-answer")
    return answer


def test_d178_attribute_fillers_and_project_tokens_stripped() -> None:
    terms = _question_claim_terms(HARBOR_DB_QUESTION, project_id="harbor-api")
    assert "postgresql" in terms
    assert terms.isdisjoint(_QUESTION_ATTRIBUTE_FILLERS)
    assert "harbor" not in terms
    assert "api" not in terms
    assert "use" not in terms
    assert "major" not in terms
    assert "version" not in terms


def test_d178_d150_leftover_nouns_remain_required() -> None:
    terms = _question_claim_terms("Helix annual revenue target")
    assert "helix" in terms
    assert "annual" in terms
    assert "revenue" in terms
    assert "target" in terms


def test_d178_single_grounded_claim_is_known(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [_claim("claim-pg16", project_id="harbor-api", value="PostgreSQL 16")],
    )
    answer = _ask(vault, HARBOR_DB_QUESTION)
    assert answer["status"] == "known"
    assert answer["UNKNOWN"]["is_unknown"] is False
    assert answer["evidence_count"] >= 1
    assert answer["CONFLICTS"]["unresolved_count"] == 0
    assert {e["record_id"] for e in answer["EVIDENCE"]} == {"claim-pg16"}


def test_d178_two_compatible_grounded_claims_are_known(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim("claim-pg16", project_id="harbor-api", value="PostgreSQL 16"),
            _claim(
                "claim-engine",
                project_id="harbor-api",
                value="PostgreSQL",
                field="datastore",
            ),
        ],
    )
    answer = _ask(vault, DATABASE_QUESTION)
    assert answer["status"] == "known"
    ids = {e["record_id"] for e in answer["EVIDENCE"]}
    assert "claim-pg16" in ids
    assert answer["CONFLICTS"]["unresolved_count"] == 0


def test_d178_two_conflicting_claims_are_conflict_aware(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim("claim-pg15", project_id="harbor-api", value="PostgreSQL 15"),
            _claim("claim-pg16", project_id="harbor-api", value="PostgreSQL 16"),
        ],
        conflicts=[
            {
                "conflict_id": "conflict-pg-major",
                "state": "unresolved",
                "claim_ids": ["claim-pg15", "claim-pg16"],
                "subject": "doc:harbor-api-datastore",
                "field": "engine",
            }
        ],
    )
    answer = _ask(vault, HARBOR_DB_QUESTION)
    assert answer["status"] == "conflict"
    assert answer["ANSWER"] is None
    assert answer["UNKNOWN"]["is_unknown"] is False
    assert answer["CONFLICTS"]["unresolved_count"] >= 1
    assert "conflict-pg-major" in answer["CONFLICTS"]["conflict_ids"]
    ids = {e["record_id"] for e in answer["EVIDENCE"]}
    assert {"claim-pg15", "claim-pg16"} <= ids


def test_d178_true_no_evidence_stays_unknown(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [_claim("claim-pg16", project_id="harbor-api", value="PostgreSQL 16")],
    )
    answer = _ask(vault, "Quasar nebula billing orbiter")
    assert answer["status"] == "unknown"
    assert answer["ANSWER"] is None
    assert answer["EVIDENCE"] == []
    assert answer["CONFLICTS"]["unresolved_count"] == 0
    assert answer["UNKNOWN"]["is_unknown"] is True
    assert "no-grounded-evidence" in answer["UNKNOWN"]["reasons"]


def test_d178_wrong_project_evidence_excluded(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim("claim-other", project_id="other-api", value="PostgreSQL 16"),
        ],
    )
    answer = _ask(vault, HARBOR_DB_QUESTION, project_id="harbor-api")
    assert answer["status"] == "unknown"
    assert answer["EVIDENCE"] == []
    assert answer["CONFLICTS"]["unresolved_count"] == 0
    assert all(entry["record_id"] != "claim-other" for entry in answer["EVIDENCE"])


def test_d178_secret_shaped_value_is_not_echoed(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "claim-secret",
                project_id="harbor-api",
                value=f"PostgreSQL 16 token {SECRET_TOKEN}",
            )
        ],
    )
    answer = _ask(vault, HARBOR_DB_QUESTION)
    dumped = json.dumps(answer, sort_keys=True)
    assert SECRET_TOKEN not in dumped
    assert SECRET_TOKEN not in json.dumps(answer.get("ANSWER"))
    assert answer["status"] in {"known", "unknown"}


def test_d178_stale_evidence_is_not_silently_current(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "claim-pg16",
                project_id="harbor-api",
                value="PostgreSQL 16",
                source_id="src-old",
            )
        ],
        portfolio={"src-old": "stale"},
    )
    answer = _ask(vault, HARBOR_DB_QUESTION)
    assert answer["status"] == "known"
    assert answer["FRESHNESS"]["aggregate"] == "stale"
    assert answer["FRESHNESS"]["stale_count"] >= 1
    assert answer["FRESHNESS"]["aggregate"] != "fresh"


def test_d178_forged_primary_authority_is_not_honored(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "claim-forged",
                project_id="harbor-api",
                value="PostgreSQL 16",
                extra={"authority": "primary", "authority_level": "primary"},
            )
        ],
    )
    answer = _ask(vault, HARBOR_DB_QUESTION)
    assert answer["status"] == "known"
    assert answer["AUTHORITY"]["level"] == "derived"
    assert answer["AUTHORITY"]["llm_authority"] is False
    for entry in answer["EVIDENCE"]:
        assert entry["authority_level"] != "primary"
        assert entry["authority_level"] in {"derived", "generated", "inferred", "conflicting"}


def test_d178_empty_vault_unknown(tmp_path: Path) -> None:
    vault = _claims_vault(tmp_path, [])
    answer = _ask(vault, HARBOR_DB_QUESTION)
    assert answer["status"] == "unknown"
    assert answer["ANSWER"] is None
    assert answer["CONFLICTS"]["unresolved_count"] == 0


def test_d178_version_control_use_keeps_relational_use_required(tmp_path: Path) -> None:
    """'Does Helix use version control?' must keep use* required (not version-scaffold)."""
    from project_atlas.ask2 import _question_claim_terms

    terms = _question_claim_terms("Does Helix use version control?", project_id="helix")
    assert "use" in terms or "uses" in terms
    assert "version" not in terms  # attribute filler
    assert "control" in terms


def test_d178_project_id_strip_keeps_substantive_api_token() -> None:
    """Exact project_id phrase strip must not drop standalone 'api' claim nouns."""
    from project_atlas.ask2 import _question_claim_terms

    terms = _question_claim_terms(
        "Which database does harbor-api use for the public api layer?",
        project_id="harbor-api",
    )
    assert "api" in terms
    assert "harbor" not in terms
    assert "database" in terms
