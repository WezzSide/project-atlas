"""AS-2.2-ASK2-001 — Ask Atlas 2 answer lens (integrated) unit tests.

Exercises the full reconciled pipeline against CURRENT main building blocks:

    QUESTION → project-scoped BM25/RRF hybrid retrieval → p2-readonly context
    compiler → answer contract (EVIDENCE / FRESHNESS / CONFLICT / UNKNOWN).

Covers the required matrix: known, unknown, conflict, stale, freshness-unknown,
cross-project (no leak), missing index, malformed provenance, graph-only
relation, and the legacy-compatibility subordinate path (dangerous-ambiguity
fix). Deterministic and read-only (NFR-001).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from project_atlas.ask2 import (
    PACKAGE_ID,
    Ask2Error,
    answer_to_json,
    ask_atlas_2,
)
from project_atlas.schema import available_schemas, validate_record


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


def _concept_vault(tmp_path: Path, *, project: str = "demo") -> Path:
    """Concept-only vault with well-formed provenance (source_lineage_id)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _empty_indexes(vault)
    _wr(
        vault / "generated" / "indexes" / "concepts.json",
        {
            "by_concept_id": {
                "alpha-auth": ["alpha-auth"],
                "beta-token": ["beta-token"],
                "gamma-auth-token": ["gamma-auth-token"],
            },
            "by_type": {"capability": ["alpha-auth", "beta-token", "gamma-auth-token"]},
            "by_project_id": {project: ["alpha-auth", "beta-token", "gamma-auth-token"]},
            "by_tag": {"auth": ["alpha-auth", "gamma-auth-token"]},
            "by_relationship_target": {},
        },
    )
    _wr(
        vault / "state" / "concepts" / f"{project}.json",
        {
            "concepts": [
                {
                    "concept_id": "alpha-auth",
                    "type": "capability",
                    "project_id": project,
                    "summary": "authentication gate policy",
                    "provenance": [{"source_lineage_id": "sline-a"}],
                },
                {
                    "concept_id": "beta-token",
                    "type": "capability",
                    "project_id": project,
                    "summary": "bearer token issuance",
                    "provenance": [{"source_lineage_id": "sline-b"}],
                },
                {
                    "concept_id": "gamma-auth-token",
                    "type": "capability",
                    "project_id": project,
                    "summary": "auth token rotation policy",
                    "provenance": [{"source_lineage_id": "sline-c"}],
                },
            ]
        },
    )
    return vault


def _claims_vault(
    tmp_path: Path,
    *,
    project: str = "demo",
    with_conflict: bool = False,
    portfolio: dict[str, str] | None = None,
) -> Path:
    """Claim-only vault; optional unresolved conflict + portfolio freshness."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _empty_indexes(vault)
    _wr(
        vault / "generated" / "indexes" / "claims.json",
        {
            "by_claim_id": {
                "claim-alpha": ["claim-alpha"],
                "claim-beta": ["claim-beta"],
            },
            "by_field": {"status": ["claim-alpha"], "owner": ["claim-beta"]},
            "by_concept_id": {},
            "by_source_lineage_id": {},
        },
    )
    _wr(
        vault / "state" / "claims" / "claims.json",
        {
            "claims": [
                {
                    "claim_id": "claim-alpha",
                    "field": "status",
                    "project_id": project,
                    "provenance": [{"ref": "sources/a.md"}],
                },
                {
                    "claim_id": "claim-beta",
                    "field": "owner",
                    "project_id": project,
                    "provenance": [{"ref": "sources/b.md"}],
                },
            ]
        },
    )
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
    if with_conflict:
        _wr(
            vault / "review" / "conflicts" / "conflicts.json",
            {
                "entries": [
                    {
                        "conflict_id": "conflict-alpha-status",
                        "state": "unresolved",
                        "claim_ids": ["claim-alpha"],
                        "subject": "project",
                        "field": "status",
                    }
                ]
            },
        )
    return vault


def _grounding_distractor_vault(tmp_path: Path) -> Path:
    """Corpus with nearby records that must not answer absent-subject questions."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _empty_indexes(vault)
    records = [
        (
            "atlas-revenue-forecast",
            "Atlas annual revenue forecast is maintained by finance.",
        ),
        (
            "atlas-billing-api",
            "Atlas Billing API service supports invoice retrieval.",
        ),
        (
            "atlas-ledger-product",
            "Atlas Ledger product manages invoice reconciliation.",
        ),
        (
            "morgan-forecast",
            "Morgan Lee owns the Atlas revenue forecast.",
        ),
        (
            "meridian-api",
            "Meridian Billing API service supports invoice retrieval.",
        ),
    ]
    _wr(
        vault / "generated" / "indexes" / "concepts.json",
        {
            "by_concept_id": {record_id: [record_id] for record_id, _ in records},
            "by_type": {"capability": [record_id for record_id, _ in records]},
            "by_project_id": {"demo": [record_id for record_id, _ in records]},
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
                for record_id, summary in records
            ]
        },
    )
    return vault


def _fingerprint(vault: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(vault).as_posix(): (
            path.read_bytes(),
            os.stat(path).st_mtime_ns,
        )
        for path in vault.rglob("*")
        if path.is_file()
    }


# --------------------------------------------------------------------------- #
# schema / invariants
# --------------------------------------------------------------------------- #


def test_ask2_schema_registered() -> None:
    assert "ask-atlas-2-answer" in available_schemas()


def test_ask2_requires_project_scope_structurally(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    with pytest.raises(Ask2Error, match="project-scope-required"):
        ask_atlas_2(vault, question="auth token", project_id="   ")


def test_ask2_rejects_empty_question(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    with pytest.raises(Ask2Error, match="question-empty"):
        ask_atlas_2(vault, question="   ", project_id="demo")


# --------------------------------------------------------------------------- #
# known
# --------------------------------------------------------------------------- #


def test_ask2_known_grounded_answer(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    answer = ask_atlas_2(vault, question="auth token", project_id="demo", kinds=("concept",))
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["package_id"] == PACKAGE_ID
    assert answer["status"] == "known"
    assert answer["evidence_count"] >= 1
    assert answer["UNKNOWN"]["is_unknown"] is False
    # Authority is stamped by Core, never elevated to primary / llm / graph.
    assert answer["AUTHORITY"]["level"] == "derived"
    assert answer["AUTHORITY"]["source"] == "core-context-compiler"
    assert answer["ANSWER"] is None
    assert answer["llm_authority"] is False
    assert answer["graph_authority"] is False
    assert answer["canonical_write"] is False
    assert answer["ui_truth"] is False
    ids = {e["record_id"] for e in answer["EVIDENCE"]}
    assert "gamma-auth-token" in ids
    for entry in answer["EVIDENCE"]:
        assert entry["authority_level"] in {"derived", "generated", "inferred"}
        assert entry["authority_level"] != "primary"
        assert entry["provenance"]  # grounded evidence always carries provenance
    assert answer["retrieval"]["project_scoped"] is True
    assert answer["retrieval"]["semantic_enabled"] is False
    assert answer["context"]["profile_id"] == "p2-readonly"


def test_ask2_read_only_no_writes(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    before = _fingerprint(vault)
    ask_atlas_2(vault, question="auth token", project_id="demo", kinds=("concept",))
    assert _fingerprint(vault) == before


def test_ask2_deterministic_repeat_byte_identical(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    a = answer_to_json(ask_atlas_2(vault, question="auth", project_id="demo", kinds=("concept",)))
    b = answer_to_json(ask_atlas_2(vault, question="auth", project_id="demo", kinds=("concept",)))
    assert a == b


# --------------------------------------------------------------------------- #
# unknown
# --------------------------------------------------------------------------- #


def test_ask2_unknown_when_no_grounded_evidence(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    answer = ask_atlas_2(
        vault,
        question="zzz-nonexistent-subject",
        project_id="demo",
        kinds=("concept",),
        legacy_scan=False,
    )
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["status"] == "unknown"
    assert answer["UNKNOWN"]["is_unknown"] is True
    assert "no-grounded-evidence" in answer["UNKNOWN"]["reasons"]
    assert answer["EVIDENCE"] == []
    assert answer["evidence_count"] == 0
    assert answer["retrieval"]["candidate_count"] == 0


@pytest.mark.parametrize(
    "question",
    [
        "Nebula annual revenue forecast",
        "Quasar Billing API service",
        "Orbit Ledger product",
        "Morgan Lee revenue target",
        "Atlas annual revenue target",
    ],
    ids=[
        "absent-financial-claim",
        "absent-api-service",
        "absent-product",
        "semantic-near-distractor",
        "lexical-overlap-distractor",
    ],
)
def test_ask2_nearby_retrieval_without_claim_support_stays_unknown(
    tmp_path: Path, question: str
) -> None:
    """Ranked text is not evidence unless it supports every asserted term."""
    vault = _grounding_distractor_vault(tmp_path)
    answer = ask_atlas_2(
        vault,
        question=question,
        project_id="demo",
        kinds=("concept",),
        legacy_scan=False,
    )
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["retrieval"]["result_count"] > 0
    assert answer["retrieval"]["candidate_count"] == 0
    assert answer["status"] == "unknown"
    assert answer["EVIDENCE"] == []
    assert answer["UNKNOWN"]["is_unknown"] is True
    assert "retrieval-hits-lack-claim-support" in answer["UNKNOWN"]["reasons"]


def test_ask2_explicitly_supported_claim_remains_known(tmp_path: Path) -> None:
    vault = _grounding_distractor_vault(tmp_path)
    answer = ask_atlas_2(
        vault,
        question="Atlas annual revenue forecast",
        project_id="demo",
        kinds=("concept",),
        legacy_scan=False,
    )
    assert answer["status"] == "known"
    assert {entry["record_id"] for entry in answer["EVIDENCE"]} == {"atlas-revenue-forecast"}


# --------------------------------------------------------------------------- #
# conflict
# --------------------------------------------------------------------------- #


def test_ask2_conflict_status_and_sidecar(tmp_path: Path) -> None:
    vault = _claims_vault(tmp_path, with_conflict=True, portfolio={"a": "stale", "b": "fresh"})
    answer = ask_atlas_2(vault, question="status", project_id="demo", kinds=("claim",))
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["status"] == "conflict"
    assert answer["CONFLICTS"]["unresolved_count"] >= 1
    assert "conflict-alpha-status" in answer["CONFLICTS"]["conflict_ids"]
    assert answer["CONFLICTS"]["retained_as_sidecars"] is True
    alpha = next(e for e in answer["EVIDENCE"] if e["record_id"] == "claim-alpha")
    # Conflict state is preserved; authority is Core "conflicting" (no silent winner).
    assert alpha["conflict_state"] == "unresolved"
    assert alpha["authority_level"] == "conflicting"
    assert alpha["conflict_ids"] == ["conflict-alpha-status"]


# --------------------------------------------------------------------------- #
# stale / freshness-unknown
# --------------------------------------------------------------------------- #


def test_ask2_stale_freshness_preserved(tmp_path: Path) -> None:
    vault = _claims_vault(tmp_path, portfolio={"b": "stale"})
    answer = ask_atlas_2(vault, question="owner", project_id="demo", kinds=("claim",))
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["status"] == "known"
    assert answer["FRESHNESS"]["aggregate"] == "stale"
    assert answer["FRESHNESS"]["stale_count"] >= 1
    beta = next(e for e in answer["EVIDENCE"] if e["record_id"] == "claim-beta")
    assert beta["freshness"] == "stale"


def test_ask2_freshness_unknown_never_invented(tmp_path: Path) -> None:
    # No portfolio corroboration → freshness unknown (never invented fresh).
    vault = _claims_vault(tmp_path, portfolio=None)
    answer = ask_atlas_2(vault, question="owner", project_id="demo", kinds=("claim",))
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["FRESHNESS"]["aggregate"] == "unknown"
    assert answer["FRESHNESS"]["unknown_count"] >= 1
    beta = next(e for e in answer["EVIDENCE"] if e["record_id"] == "claim-beta")
    assert beta["freshness"] == "unknown"


# --------------------------------------------------------------------------- #
# cross-project (must not leak)
# --------------------------------------------------------------------------- #


def _two_project_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _empty_indexes(vault)
    _wr(
        vault / "generated" / "indexes" / "concepts.json",
        {
            "by_concept_id": {
                "a-auth-gate": ["a-auth-gate"],
                "a-other": ["a-other"],
                "b-auth-gate": ["b-auth-gate"],
                "b-other": ["b-other"],
            },
            "by_type": {},
            "by_project_id": {
                "PROJECT_A": ["a-auth-gate", "a-other"],
                "PROJECT_B": ["b-auth-gate", "b-other"],
            },
            "by_tag": {},
            "by_relationship_target": {},
        },
    )
    _wr(
        vault / "state" / "concepts" / "PROJECT_A.json",
        {
            "concepts": [
                {
                    "concept_id": "a-auth-gate",
                    "type": "capability",
                    "project_id": "PROJECT_A",
                    "summary": "sharedmarker authentication gate",
                    "provenance": [{"source_lineage_id": "sline-a1"}],
                },
                {
                    "concept_id": "a-other",
                    "type": "capability",
                    "project_id": "PROJECT_A",
                    "summary": "project a auxiliary",
                    "provenance": [{"source_lineage_id": "sline-a2"}],
                },
            ]
        },
    )
    _wr(
        vault / "state" / "concepts" / "PROJECT_B.json",
        {
            "concepts": [
                {
                    "concept_id": "b-auth-gate",
                    "type": "capability",
                    "project_id": "PROJECT_B",
                    "summary": "sharedmarker authentication gate",
                    "provenance": [{"source_lineage_id": "sline-b1"}],
                },
                {
                    "concept_id": "b-other",
                    "type": "capability",
                    "project_id": "PROJECT_B",
                    "summary": "project b zebrawidget auxiliary",
                    "provenance": [{"source_lineage_id": "sline-b2"}],
                },
            ]
        },
    )
    return vault


def test_ask2_cross_project_does_not_leak(tmp_path: Path) -> None:
    vault = _two_project_vault(tmp_path)
    shared = ask_atlas_2(
        vault,
        question="sharedmarker authentication",
        project_id="PROJECT_A",
        kinds=("concept",),
        legacy_scan=True,
    )
    blob = json.dumps(shared)
    assert "b-auth-gate" not in blob
    assert "b-other" not in blob
    ids = {e["record_id"] for e in shared["EVIDENCE"]}
    assert "a-auth-gate" in ids

    # A token unique to PROJECT_B must never ground a PROJECT_A answer.
    isolated = ask_atlas_2(
        vault, question="zebrawidget", project_id="PROJECT_A", kinds=("concept",)
    )
    assert isolated["status"] == "unknown"
    assert "b-other" not in json.dumps(isolated["EVIDENCE"])
    assert isolated["retrieval"]["candidate_count"] == 0


# --------------------------------------------------------------------------- #
# missing index (fail closed)
# --------------------------------------------------------------------------- #


def test_ask2_missing_index_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Ask2Error, match="retrieval-substrate"):
        ask_atlas_2(empty, question="auth", project_id="demo", kinds=("concept",))


# --------------------------------------------------------------------------- #
# malformed provenance (fail closed)
# --------------------------------------------------------------------------- #


def test_ask2_malformed_provenance_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _empty_indexes(vault)
    _wr(
        vault / "generated" / "indexes" / "concepts.json",
        {
            "by_concept_id": {"orphan-concept": ["orphan-concept"]},
            "by_type": {},
            "by_project_id": {"demo": ["orphan-concept"]},
            "by_tag": {},
            "by_relationship_target": {},
        },
    )
    _wr(
        vault / "state" / "concepts" / "demo.json",
        {
            "concepts": [
                {
                    "concept_id": "orphan-concept",
                    "type": "capability",
                    "project_id": "demo",
                    "summary": "orphan record",
                    # Provenance element carries no usable ref → sanitizes to empty.
                    "provenance": [{"note": "no-ref-field"}],
                }
            ]
        },
    )
    with pytest.raises(Ask2Error, match="context-compiler"):
        ask_atlas_2(vault, question="orphan", project_id="demo", kinds=("concept",))


# --------------------------------------------------------------------------- #
# graph-only relation (graph ≠ authority)
# --------------------------------------------------------------------------- #


def test_ask2_graph_only_relation_not_authority(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    # A derived impact-graph projection references a ghost node absent from the
    # lexical indexes / state. It must never surface as grounded evidence and
    # must never confer authority.
    _wr(
        vault / "generated" / "indexes" / "impact-graph.json",
        {
            "nodes": [{"id": "gamma-auth-token"}, {"id": "ghost-graph-node"}],
            "edges": [{"src": "gamma-auth-token", "dst": "ghost-graph-node"}],
        },
    )
    answer = ask_atlas_2(vault, question="auth token", project_id="demo", kinds=("concept",))
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["graph_authority"] is False
    assert answer["AUTHORITY"]["graph_authority"] is False
    ids = {e["record_id"] for e in answer["EVIDENCE"]}
    assert "ghost-graph-node" not in ids
    # No graph relationship elevates an entry above Core-derived authority.
    for entry in answer["EVIDENCE"]:
        assert entry["authority_level"] != "primary"


# --------------------------------------------------------------------------- #
# legacy-compatibility subordinate (dangerous-ambiguity fix)
# --------------------------------------------------------------------------- #


def test_ask2_legacy_unknown_stays_unknown(tmp_path: Path) -> None:
    """Grounded UNKNOWN + legacy matches must NOT masquerade as grounded."""
    vault = _concept_vault(tmp_path)
    answer = ask_atlas_2(
        vault,
        question="zzz-nonexistent-subject",
        project_id="demo",
        kinds=("concept",),
        legacy_scan=False,
        legacy_matches=[{"record_type": "project", "record_id": "demo-portfolio"}],
    )
    validate_record(answer, "ask-atlas-2-answer")
    # Legacy match is present but strictly subordinate; status stays UNKNOWN.
    assert answer["status"] == "unknown"
    assert answer["UNKNOWN"]["is_unknown"] is True
    assert answer["EVIDENCE"] == []
    legacy = answer["legacy_compatibility"]
    assert legacy["authoritative"] is False
    assert legacy["subordinate"] is True
    assert legacy["match_count"] == 1
    assert legacy["matches"][0]["record_id"] == "demo-portfolio"
    assert legacy["matches"][0]["source"] == "external"


def test_ask2_legacy_matches_subordinate_when_known(tmp_path: Path) -> None:
    vault = _concept_vault(tmp_path)
    answer = ask_atlas_2(
        vault,
        question="auth token",
        project_id="demo",
        kinds=("concept",),
        legacy_scan=True,
    )
    validate_record(answer, "ask-atlas-2-answer")
    assert answer["status"] == "known"
    legacy = answer["legacy_compatibility"]
    assert legacy["authoritative"] is False
    assert legacy["subordinate"] is True
    assert legacy["match_count"] >= 1
    # Legacy is a non-authoritative substring surface; authority still Core.
    assert answer["AUTHORITY"]["source"] == "core-context-compiler"
    assert all(m["source"] == "legacy-substring" for m in legacy["matches"])
