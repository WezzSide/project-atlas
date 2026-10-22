"""AS-RET-FILENAME-PROJECT-BIND-001 — filename stem is project ownership.

A sibling state file whose records declare another project's id must not
enter that project's lexical index or BM25 corpus. In-record
``project_id`` is not authority over the filename.
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.indexes import _claim_index, _conflict_index
from project_atlas.retrieval import VaultRetriever


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_foreign_file_claim_is_not_in_victim_corpus(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    claims = vault / "state" / "claims"
    _write_json(
        claims / "victim.json",
        {
            "claims": [
                {
                    "claim_id": "claim-honest",
                    "project_id": "victim",
                    "field": "datastore",
                    "value": "PostgreSQL 15",
                }
            ]
        },
    )
    _write_json(
        claims / "attacker.json",
        {
            "claims": [
                {
                    "claim_id": "claim-forged",
                    "project_id": "victim",
                    "field": "datastore",
                    "value": "FOREIGN-LEAK-POSTGRES-99",
                }
            ]
        },
    )
    indexes = vault / "generated" / "indexes"
    indexes.mkdir(parents=True)
    for name in (
        "sources.json",
        "claims.json",
        "concepts.json",
        "conflicts.json",
        "authority.json",
        "provenance.json",
    ):
        _write_json(indexes / name, {"by_claim_id": {}})

    retriever = VaultRetriever(vault)
    corpus = retriever.bm25_corpus("claim", project_id="victim")
    texts = " ".join(document for _record_id, document in corpus)
    assert "FOREIGN-LEAK-POSTGRES-99" not in texts
    assert "PostgreSQL 15" in texts
    assert {record_id for record_id, _ in corpus} == {"claim-honest"}


def test_claim_index_drops_stem_mismatched_records(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    claims = vault / "state" / "claims"
    _write_json(
        claims / "victim.json",
        {"claims": [{"claim_id": "claim-honest", "project_id": "victim"}]},
    )
    _write_json(
        claims / "attacker.json",
        {"claims": [{"claim_id": "claim-forged", "project_id": "victim"}]},
    )
    index = _claim_index(vault)
    assert index["ids"] == ["claim-honest"]
    assert "claim-forged" not in index["by_claim_id"]


def test_conflict_index_drops_stem_mismatched_entries(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    root = vault / "review" / "conflicts"
    _write_json(
        root / "victim.json",
        {
            "entries": [
                {
                    "conflict_id": "conflict-honest",
                    "project_id": "victim",
                    "claim_ids": ["a", "b"],
                }
            ]
        },
    )
    _write_json(
        root / "attacker.json",
        {
            "entries": [
                {
                    "conflict_id": "conflict-forged",
                    "project_id": "victim",
                    "claim_ids": ["c", "d"],
                }
            ]
        },
    )
    index = _conflict_index(vault)
    assert index["ids"] == ["conflict-honest"]
    assert "conflict-forged" not in index["by_conflict_id"]
