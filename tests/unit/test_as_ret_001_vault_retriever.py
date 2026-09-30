"""AS-RET-001 tip-safe VaultRetriever fail-closed and deterministic exact/prefix."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.retrieval import VaultRetriever


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _seed_minimal_vault(vault: Path) -> None:
    """Minimal generated indexes + concept state for read-only retrieval probes."""
    indexes = vault / "generated" / "indexes"
    indexes.mkdir(parents=True)
    _write_json(
        indexes / "concepts.json",
        {
            "by_concept_id": {
                "alpha-concept": ["alpha-concept"],
                "alpha-concept-extra": ["alpha-concept-extra"],
                "beta-concept": ["beta-concept"],
            },
            "by_type": {"project": ["alpha-concept", "beta-concept"]},
            "by_project_id": {"demo": ["alpha-concept", "beta-concept"]},
            "by_tag": {},
            "by_relationship_target": {},
        },
    )
    for name in (
        "sources.json",
        "claims.json",
        "conflicts.json",
        "authority.json",
        "provenance.json",
    ):
        _write_json(indexes / name, {})

    concepts_dir = vault / "state" / "concepts"
    concepts_dir.mkdir(parents=True)
    _write_json(
        concepts_dir / "demo.json",
        {
            "concepts": [
                {
                    "concept_id": "alpha-concept",
                    "type": "project",
                    "project_id": "demo",
                    "provenance": [{"source_lineage_id": "sline-alpha"}],
                },
                {
                    "concept_id": "alpha-concept-extra",
                    "type": "project",
                    "project_id": "demo",
                    "provenance": [{"source_lineage_id": "sline-extra"}],
                },
                {
                    "concept_id": "beta-concept",
                    "type": "project",
                    "project_id": "demo",
                    "provenance": [{"source_lineage_id": "sline-beta"}],
                },
            ]
        },
    )


def _vault_fingerprint(vault: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(vault).as_posix(): (path.read_bytes(), os.stat(path).st_mtime_ns)
        for path in vault.rglob("*")
        if path.is_file()
    }


def test_unsupported_kind_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    retriever = VaultRetriever(vault)
    with pytest.raises(ValueError, match="unsupported retrieval kind"):
        retriever.lookup("embedding", "alpha-concept")


def test_empty_and_whitespace_value_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    retriever = VaultRetriever(vault)
    for bad in ("", " ", "\t", "\n"):
        with pytest.raises(ValueError, match="non-empty"):
            retriever.lookup("concept", bad)
        with pytest.raises(ValueError, match="non-empty"):
            retriever.lookup("concept", bad, prefix=True)
        with pytest.raises(ValueError, match="non-empty"):
            retriever.search(bad)


def test_missing_generated_index_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    (vault / "generated" / "indexes" / "concepts.json").unlink()
    retriever = VaultRetriever(vault)
    with pytest.raises(ValueError, match="generated lexical index is missing"):
        retriever.lookup("concept", "alpha-concept")


def test_obsolete_indexes_directory_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    legacy = vault / "indexes"
    legacy.mkdir()
    (legacy / "marker.txt").write_text("obsolete", encoding="utf-8")
    retriever = VaultRetriever(vault)
    with pytest.raises(ValueError, match="obsolete generated index directory"):
        retriever.lookup("concept", "alpha-concept")
    assert (legacy / "marker.txt").read_text(encoding="utf-8") == "obsolete"


def test_exact_does_not_match_prefix_siblings(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    retriever = VaultRetriever(vault)

    exact = retriever.lookup("concept", "alpha-concept")
    assert [item.record_id for item in exact] == ["alpha-concept"]

    prefix = retriever.lookup("concept", "alpha-concept", prefix=True)
    assert [item.record_id for item in prefix] == [
        "alpha-concept",
        "alpha-concept-extra",
    ]

    miss = retriever.lookup("concept", "alpha")
    assert miss == []


def test_prefix_and_search_are_deterministic(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    retriever = VaultRetriever(vault)

    first = [item.record_id for item in retriever.lookup("concept", "alpha", prefix=True)]
    second = [item.record_id for item in retriever.lookup("concept", "alpha", prefix=True)]
    assert first == second == ["alpha-concept", "alpha-concept-extra"]

    search_a = [
        (item.record_type, item.record_id)
        for item in retriever.search("demo", kind=None, prefix=False)
    ]
    search_b = [
        (item.record_type, item.record_id)
        for item in retriever.search("demo", kind=None, prefix=False)
    ]
    assert search_a == search_b
    assert search_a == sorted(search_a)


def test_retrieve_alias_matches_lookup(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    retriever = VaultRetriever(vault)
    via_lookup = retriever.lookup("concept", "beta-concept")
    via_retrieve = retriever.retrieve("concept", "beta-concept")
    assert [item.record_id for item in via_lookup] == [item.record_id for item in via_retrieve]
    assert via_lookup[0].provenance == ({"source_lineage_id": "sline-beta"},)


def test_lookup_does_not_mutate_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_minimal_vault(vault)
    before = _vault_fingerprint(vault)
    retriever = VaultRetriever(vault)
    assert retriever.lookup("concept", "alpha-concept")
    assert retriever.lookup("concept", "alpha", prefix=True)
    assert retriever.search("beta-concept")
    after = _vault_fingerprint(vault)
    assert after == before
