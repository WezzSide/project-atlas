"""AS-2.2-RUNTIME-001 — project-scope isolation + query bounds (CLAUDE-009/013).

Regression coverage for validator W1's finding that the runtime hybrid surface
(:mod:`project_atlas.runtime_22`) returned candidates from *every* project on a
multi-project vault. The runtime surface now mirrors the fail-closed, project
scoped AS-2.0 hybrid surface: a non-empty ``project_id`` is required and no
cross-project record may be returned (default-deny). The runtime query path
also enforces the same query-length / term bounds as the AS-2.0 surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.hybrid_retrieval import MAX_QUERY_CHARS, MAX_QUERY_TERMS
from project_atlas.runtime_22 import (
    Runtime22Error,
    compile_context,
    hybrid_retrieve,
)
from project_atlas.schema import validate_record

# Two sibling projects that share the lexical key "version" (W1 reproduction).
P1 = "proj-p1"
P2 = "proj-p2"


def _multi_project_vault(tmp_path: Path) -> Path:
    """Vault with one ``field=version`` claim per project (shared lexical key)."""
    vault = tmp_path / "vault"
    (vault / "generated" / "indexes").mkdir(parents=True)
    (vault / "state" / "claims").mkdir(parents=True)
    index = {
        "by_claim_id": {
            "claim-p1-version": ["claim-p1-version"],
            "claim-p2-version": ["claim-p2-version"],
        },
        # Both projects index the same lexical value — the leak vector.
        "by_field": {"version": ["claim-p1-version", "claim-p2-version"]},
        "by_concept_id": {},
        "by_source_lineage_id": {},
    }
    (vault / "generated" / "indexes" / "claims.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    claims = {
        "claims": [
            {
                "claim_id": "claim-p1-version",
                "field": "version",
                "project_id": P1,
                "provenance": [{"ref": "sources/p1.md"}],
            },
            {
                "claim_id": "claim-p2-version",
                "field": "version",
                "project_id": P2,
                "provenance": [{"ref": "sources/p2.md"}],
            },
        ]
    }
    (vault / "state" / "claims" / "claims.json").write_text(
        json.dumps(claims, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return vault


def _candidate(record_id: str, ref: str) -> dict[str, object]:
    return {
        "record_type": "claim",
        "record_id": record_id,
        "slot": "lexical_exact",
        "authority_level": "derived",
        "provenance": [{"kind": "source", "ref": ref}],
    }


def test_hybrid_retrieve_scoped_returns_only_p1(tmp_path: Path) -> None:
    """(a) Scoped hybrid retrieval returns ONLY the scoped project's records."""
    vault = _multi_project_vault(tmp_path)
    report = hybrid_retrieve(vault, kind="claim", value="version", project_id=P1)
    ids = {c["record_id"] for c in report["candidates"]}
    assert ids == {"claim-p1-version"}
    assert "claim-p2-version" not in ids
    assert report["query"]["project_id"] == P1
    validate_record(report, "runtime-hybrid-retrieval")

    # The sibling scope is symmetric — no cross-contamination either direction.
    other = hybrid_retrieve(vault, kind="claim", value="version", project_id=P2)
    assert {c["record_id"] for c in other["candidates"]} == {"claim-p2-version"}


def test_regression_w1_cross_project_leak_fixed(tmp_path: Path) -> None:
    """W1's exact reproduction: kind=claim, value=version, two projects.

    Before remediation ``hybrid_retrieve`` returned claims from every project;
    now the scoped call yields only the scoped project's claim (no leak).
    """
    vault = _multi_project_vault(tmp_path)
    report = hybrid_retrieve(vault, kind="claim", value="version", project_id=P1)
    returned = [c["record_id"] for c in report["candidates"]]
    assert returned == ["claim-p1-version"]
    assert report["candidate_count"] == 1


def test_compile_context_scoped_returns_only_p1(tmp_path: Path) -> None:
    """(b) compile_context is likewise project-scoped."""
    vault = _multi_project_vault(tmp_path)
    package = compile_context(
        vault,
        pack_id="scoped-pack",
        project_id=P1,
        candidates=[_candidate("claim-p1-version", "sources/p1.md")],
    )
    ids = [e["record_id"] for e in package["entries"]]
    assert ids == ["claim-p1-version"]
    assert package["project_id"] == P1
    validate_record(package, "runtime-context-compiler")


def test_compile_context_rejects_cross_project_candidate(tmp_path: Path) -> None:
    """(b) A sibling-project candidate under P1 scope fails closed (no leak)."""
    vault = _multi_project_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="project-scope-mismatch"):
        compile_context(
            vault,
            pack_id="leak-pack",
            project_id=P1,
            candidates=[_candidate("claim-p2-version", "sources/p2.md")],
        )


def test_hybrid_retrieve_missing_scope_rejected(tmp_path: Path) -> None:
    """(c) Missing / empty project scope is rejected fail-closed."""
    vault = _multi_project_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="runtime-hybrid-project-scope-required"):
        hybrid_retrieve(vault, kind="claim", value="version", project_id="")
    with pytest.raises(Runtime22Error, match="runtime-hybrid-project-scope-required"):
        hybrid_retrieve(vault, kind="claim", value="version", project_id="   ")


def test_compile_context_missing_scope_rejected(tmp_path: Path) -> None:
    """(c) compile_context also rejects empty project scope fail-closed."""
    vault = _multi_project_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="runtime-context-project-scope-required"):
        compile_context(
            vault,
            pack_id="no-scope",
            project_id="   ",
            candidates=[_candidate("claim-p1-version", "sources/p1.md")],
        )


def test_hybrid_retrieve_query_too_long_rejected(tmp_path: Path) -> None:
    """(d) Over-length queries are rejected on the runtime query path."""
    vault = _multi_project_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="hybrid-query-too-long"):
        hybrid_retrieve(
            vault,
            kind="claim",
            value="v" * (MAX_QUERY_CHARS + 1),
            project_id=P1,
        )


def test_hybrid_retrieve_query_too_many_terms_rejected(tmp_path: Path) -> None:
    """(d) Too-many-distinct-term queries are rejected on the runtime path."""
    vault = _multi_project_vault(tmp_path)
    value = " ".join(f"term{i}" for i in range(MAX_QUERY_TERMS + 1))
    with pytest.raises(Runtime22Error, match="hybrid-query-too-many-terms"):
        hybrid_retrieve(vault, kind="claim", value=value, project_id=P1)


def test_cli_hybrid_retrieve_requires_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(e) CLI hybrid-retrieve without --project fails with a clear usage error."""
    vault = _multi_project_vault(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "runtime",
                "hybrid-retrieve",
                "--vault",
                str(vault),
                "--kind",
                "claim",
                "--value",
                "version",
            ]
        )
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--project" in captured.err


def test_cli_hybrid_retrieve_scoped_no_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(e) CLI hybrid-retrieve with --project succeeds and returns only P1."""
    vault = _multi_project_vault(tmp_path)
    code = main(
        [
            "runtime",
            "hybrid-retrieve",
            "--vault",
            str(vault),
            "--project",
            P1,
            "--kind",
            "claim",
            "--value",
            "version",
            "--json",
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    ids = {c["record_id"] for c in report["candidates"]}
    assert ids == {"claim-p1-version"}
    assert report["query"]["project_id"] == P1
