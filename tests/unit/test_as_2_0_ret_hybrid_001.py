"""AS-2.0-RET-HYBRID-001 hybrid retrieval plan harness tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor
from project_atlas.hybrid_retrieval import (
    PACKAGE_ID,
    HybridRetrievalError,
    build_hybrid_retrieval_plan,
    plan_to_json,
)
from project_atlas.schema import available_schemas, validate_record

ROOT = Path(__file__).resolve().parents[2]


def _seed_vault(vault: Path) -> None:
    """Minimal lexical indexes + concept state for VaultRetriever."""
    indexes = vault / "generated" / "indexes"
    indexes.mkdir(parents=True)
    concepts_index = {
        "by_concept_id": {"demo-concept": ["demo-concept"]},
        "by_type": {},
        "by_project_id": {"demo": ["demo-concept"]},
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
    (concepts_dir / "demo.json").write_text(
        json.dumps(
            {
                "concepts": [
                    {
                        "concept_id": "demo-concept",
                        "type": "project",
                        "project_id": "demo",
                        "provenance": [{"source_lineage_id": "sline-demo"}],
                    }
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


def test_hybrid_plan_exact_lexical_and_semantic_disabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)

    plan = build_hybrid_retrieval_plan(
        vault, kind="concept", value="demo-concept", project_id="demo", mode="exact"
    )

    assert plan["package_id"] == PACKAGE_ID
    assert plan["compat_snapshot_id"] == SNAPSHOT_ID
    assert plan["semantic_enabled"] is False
    assert plan["slots"]["semantic"]["enabled"] is False
    assert plan["slots"]["semantic"]["status"] == "disabled"
    assert plan["slots"]["lexical_exact"]["status"] == "active"
    assert plan["slots"]["lexical_prefix"]["status"] == "idle"
    assert plan["results"] == [
        {
            "record_type": "concept",
            "record_id": "demo-concept",
            "slot": "lexical_exact",
        }
    ]
    validate_record(plan, "hybrid-retrieval-plan")
    assert '"semantic_enabled": false' in plan_to_json(plan)


def test_hybrid_plan_prefix_mode(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)

    plan = build_hybrid_retrieval_plan(
        vault, kind="concept", value="demo-", project_id="demo", mode="prefix"
    )
    assert plan["slots"]["lexical_prefix"]["status"] == "active"
    assert plan["slots"]["lexical_exact"]["status"] == "idle"
    assert plan["results"][0]["slot"] == "lexical_prefix"
    assert plan["semantic_enabled"] is False


def test_hybrid_plan_does_not_write_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    before = _vault_fingerprint(vault)

    build_hybrid_retrieval_plan(vault, kind="concept", value="demo-concept", project_id="demo")

    after = _vault_fingerprint(vault)
    assert after == before
    assert list(vault.rglob("*.tmp")) == []


def test_hybrid_plan_binds_compat_anchor() -> None:
    anchor = require_compatibility_anchor()
    assert anchor.snapshot_id == SNAPSHOT_ID
    assert anchor.one_dot_oh_wins_conflicts is True


def test_hybrid_plan_rejects_semantic_enable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="semantic-slot-not-available"):
        build_hybrid_retrieval_plan(
            vault,
            kind="concept",
            value="demo-concept",
            project_id="demo",
            enable_semantic=True,
        )


def test_hybrid_plan_rejects_unknown_kind(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="kind-unsupported"):
        build_hybrid_retrieval_plan(vault, kind="embedding", value="x", project_id="demo")


def test_hybrid_module_does_not_invent_embeddings() -> None:
    text = (
        (ROOT / "src" / "project_atlas" / "hybrid_retrieval.py").read_text(encoding="utf-8").lower()
    )
    for forbidden in ("openai", "embedding_model", "vector_store", "nearest_neighbor"):
        assert forbidden not in text


def test_hybrid_plan_rejects_missing_project_scope(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_vault(vault)
    with pytest.raises(HybridRetrievalError, match="project-scope-required"):
        build_hybrid_retrieval_plan(vault, kind="concept", value="demo-concept", project_id="")


def test_hybrid_schema_registered_and_docs() -> None:
    assert "hybrid-retrieval-plan" in available_schemas()
    assert (ROOT / "docs" / "AS-2.0-RET-HYBRID-001.md").is_file()
