"""AS-RET Hybrid P2 — Lexical/BM25/RRF fusion tests (deterministic)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.hybrid_retrieval import (
    PACKAGE_ID,
    HybridRetrievalError,
    build_hybrid_retrieval_plan,
    build_hybrid_rrf_fusion,
    fusion_to_json,
)
from project_atlas.retrieval import VaultRetriever
from project_atlas.retrieval_fusion import bm25_rank, rrf_fuse, tokenize
from project_atlas.schema import available_schemas, validate_record


def _seed_vault(vault: Path) -> None:
    indexes = vault / "generated" / "indexes"
    indexes.mkdir(parents=True)
    concepts_index = {
        "by_concept_id": {
            "alpha-auth": ["alpha-auth"],
            "beta-token": ["beta-token"],
            "gamma-auth-token": ["gamma-auth-token"],
        },
        "by_type": {"capability": ["alpha-auth", "beta-token", "gamma-auth-token"]},
        "by_project_id": {"demo": ["alpha-auth", "beta-token", "gamma-auth-token"]},
        "by_tag": {"auth": ["alpha-auth", "gamma-auth-token"], "token": ["beta-token"]},
        "by_relationship_target": {},
    }
    (indexes / "concepts.json").write_text(
        json.dumps(concepts_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in (
        "sources.json",
        "claims.json",
        "conflicts.json",
        "authority.json",
        "provenance.json",
    ):
        (indexes / name).write_text("{}\n", encoding="utf-8", newline="\n")

    concepts_dir = vault / "state" / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "demo.json").write_text(
        json.dumps(
            {
                "concepts": [
                    {
                        "concept_id": "alpha-auth",
                        "type": "capability",
                        "project_id": "demo",
                        "summary": "authentication gate policy",
                        "provenance": [{"source_lineage_id": "sline-a"}],
                    },
                    {
                        "concept_id": "beta-token",
                        "type": "capability",
                        "project_id": "demo",
                        "summary": "bearer token issuance",
                        "provenance": [{"source_lineage_id": "sline-b"}],
                    },
                    {
                        "concept_id": "gamma-auth-token",
                        "type": "capability",
                        "project_id": "demo",
                        "summary": "auth token rotation policy",
                        "provenance": [{"source_lineage_id": "sline-c"}],
                    },
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _vault_fingerprint(vault: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(vault).as_posix(): (path.read_bytes(), os.stat(path).st_mtime_ns)
        for path in vault.rglob("*")
        if path.is_file()
    }


def test_tokenize_ascii_deterministic() -> None:
    assert tokenize("Auth-Token_v2") == ["auth", "token", "v2"]
    assert tokenize("") == []


def test_bm25_rank_empty_query_fails_closed() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        bm25_rank("   ", [("a", "alpha")])
    with pytest.raises(ValueError, match="non-empty"):
        bm25_rank("", [("a", "alpha")])


def test_bm25_rank_deterministic_order() -> None:
    corpus = [
        ("doc-b", "token bearer"),
        ("doc-a", "auth token policy"),
        ("doc-c", "unrelated"),
    ]
    first = bm25_rank("auth token", corpus)
    second = bm25_rank("auth token", corpus)
    assert first == second
    assert first[0][0] == "doc-a"
    assert all(score > 0 for _, score in first)


def test_rrf_fuse_prefers_multi_list_hits() -> None:
    fused = rrf_fuse(
        {
            "lexical_exact": ["only-lex", "both"],
            "bm25": ["both", "only-bm25"],
        }
    )
    assert fused[0][0] == "both"
    assert set(fused[0][2]) == {"bm25", "lexical_exact"}


def test_hybrid_rrf_fusion_lexical_bm25(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)

    report = build_hybrid_rrf_fusion(
        vault,
        kind="concept",
        value="auth token",
        project_id="demo",
        mode="exact",
        cap=10,
    )

    assert report["package_id"] == PACKAGE_ID
    assert report["artifact_kind"] == "hybrid-retrieval-rrf"
    assert report["semantic_enabled"] is False
    assert report["slots"]["semantic"]["enabled"] is False
    assert report["authority"]["level"] == "derived"
    assert report["authority"]["llm_authority"] is False
    assert report["fusion"]["method"] == "rrf"
    assert report["slots"]["bm25"]["status"] == "active"
    assert report["result_count"] == len(report["results"])
    assert report["results"]
    # Exact lexical key "auth token" will miss; BM25 should still surface docs.
    assert report["slots"]["lexical_exact"]["hit_count"] == 0
    assert report["slots"]["bm25"]["hit_count"] >= 1
    ids = [item["record_id"] for item in report["results"]]
    assert "gamma-auth-token" in ids
    assert all(item["authority_level"] == "derived" for item in report["results"])
    validate_record(report, "hybrid-retrieval-rrf")
    assert '"llm_authority": false' in fusion_to_json(report)


def test_hybrid_rrf_exact_lexical_boost(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)

    report = build_hybrid_rrf_fusion(
        vault, kind="concept", value="alpha-auth", project_id="demo", mode="exact"
    )
    assert report["slots"]["lexical_exact"]["hit_count"] == 1
    assert report["results"][0]["record_id"] == "alpha-auth"
    assert "lexical_exact" in report["results"][0]["ranks"]


def test_hybrid_rrf_empty_query_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="value-empty"):
        build_hybrid_rrf_fusion(vault, kind="concept", value="  ", project_id="demo")
    with pytest.raises(HybridRetrievalError, match="value-empty"):
        build_hybrid_retrieval_plan(vault, kind="concept", value="", project_id="demo")


def test_hybrid_rrf_rejects_semantic_authority(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="semantic-slot-not-available"):
        build_hybrid_rrf_fusion(
            vault,
            kind="concept",
            value="auth",
            project_id="demo",
            enable_semantic=True,
        )


def test_hybrid_rrf_does_not_write_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    before = _vault_fingerprint(vault)
    build_hybrid_rrf_fusion(vault, kind="concept", value="token", project_id="demo")
    assert _vault_fingerprint(vault) == before


def test_hybrid_rrf_repeat_run_byte_identical(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    a = fusion_to_json(
        build_hybrid_rrf_fusion(vault, kind="concept", value="auth", project_id="demo")
    )
    b = fusion_to_json(
        build_hybrid_rrf_fusion(vault, kind="concept", value="auth", project_id="demo")
    )
    assert a == b


def test_vault_retriever_bm25_corpus_sorted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    corpus = VaultRetriever(vault).bm25_corpus("concept")
    ids = [record_id for record_id, _text in corpus]
    assert ids == sorted(ids)
    assert all("alpha-auth" in text or record_id != "alpha-auth" for record_id, text in corpus)


def test_hybrid_rrf_schema_registered() -> None:
    assert "hybrid-retrieval-rrf" in available_schemas()
    assert "hybrid-retrieval-plan" in available_schemas()


def _seed_multi_project_vault(vault: Path) -> None:
    """Two-project fixture mirroring CLAUDE-REPRO-292 (009 cross-project leak)."""
    indexes = vault / "generated" / "indexes"
    indexes.mkdir(parents=True)
    concepts_index = {
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
    }
    (indexes / "concepts.json").write_text(
        json.dumps(concepts_index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in (
        "sources.json",
        "claims.json",
        "conflicts.json",
        "authority.json",
        "provenance.json",
    ):
        (indexes / name).write_text("{}\n", encoding="utf-8", newline="\n")

    concepts_dir = vault / "state" / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "PROJECT_A.json").write_text(
        json.dumps(
            {
                "concepts": [
                    {
                        "concept_id": "a-auth-gate",
                        "type": "capability",
                        "project_id": "PROJECT_A",
                        "summary": "secretmarker authentication gate",
                    },
                    {
                        "concept_id": "a-other",
                        "type": "capability",
                        "project_id": "PROJECT_A",
                        "summary": "PROJECT_A auxiliary",
                    },
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (concepts_dir / "PROJECT_B.json").write_text(
        json.dumps(
            {
                "concepts": [
                    {
                        "concept_id": "b-auth-gate",
                        "type": "capability",
                        "project_id": "PROJECT_B",
                        "summary": "secretmarker authentication gate",
                    },
                    {
                        "concept_id": "b-other",
                        "type": "capability",
                        "project_id": "PROJECT_B",
                        "summary": "PROJECT_B auxiliary",
                    },
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_hybrid_rrf_project_scope_isolates_cross_project(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_multi_project_vault(vault)

    shared = build_hybrid_rrf_fusion(
        vault,
        kind="concept",
        value="secretmarker authentication",
        project_id="PROJECT_A",
    )
    shared_ids = {item["record_id"] for item in shared["results"]}
    assert "a-auth-gate" in shared_ids
    assert "b-auth-gate" not in shared_ids

    keyed = build_hybrid_rrf_fusion(
        vault, kind="concept", value="PROJECT_A", project_id="PROJECT_A"
    )
    keyed_ids = {item["record_id"] for item in keyed["results"]}
    assert keyed_ids <= {"a-auth-gate", "a-other"}
    assert "b-auth-gate" not in keyed_ids
    assert "b-other" not in keyed_ids

    corpus = VaultRetriever(vault).bm25_corpus("concept", project_id="PROJECT_A")
    assert len(corpus) == 2
    assert all("a-" in record_id for record_id, _text in corpus)


def test_hybrid_rrf_rejects_missing_project_scope(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="project-scope-required"):
        build_hybrid_rrf_fusion(vault, kind="concept", value="auth", project_id="  ")


def test_hybrid_rrf_rejects_oversized_query(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="query-too-long"):
        build_hybrid_rrf_fusion(
            vault,
            kind="concept",
            value="secretmarker " * 50000,
            project_id="demo",
        )


def test_hybrid_rrf_rejects_too_many_query_terms(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    query = " ".join(f"term{n}" for n in range(300))
    with pytest.raises(HybridRetrievalError, match="query-too-many-terms"):
        build_hybrid_rrf_fusion(vault, kind="concept", value=query, project_id="demo")


def test_hybrid_rrf_missing_indexes_hybrid_error_contract(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(HybridRetrievalError, match="hybrid-retrieval-substrate"):
        build_hybrid_rrf_fusion(empty, kind="concept", value="auth", project_id="demo")
    with pytest.raises(HybridRetrievalError, match="hybrid-retrieval-substrate"):
        build_hybrid_retrieval_plan(empty, kind="concept", value="auth", project_id="demo")
