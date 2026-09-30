"""D-181 — Ask2 precision adversarial matrix (claim-to-use scaffolding).

Global stop-word deletion of ``use*`` / ``claim*`` is forbidden. Scaffolding is
stripped only inside closed ``claim* … to use*`` phrases. Relational ``use``
must remain required; entity co-occurrence ≠ relational support.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_atlas.ask2 import (
    _QUESTION_FUNCTION_WORDS,
    _claim_to_use_scaffold_tokens,
    _question_claim_terms,
    ask_atlas_2,
)
from project_atlas.schema import validate_record

SECRET_TOKEN = "AKIAIOSFODNN7EXAMPLE"


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
    subject: str = "doc:subject",
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
        _wr(vault / "state" / "claims" / f"{project_id}.json", {"claims": rows})
    if conflicts:
        _wr(vault / "review" / "conflicts" / "conflicts.json", {"entries": conflicts})
    return vault


def _ask(vault: Path, question: str, *, project_id: str) -> dict[str, Any]:
    answer = ask_atlas_2(
        vault,
        question=question,
        project_id=project_id,
        kinds=("claim",),
        legacy_scan=False,
    )
    validate_record(answer, "ask-atlas-2-answer")
    return answer


def test_d181_invariants_claim_use_not_global_function_words() -> None:
    for word in ("claim", "claims", "claiming", "claimed", "use", "uses", "using", "used"):
        assert word not in _QUESTION_FUNCTION_WORDS
    scaffold = _claim_to_use_scaffold_tokens("What database is Harbor claimed to use?")
    assert "claimed" in scaffold
    assert "use" in scaffold
    bare = _claim_to_use_scaffold_tokens("Does Helix use PostgreSQL?")
    assert bare == frozenset()
    assert "use" in _question_claim_terms("Does Helix use PostgreSQL?")


def test_d181_a_grounded_positive(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "harbor-pg",
                project_id="harbor",
                value="Harbor uses PostgreSQL.",
                subject="doc:harbor",
            )
        ],
    )
    answer = _ask(vault, "What database does Harbor use?", project_id="harbor")
    assert answer["status"] == "known"
    assert answer["UNKNOWN"]["is_unknown"] is False
    assert answer["retrieval"]["candidate_count"] >= 1


def test_d181_b_meta_claim_construction(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "harbor-db",
                project_id="harbor",
                value="Harbor database: PostgreSQL.",
                subject="doc:harbor",
            )
        ],
    )
    answer = _ask(vault, "What database does Harbor claim to use?", project_id="harbor")
    assert answer["status"] == "known"
    assert "claim" not in _question_claim_terms("What database does Harbor claim to use?")
    assert "use" not in _question_claim_terms("What database does Harbor claim to use?")


def test_d181_c_past_meta_claimed_to_use(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "harbor-db",
                project_id="harbor",
                value="Harbor database: PostgreSQL.",
                subject="doc:harbor",
            )
        ],
    )
    q = "What database is Harbor claimed to use?"
    assert "claimed" not in _question_claim_terms(q)
    assert "use" not in _question_claim_terms(q)
    answer = _ask(vault, q, project_id="harbor")
    assert answer["status"] == "known"


def test_d181_d_negative_relation_rejected_not_supported(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "helix-reject",
                project_id="helix",
                value="Helix evaluated PostgreSQL and rejected it.",
                subject="doc:helix",
            )
        ],
    )
    answer = _ask(vault, "Does Helix use PostgreSQL?", project_id="helix")
    assert answer["status"] == "unknown"
    assert answer["ANSWER"] is None
    assert answer["retrieval"]["candidate_count"] == 0
    assert answer["UNKNOWN"]["is_unknown"] is True


def test_d181_e_explicit_negation_not_positive(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "helix-neg",
                project_id="helix",
                value="Helix does not use PostgreSQL.",
                subject="doc:helix",
            )
        ],
    )
    answer = _ask(vault, "Does Helix use PostgreSQL?", project_id="helix")
    assert answer["status"] != "known"
    assert answer["status"] == "unknown"
    assert answer["retrieval"]["candidate_count"] == 0


def test_d181_f_historical_use_not_current(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "helix-hist",
                project_id="helix",
                value="Helix used PostgreSQL in 2022 but migrated to SQLite.",
                subject="doc:helix",
            )
        ],
    )
    answer = _ask(vault, "Does Helix use PostgreSQL?", project_id="helix")
    assert answer["status"] == "unknown"
    assert answer["retrieval"]["candidate_count"] == 0


def test_d181_g_conflicted_current_claims(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "claim-pg15",
                project_id="project-a",
                value="project-a database PostgreSQL 15",
                subject="doc:project-a-db",
            ),
            _claim(
                "claim-pg16",
                project_id="project-a",
                value="project-a database PostgreSQL 16",
                subject="doc:project-a-db",
            ),
        ],
        conflicts=[
            {
                "conflict_id": "conflict-pg-major",
                "state": "unresolved",
                "claim_ids": ["claim-pg15", "claim-pg16"],
                "subject": "doc:project-a-db",
                "field": "engine",
            }
        ],
    )
    answer = _ask(
        vault,
        "What database does project-a claim to use?",
        project_id="project-a",
    )
    assert answer["status"] == "conflict"
    assert answer["ANSWER"] is None
    assert answer["CONFLICTS"]["unresolved_count"] >= 1
    assert answer["UNKNOWN"]["is_unknown"] is False


def test_d181_h_true_no_evidence(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "harbor-pg",
                project_id="harbor",
                value="Harbor uses PostgreSQL.",
                subject="doc:harbor",
            )
        ],
    )
    answer = _ask(vault, "Quasar nebula billing orbiter", project_id="harbor")
    assert answer["status"] == "unknown"
    assert answer["ANSWER"] is None
    assert answer["EVIDENCE"] == []
    assert answer["CONFLICTS"]["unresolved_count"] == 0


def test_d181_i_wrong_project_excluded(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "other-pg",
                project_id="other",
                value="Harbor uses PostgreSQL.",
                subject="doc:other",
            )
        ],
    )
    answer = _ask(vault, "What database does Harbor use?", project_id="harbor")
    assert answer["status"] == "unknown"
    assert answer["EVIDENCE"] == []


def test_d181_j_d150_distractor_stays_unknown(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "helix-rev",
                project_id="demo",
                value="Helix annual revenue forecast is maintained by finance ops.",
                subject="doc:helix-rev",
                field="forecast",
            )
        ],
    )
    answer = _ask(
        vault,
        "What is the Helix quarterly margin target?",
        project_id="demo",
    )
    assert answer["status"] == "unknown"


def test_d181_k_secret_shaped_value_not_echoed(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "harbor-secret",
                project_id="harbor",
                value=f"Harbor uses PostgreSQL token {SECRET_TOKEN}",
                subject="doc:harbor",
            )
        ],
    )
    answer = _ask(vault, "What database does Harbor use?", project_id="harbor")
    dumped = json.dumps(answer, sort_keys=True)
    assert SECRET_TOKEN not in dumped
    assert SECRET_TOKEN not in json.dumps(answer.get("ANSWER"))


def test_d181_l_forged_authority_not_promoted(tmp_path: Path) -> None:
    vault = _claims_vault(
        tmp_path,
        [
            _claim(
                "harbor-forged",
                project_id="harbor",
                value="Harbor uses PostgreSQL.",
                subject="doc:harbor",
                extra={"authority": "primary", "authority_level": "primary"},
            )
        ],
    )
    answer = _ask(vault, "What database does Harbor use?", project_id="harbor")
    assert answer["status"] == "known"
    assert answer["AUTHORITY"]["level"] == "derived"
    assert answer["AUTHORITY"]["llm_authority"] is False
    for entry in answer["EVIDENCE"]:
        assert entry["authority_level"] != "primary"
