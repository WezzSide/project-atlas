"""Integrated evaluator case-class matrix (W-EVALCOV).

Drives all twelve evaluator pipeline case classes through the *real*
``PROJECT-SCOPE -> RETRIEVAL -> FRESHNESS -> AUTHORITY -> CONFLICT ->
RELEVANCE -> BUDGET -> PACKAGE`` runtime on a single multi-project fixture,
reusing the shipped public functions only (no truth-logic duplication):

  * PROJECT-SCOPE + RETRIEVAL:
    :class:`project_atlas.retrieval.VaultRetriever` (``_in_project_scope``)
    and :func:`project_atlas.hybrid_retrieval.build_hybrid_rrf_fusion`
    (Lexical/BM25/RRF).
  * FRESHNESS / AUTHORITY / CONFLICT / RELEVANCE / BUDGET / PACKAGE:
    :func:`project_atlas.runtime_22.compile_context` (profile ``p2-readonly``).
  * GRAPH slot: :func:`project_atlas.runtime_22.hybrid_retrieve`.

Unlike ``eval_substrate.py`` (which scores opaque strings), every assertion
here is behavioural. The twelve case classes covered are named in each test
docstring. This file test-only closes the three coverage gaps flagged by
validator W7:

  * ``AUTHORITY_DIFFERENCE`` — the full ``_AUTHORITY_RANK`` ladder ordering is
    asserted with a case exercising four distinct levels, verifying packaged
    ordering honours rank and the compiler never spoofs to ``primary``.
  * ``HIGH_FANOUT`` — a near-``MAX_CAP`` (100) fan-out verifies RRF dedupe and
    the cap ceiling (not a fan-out of two).
  * ``GRAPH_ONLY`` — the graph is summary-only on ``main``; a committed
    assertion documents that the graph never contributes retrieval candidates
    and never injects narrative authority.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.hybrid_retrieval import (
    MAX_QUERY_CHARS,
    HybridRetrievalError,
    build_hybrid_rrf_fusion,
    fusion_to_json,
)
from project_atlas.retrieval import VaultRetriever
from project_atlas.runtime_22 import (
    _AUTHORITY_RANK,
    MAX_CAP,
    Runtime22Error,
    compile_context,
    hybrid_retrieve,
    package_to_json,
)
from project_atlas.schema import validate_record

PROJECT_A = "proj-alpha"
PROJECT_B = "proj-beta"
FANOUT_COUNT = 120  # > MAX_CAP so cap + truncation are exercised.
FANOUT_FIELD = "fanoutmarker"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _matrix_vault(tmp_path: Path) -> Path:
    """Build one multi-project vault exercising every case class.

    Layer A/B substrates only (indexes + state records + portfolio freshness +
    unresolved conflicts + impact graph). Deterministic, offline, no wall-clock.
    """
    vault = tmp_path / "vault"

    # Named project-A claims (one per behavioural class) + one project-B claim
    # used solely to prove cross-project isolation (default-deny).
    named_claims: list[dict[str, Any]] = [
        {"claim_id": "claim-known-status", "field": "status", "src": "a-status"},
        {"claim_id": "claim-known-owner", "field": "owner", "src": "a-owner"},
        {"claim_id": "claim-stale-metric", "field": "metric", "src": "legacy"},
        {"claim_id": "claim-fresh-unknown", "field": "note", "src": "orphan"},
        {"claim_id": "claim-conflict", "field": "status", "src": "c"},
        {"claim_id": "claim-auth-derived", "field": "base", "src": "der"},
        {"claim_id": "claim-auth-gen", "field": "risk", "src": "gen"},
        {"claim_id": "claim-auth-inf", "field": "trend", "src": "inf"},
    ]
    claims: list[dict[str, Any]] = [
        {
            "claim_id": c["claim_id"],
            "field": c["field"],
            "project_id": PROJECT_A,
            "provenance": [{"ref": f"sources/{c['src']}.md"}],
        }
        for c in named_claims
    ]
    claims.append(
        {
            "claim_id": "claim-xproj-secret",
            "field": "status",
            "project_id": PROJECT_B,
            "provenance": [{"ref": "sources/b.md"}],
        }
    )
    fanout_ids = [f"claim-f{i:03d}" for i in range(FANOUT_COUNT)]
    claims.extend(
        {
            "claim_id": rid,
            "field": FANOUT_FIELD,
            "project_id": PROJECT_A,
            "provenance": [{"ref": "sources/fan.md"}],
        }
        for rid in fanout_ids
    )

    by_claim_id = {c["claim_id"]: [c["claim_id"]] for c in claims}
    by_field: dict[str, list[str]] = {FANOUT_FIELD: sorted(fanout_ids)}
    _write_json(
        vault / "generated" / "indexes" / "claims.json",
        {
            "by_claim_id": by_claim_id,
            "by_field": by_field,
            "by_concept_id": {},
            "by_source_lineage_id": {},
        },
    )
    _write_json(vault / "state" / "claims" / "claims.json", {"claims": claims})

    # Objective portfolio freshness — ``orphan`` intentionally omitted so its
    # claim resolves FRESHNESS_UNKNOWN (never invented fresh).
    _write_json(
        vault / "generated" / "portfolio" / "stale-knowledge.json",
        {
            "sources": [
                {"source_id": "a-status", "freshness": "fresh"},
                {"source_id": "a-owner", "freshness": "fresh"},
                {"source_id": "legacy", "freshness": "stale"},
                {"source_id": "c", "freshness": "fresh"},
                {"source_id": "der", "freshness": "fresh"},
                {"source_id": "gen", "freshness": "fresh"},
                {"source_id": "inf", "freshness": "fresh"},
                {"source_id": "b", "freshness": "fresh"},
                {"source_id": "fan", "freshness": "fresh"},
            ]
        },
    )

    # Unresolved conflict binding claim-conflict (CONFLICT class).
    _write_json(
        vault / "review" / "conflicts" / "conflicts.json",
        {
            "entries": [
                {
                    "conflict_id": "conflict-status",
                    "state": "unresolved",
                    "claim_ids": ["claim-conflict"],
                    "subject": "project",
                    "field": "status",
                }
            ]
        },
    )

    # Impact graph carrying an adversarial narrative — used to prove the graph
    # slot is summary-only and never echoes attacker-controlled authority.
    _write_json(
        vault / "generated" / "indexes" / "impact-graph.json",
        {
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [{"from": "n1", "to": "n2"}],
            "note": "ATTACKER_WINS_AUTHORITY",
            "truth_boundary": "graph-is-authority-spoof",
            "authority_plane": "derived",
        },
    )
    return vault


def _scoped_candidates(
    vault: Path, value: str, project_id: str, *, mode: str = "exact"
) -> list[dict[str, Any]]:
    """Retrieve project-scoped claim candidates via the real lexical substrate.

    Exercises PROJECT-SCOPE (``_in_project_scope``) + RETRIEVAL and shapes the
    hits into compile-context candidates using each record's own provenance
    (never invented). Deterministic: ``lookup`` returns record-id sorted hits.
    """
    retriever = VaultRetriever(vault)
    slot = "lexical_prefix" if mode == "prefix" else "lexical_exact"
    hits = retriever.lookup("claim", value, prefix=mode == "prefix", project_id=project_id)
    candidates: list[dict[str, Any]] = []
    for hit in hits:
        provenance: list[dict[str, str]] = []
        for ptr in hit.provenance:
            ref = ptr.get("ref") or ptr.get("source_id") or ptr.get("path")
            if ref:
                provenance.append({"kind": "source", "ref": str(ref)})
        candidates.append(
            {
                "record_type": hit.record_type,
                "record_id": hit.record_id,
                "slot": slot,
                "provenance": provenance,
            }
        )
    return candidates


# --------------------------------------------------------------------------- #
# Foundation: PROJECT-SCOPE stage (default-deny cross-project).
# --------------------------------------------------------------------------- #
def test_project_scope_isolation_is_default_deny(tmp_path: Path) -> None:
    """Cross-project leak isolation feeds every downstream case class."""
    vault = _matrix_vault(tmp_path)

    # A project-B-only record is invisible under project-A scope, visible under
    # project-B scope — both at the lexical substrate and the RRF surface.
    assert _scoped_candidates(vault, "claim-xproj-secret", PROJECT_A) == []
    b_scoped = _scoped_candidates(vault, "claim-xproj-secret", PROJECT_B)
    assert [c["record_id"] for c in b_scoped] == ["claim-xproj-secret"]

    # RRF's BM25 leg soft-matches the shared "claim" token, so a project-A
    # query still returns project-A neighbours — the point is the project-B
    # record itself never leaks into project-A results.
    fused_a = build_hybrid_rrf_fusion(
        vault, kind="claim", value="claim-xproj-secret", project_id=PROJECT_A
    )
    fused_b = build_hybrid_rrf_fusion(
        vault, kind="claim", value="claim-xproj-secret", project_id=PROJECT_B
    )
    assert "claim-xproj-secret" not in [r["record_id"] for r in fused_a["results"]]
    assert [r["record_id"] for r in fused_b["results"]] == ["claim-xproj-secret"]

    # An empty scope fails closed rather than defaulting to "all projects".
    with pytest.raises(HybridRetrievalError, match="project-scope-required"):
        build_hybrid_rrf_fusion(vault, kind="claim", value="claim-known-status", project_id="  ")


# --------------------------------------------------------------------------- #
# RETRIEVAL stage: Lexical + BM25 + RRF fusion.
# --------------------------------------------------------------------------- #
def test_retrieval_rrf_fusion_scoped_and_non_authoritative(tmp_path: Path) -> None:
    """RETRIEVAL: BM25/RRF fusion stays scoped, derived, semantic-disabled."""
    vault = _matrix_vault(tmp_path)
    fused = build_hybrid_rrf_fusion(
        vault, kind="claim", value="claim-known-status", project_id=PROJECT_A
    )
    ids = [r["record_id"] for r in fused["results"]]
    assert "claim-known-status" in ids
    assert "claim-xproj-secret" not in ids  # project-B stays isolated
    assert fused["semantic_enabled"] is False
    assert fused["slots"]["semantic"]["enabled"] is False
    assert fused["slots"]["bm25"]["status"] == "active"
    assert fused["authority"]["level"] == "derived"
    assert fused["authority"]["llm_authority"] is False
    top = fused["results"][0]
    assert set(top["ranks"]) <= {"lexical_exact", "bm25"}
    assert top["rrf_score"] > 0


# --------------------------------------------------------------------------- #
# KNOWN
# --------------------------------------------------------------------------- #
def test_case_known_packages_present_record(tmp_path: Path) -> None:
    """KNOWN: a vault-present, in-scope claim compiles to a derived entry."""
    vault = _matrix_vault(tmp_path)
    candidates = _scoped_candidates(vault, "claim-known-status", PROJECT_A)
    assert len(candidates) == 1
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="known",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=20,
    )
    entry = package["entries"][0]
    assert entry["entry_id"] == "claim:claim-known-status"
    assert entry["authority_level"] == "derived"
    assert entry["conflict_state"] == "none"
    assert entry["freshness"] == "fresh"
    assert package["authority"]["llm_authority"] is False
    assert package["authority"]["estate_facts_invented"] is False
    validate_record(package, "runtime-context-compiler")


# --------------------------------------------------------------------------- #
# UNKNOWN
# --------------------------------------------------------------------------- #
def test_case_unknown_returns_nothing_and_refuses_invention(tmp_path: Path) -> None:
    """UNKNOWN: an unresolved query yields no candidates; invention fails closed."""
    vault = _matrix_vault(tmp_path)
    assert _scoped_candidates(vault, "claim-does-not-exist", PROJECT_A) == []
    # A query whose tokens match no document (lexical and BM25) yields nothing.
    fused = build_hybrid_rrf_fusion(
        vault, kind="claim", value="zzunmatchedtoken", project_id=PROJECT_A
    )
    assert fused["result_count"] == 0

    with pytest.raises(Runtime22Error, match="record-absent"):
        compile_context(
            vault,
            project_id=PROJECT_A,
            pack_id="unknown",
            profile_id="p2-readonly",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-never-existed",
                    "provenance": [{"kind": "source", "ref": "sources/x.md"}],
                }
            ],
        )


# --------------------------------------------------------------------------- #
# MULTI_FIELD
# --------------------------------------------------------------------------- #
def test_case_multi_field_distinct_fields_same_subject(tmp_path: Path) -> None:
    """MULTI_FIELD: multiple distinct-field claims for a subject all package."""
    vault = _matrix_vault(tmp_path)
    candidates = _scoped_candidates(vault, "claim-known-", PROJECT_A, mode="prefix")
    ids = sorted(c["record_id"] for c in candidates)
    assert ids == ["claim-known-owner", "claim-known-status"]

    # The underlying claims genuinely differ by field (MULTI_FIELD).
    retriever = VaultRetriever(vault)
    fields = {
        retriever.lookup("claim", rid, project_id=PROJECT_A)[0].record["field"] for rid in ids
    }
    assert fields == {"owner", "status"}

    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="multi-field",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=20,
    )
    assert package["entry_count"] == 2
    assert [e["record_id"] for e in package["entries"]] == [
        "claim-known-owner",
        "claim-known-status",
    ]
    validate_record(package, "runtime-context-compiler")


# --------------------------------------------------------------------------- #
# CONFLICT
# --------------------------------------------------------------------------- #
def test_case_conflict_retained_and_excludable(tmp_path: Path) -> None:
    """CONFLICT: unresolved conflict is stamped conflicting + kept as sidecar."""
    vault = _matrix_vault(tmp_path)
    candidates = _scoped_candidates(vault, "claim-conflict", PROJECT_A)

    retained = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="conflict-keep",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=20,
    )
    entry = retained["entries"][0]
    assert entry["conflict_state"] == "unresolved"
    assert entry["authority_level"] == "conflicting"
    assert entry["reason_included"] == "conflict-sidecar"
    assert entry["conflict_ids"] == ["conflict-status"]
    assert retained["pipeline_receipt"]["unresolved_conflicts_retained"] == 1

    excluded = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="conflict-drop",
        profile_id="p2-readonly",
        include_unresolved_conflicts=False,
        candidates=candidates,
        budget=20,
    )
    assert excluded["entry_count"] == 0
    receipt = excluded["pipeline_receipt"]
    assert receipt["conflicts_excluded"] == 1
    assert receipt["excluded_conflict_ids"] == ["conflict-status"]
    validate_record(retained, "runtime-context-compiler")


# --------------------------------------------------------------------------- #
# STALE
# --------------------------------------------------------------------------- #
def test_case_stale_freshness_from_portfolio(tmp_path: Path) -> None:
    """STALE: portfolio-declared stale evidence surfaces as stale freshness."""
    vault = _matrix_vault(tmp_path)
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="stale",
        profile_id="p2-readonly",
        candidates=_scoped_candidates(vault, "claim-stale-metric", PROJECT_A),
        budget=20,
    )
    assert package["entries"][0]["freshness"] == "stale"
    validate_record(package, "runtime-context-compiler")


# --------------------------------------------------------------------------- #
# FRESHNESS_UNKNOWN
# --------------------------------------------------------------------------- #
def test_case_freshness_unknown_never_invents_fresh(tmp_path: Path) -> None:
    """FRESHNESS_UNKNOWN: no corroboration -> unknown, even if caller says fresh."""
    vault = _matrix_vault(tmp_path)
    candidates = _scoped_candidates(vault, "claim-fresh-unknown", PROJECT_A)
    # Caller asserts "fresh" but portfolio has no corroboration -> unknown.
    candidates[0]["freshness"] = "fresh"
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="fresh-unknown",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=20,
    )
    assert package["entries"][0]["freshness"] == "unknown"
    assert package["pipeline_receipt"]["freshness_unknown_count"] == 1
    validate_record(package, "runtime-context-compiler")


# --------------------------------------------------------------------------- #
# AUTHORITY_DIFFERENCE (WEAK spot #1 -> closed)
# --------------------------------------------------------------------------- #
def test_authority_rank_ladder_is_pinned() -> None:
    """The full objective authority ladder ordering is pinned (no drift)."""
    assert sorted(_AUTHORITY_RANK, key=lambda level: _AUTHORITY_RANK[level]) == [
        "primary",
        "validated-execution",
        "maintained",
        "derived",
        "generated",
        "inferred",
        "pending",
        "conflicting",
        "rejected",
    ]
    # primary is strictly the top rung and rejected the bottom.
    assert _AUTHORITY_RANK["primary"] == min(_AUTHORITY_RANK.values())
    assert _AUTHORITY_RANK["rejected"] == max(_AUTHORITY_RANK.values())


def test_case_authority_difference_orders_by_full_rank(tmp_path: Path) -> None:
    """AUTHORITY_DIFFERENCE: >=3 distinct levels ordered by the rank ladder."""
    vault = _matrix_vault(tmp_path)
    candidates = [
        {
            "record_type": "claim",
            "record_id": "claim-auth-derived",
            "authority_level": "none",  # objective stamp -> derived
            "provenance": [{"kind": "source", "ref": "sources/der.md"}],
        },
        {
            "record_type": "claim",
            "record_id": "claim-auth-gen",
            "authority_level": "generated",
            "provenance": [{"kind": "source", "ref": "sources/gen.md"}],
        },
        {
            "record_type": "claim",
            "record_id": "claim-auth-inf",
            "authority_level": "inferred",
            "provenance": [{"kind": "source", "ref": "sources/inf.md"}],
        },
        {
            "record_type": "claim",
            "record_id": "claim-conflict",
            "authority_level": "derived",  # unresolved conflict -> conflicting
            "provenance": [{"kind": "source", "ref": "sources/c.md"}],
        },
    ]
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="authority-diff",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=20,
    )
    levels = [e["authority_level"] for e in package["entries"]]
    # Four distinct levels, ordered exactly by the _AUTHORITY_RANK ladder.
    assert levels == ["derived", "generated", "inferred", "conflicting"]
    ranks = [_AUTHORITY_RANK[level] for level in levels]
    assert ranks == sorted(ranks)  # packaged order honours rank
    assert len(set(levels)) >= 3
    assert [e["relevance_rank"] for e in package["entries"]] == [0, 1, 2, 3]
    # The compiler never spoofs caller hints up to a privileged rung.
    assert not ({"primary", "validated-execution", "maintained"} & set(levels))
    assert package["authority"]["llm_authority"] is False
    validate_record(package, "runtime-context-compiler")


@pytest.mark.parametrize("spoof", ["primary", "validated-execution", "maintained"])
def test_case_authority_difference_rejects_upward_spoof(tmp_path: Path, spoof: str) -> None:
    """AUTHORITY_DIFFERENCE: caller cannot spoof to a privileged rung."""
    vault = _matrix_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="authority-spoof"):
        compile_context(
            vault,
            project_id=PROJECT_A,
            pack_id="spoof",
            profile_id="p2-readonly",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-known-status",
                    "authority_level": spoof,
                    "provenance": [{"kind": "source", "ref": "sources/a-status.md"}],
                }
            ],
        )


# --------------------------------------------------------------------------- #
# GRAPH_ONLY (WEAK spot #2 -> closed)
# --------------------------------------------------------------------------- #
def test_case_graph_only_is_summary_and_never_authority(tmp_path: Path) -> None:
    """GRAPH_ONLY: graph is summary-only; contributes 0 candidates, 0 authority.

    On ``main`` the impact graph is a UI/summary surface by design: it never
    feeds retrieval candidates and never injects narrative authority. This
    committed assertion documents that invariant end-to-end.
    """
    vault = _matrix_vault(tmp_path)
    without_graph = hybrid_retrieve(
        vault,
        kind="claim",
        value="claim-known-",
        mode="prefix",
        cap=20,
        project_id=PROJECT_A,
    )
    with_graph = hybrid_retrieve(
        vault,
        kind="claim",
        value="claim-known-",
        mode="prefix",
        cap=20,
        project_id=PROJECT_A,
        include_graph_slot=True,
    )
    # Enabling the graph slot adds exactly zero retrieval candidates.
    assert with_graph["candidates"] == without_graph["candidates"]
    assert all(c["slot"] in ("lexical_exact", "lexical_prefix") for c in with_graph["candidates"])
    graph = with_graph["slots"]["graph"]
    assert graph["graph_authority"] is False
    assert graph["summary"]["graph_authority"] is False
    assert "truth_boundary" not in graph["summary"]  # attacker fields stripped
    assert graph["note"].startswith("GRAPH \u2260 AUTHORITY")
    assert "ATTACKER" not in json.dumps(with_graph)
    validate_record(with_graph, "runtime-hybrid-retrieval")

    # The compiled package over graph-active retrieval stays derived-only.
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="graph-only",
        profile_id="p2-readonly",
        candidates=with_graph["candidates"],
        budget=20,
    )
    assert package["authority"]["level"] == "derived"
    assert package["authority"]["llm_authority"] is False
    assert all(e["authority_level"] == "derived" for e in package["entries"])


# --------------------------------------------------------------------------- #
# HIGH_FANOUT (WEAK spot #3 -> closed)
# --------------------------------------------------------------------------- #
def test_case_high_fanout_dedupe_and_cap(tmp_path: Path) -> None:
    """HIGH_FANOUT: ~MAX_CAP fan-out verifies RRF dedupe + cap + compiler dedupe."""
    vault = _matrix_vault(tmp_path)
    fused = build_hybrid_rrf_fusion(
        vault, kind="claim", value=FANOUT_FIELD, project_id=PROJECT_A, cap=MAX_CAP
    )
    ids = [r["record_id"] for r in fused["results"]]
    assert fused["result_count"] == MAX_CAP == 100
    assert fused["truncated"] is True  # 120 fan-out -> capped at 100
    assert len(ids) == len(set(ids))  # RRF collapses duplicate list entries
    assert all(rid.startswith("claim-f") for rid in ids)
    # Every fused id was present in BOTH fusion lists (dedupe merged them).
    assert all(set(r["ranks"]) == {"lexical_exact", "bm25"} for r in fused["results"])

    # The cap ceiling is a hard bound (cannot request beyond MAX_CAP).
    with pytest.raises(HybridRetrievalError, match="cap-out-of-range"):
        build_hybrid_rrf_fusion(
            vault,
            kind="claim",
            value=FANOUT_FIELD,
            project_id=PROJECT_A,
            cap=MAX_CAP + 1,
        )

    # Compiler-side dedupe: 100 unique + 1 duplicate -> 100 entries, 1 collapsed.
    candidates = [
        {
            "record_type": "claim",
            "record_id": rid,
            "provenance": [{"kind": "source", "ref": "sources/fan.md"}],
        }
        for rid in ids
    ]
    candidates.append(dict(candidates[0]))  # exact duplicate row
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="high-fanout",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=MAX_CAP,
    )
    assert package["entry_count"] == MAX_CAP
    assert package["input_hygiene"]["duplicates_collapsed"] == 1
    assert package["truncated"] is False


# --------------------------------------------------------------------------- #
# BUDGET_OVERFLOW
# --------------------------------------------------------------------------- #
def test_case_budget_overflow_truncates_or_fails_closed(tmp_path: Path) -> None:
    """BUDGET_OVERFLOW: truncate honours budget; on_overflow=fail fails closed."""
    vault = _matrix_vault(tmp_path)
    fused = build_hybrid_rrf_fusion(
        vault, kind="claim", value=FANOUT_FIELD, project_id=PROJECT_A, cap=MAX_CAP
    )
    candidates = [
        {
            "record_type": "claim",
            "record_id": r["record_id"],
            "provenance": [{"kind": "source", "ref": "sources/fan.md"}],
        }
        for r in fused["results"]
    ]

    truncated = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="overflow-truncate",
        profile_id="p2-readonly",
        candidates=candidates,
        budget=5,
    )
    assert truncated["entry_count"] == 5
    assert truncated["truncated"] is True
    overflow = truncated["pipeline_receipt"]["overflow"]
    assert overflow["occurred"] is True
    assert overflow["dropped_count"] == MAX_CAP - 5
    assert overflow["policy"] == "truncate"

    with pytest.raises(Runtime22Error, match="budget-overflow"):
        compile_context(
            vault,
            project_id=PROJECT_A,
            pack_id="overflow-fail",
            profile_id="p2-readonly",
            candidates=candidates,
            budget=5,
            on_overflow="fail",
        )


# --------------------------------------------------------------------------- #
# MALFORMED_INPUT
# --------------------------------------------------------------------------- #
def test_case_malformed_input_hygiene_and_query_bounds(tmp_path: Path) -> None:
    """MALFORMED_INPUT: junk rows skipped/counted; bad queries fail closed."""
    vault = _matrix_vault(tmp_path)
    package = compile_context(
        vault,
        project_id=PROJECT_A,
        pack_id="malformed",
        profile_id="p2-readonly",
        candidates=[
            "not-a-dict",  # type: ignore[list-item]
            42,  # type: ignore[list-item]
            {"record_type": "claim"},  # missing record_id
            {"record_id": "claim-known-status"},  # missing record_type
            {
                "record_type": "claim",
                "record_id": "claim-known-status",
                "provenance": [
                    {"kind": "source", "ref": "sources/a-status.md"},
                    "string-elem",  # dropped
                    {"kind": "source", "ref": "../../etc/passwd"},  # dropped
                ],
            },
        ],
        budget=20,
    )
    assert package["entry_count"] == 1
    assert package["input_hygiene"]["skipped_malformed"] == 4
    assert package["input_hygiene"]["provenance_elems_dropped"] >= 2
    refs = [p["ref"] for p in package["entries"][0]["provenance"]]
    assert refs == ["sources/a-status.md"]

    # Malformed queries fail closed at the retrieval boundary.
    with pytest.raises(HybridRetrievalError, match="query-too-long"):
        build_hybrid_rrf_fusion(
            vault,
            kind="claim",
            value="x" * (MAX_QUERY_CHARS + 1),
            project_id=PROJECT_A,
        )
    with pytest.raises(HybridRetrievalError, match="value-empty"):
        build_hybrid_rrf_fusion(vault, kind="claim", value="   ", project_id=PROJECT_A)
    with pytest.raises(Runtime22Error, match="context-budget-invalid"):
        compile_context(
            vault,
            project_id=PROJECT_A,
            pack_id="bad-budget",
            profile_id="p2-readonly",
            candidates=[],
            budget=3.5,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------- #
# REPLAY_DETERMINISM
# --------------------------------------------------------------------------- #
def test_case_replay_determinism_byte_identical(tmp_path: Path) -> None:
    """REPLAY_DETERMINISM: repeated retrieval + compile are byte-identical."""
    vault = _matrix_vault(tmp_path)

    def _run() -> str:
        candidates = _scoped_candidates(vault, "claim-known-", PROJECT_A, mode="prefix")
        package = compile_context(
            vault,
            project_id=PROJECT_A,
            pack_id="replay",
            profile_id="p2-readonly",
            candidates=candidates,
            budget=20,
        )
        return package_to_json(package)

    assert _run() == _run()

    def _fuse() -> str:
        return fusion_to_json(
            build_hybrid_rrf_fusion(
                vault,
                kind="claim",
                value=FANOUT_FIELD,
                project_id=PROJECT_A,
                cap=MAX_CAP,
            )
        )

    assert _fuse() == _fuse()
