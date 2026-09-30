"""AS-2.2-RUNTIME-001 — Hybrid Retrieval + Context Compiler unit tests (P1 deepen)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.compat_anchor import SNAPSHOT_ID
from project_atlas.runtime_22 import (
    COMPILER_KIND,
    PACKAGE_ID,
    Runtime22Error,
    package_to_json,
)
from project_atlas.runtime_22 import (
    compile_context as _compile_context,
)
from project_atlas.runtime_22 import (
    hybrid_retrieve as _hybrid_retrieve,
)
from project_atlas.schema import validate_record

# CLAUDE-009: the runtime surface is now project-scoped (default-deny cross
# project). All fixture records carry this project id. These thin wrappers
# default the (now required) project scope so the pre-existing behavioural
# assertions keep exercising the scoped contract without per-call churn; the
# fail-closed / leak-isolation contract itself is covered explicitly in
# test_as_2_2_runtime_scope_001.py.
PROJECT_A = "proj-alpha"


def hybrid_retrieve(vault: Path, *, project_id: str = PROJECT_A, **kwargs: Any) -> dict[str, Any]:
    return _hybrid_retrieve(vault, project_id=project_id, **kwargs)


def compile_context(vault: Path, *, project_id: str = PROJECT_A, **kwargs: Any) -> dict[str, Any]:
    return _compile_context(vault, project_id=project_id, **kwargs)


def _mini_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "generated" / "indexes").mkdir(parents=True)
    (vault / "state" / "claims").mkdir(parents=True)
    index = {
        "by_claim_id": {"claim-alpha": ["claim-alpha"], "claim-beta": ["claim-beta"]},
        "by_field": {},
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
                "claim_id": "claim-alpha",
                "field": "status",
                "project_id": PROJECT_A,
                "provenance": [{"ref": "sources/a.md"}],
            },
            {
                "claim_id": "claim-beta",
                "field": "owner",
                "project_id": PROJECT_A,
                "provenance": [{"ref": "sources/b.md"}],
            },
        ]
    }
    (vault / "state" / "claims" / "claims.json").write_text(
        json.dumps(claims, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return vault


def test_hybrid_retrieve_lexical_and_cap(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    report = hybrid_retrieve(vault, kind="claim", value="claim-", mode="prefix", cap=1)
    assert report["package_id"] == PACKAGE_ID
    assert report["candidate_count"] == 1
    assert report["truncated"] is True
    assert report["slots"]["semantic"]["enabled"] is False
    assert report["authority"]["llm_authority"] is False
    assert report["candidates"][0]["authority_level"] == "derived"
    validate_record(report, "runtime-hybrid-retrieval")


def test_hybrid_retrieve_rejects_semantic(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="semantic-slot-forbidden"):
        hybrid_retrieve(
            vault,
            kind="claim",
            value="claim-alpha",
            enable_semantic=True,
        )


def test_hybrid_retrieve_rejects_float_cap(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="hybrid-cap-invalid"):
        hybrid_retrieve(vault, kind="claim", value="claim-alpha", cap=20.9)  # type: ignore[arg-type]


def test_hybrid_graph_slot_no_narrative_injection(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    graph_path = vault / "generated" / "indexes" / "impact-graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "nodes": [{"id": "n1"}],
                "edges": [],
                "note": "ATTACKER_WINS_AUTHORITY",
                "truth_boundary": "Graph=authority spoof",
                "authority_plane": "derived",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = hybrid_retrieve(
        vault,
        kind="claim",
        value="claim-alpha",
        include_graph_slot=True,
    )
    graph = report["slots"]["graph"]
    assert graph["graph_authority"] is False
    assert "ATTACKER" not in json.dumps(graph)
    assert graph["summary"]["graph_authority"] is False
    assert graph["summary"]["node_count"] == 1
    assert "truth_boundary" not in graph["summary"]
    assert graph["note"].startswith("GRAPH ≠ AUTHORITY")


def test_compile_context_budget_and_write(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    hybrid = hybrid_retrieve(vault, kind="claim", value="claim-", mode="prefix", cap=20)
    package = compile_context(
        vault,
        pack_id="demo-pack",
        candidates=hybrid["candidates"],
        budget=1,
        write=True,
    )
    assert package["entry_count"] == 1
    assert package["truncated"] is True
    assert package["authority"]["estate_facts_invented"] is False
    assert package["output_path"] == ("generated/context-compiler/demo-pack-context-compiler.json")
    assert not Path(package["output_path"]).is_absolute()
    out = vault / "generated" / "context-compiler" / "demo-pack-context-compiler.json"
    assert out.is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["pack_id"] == "demo-pack"
    assert "output_path" not in loaded
    validate_record(package, "runtime-context-compiler")


def test_compile_context_unknown_profile(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="profile-unknown"):
        compile_context(
            vault,
            pack_id="x",
            candidates=[],
            profile_id="invent-pilot",
        )


def test_compile_context_authority_spoof(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="authority-spoof"):
        compile_context(
            vault,
            pack_id="x",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-alpha",
                    "authority_level": "canonical",
                    "provenance": [{"kind": "source", "ref": "sources/a.md"}],
                }
            ],
        )


def test_compile_context_skips_malformed_candidates(tmp_path: Path) -> None:
    """Non-dict / incomplete rows skip with honesty count."""
    vault = _mini_vault(tmp_path)
    package = compile_context(
        vault,
        pack_id="hygiene",
        candidates=[
            "not-a-dict",  # type: ignore[list-item]
            42,  # type: ignore[list-item]
            {"record_type": "claim"},  # missing record_id
            {"record_id": "only-id"},  # missing record_type
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "provenance": [{"kind": "source", "ref": "sources/a.md"}],
            },
        ],
    )
    assert package["entry_count"] == 1
    assert package["entries"][0]["record_id"] == "claim-alpha"
    assert package["input_hygiene"]["skipped_malformed"] == 4


def test_compile_context_refuses_invented_record(tmp_path: Path) -> None:
    """RT-ADV-001 — vault-absent IDs fail closed; no honesty-lie package."""
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="record-absent"):
        compile_context(
            vault,
            pack_id="invent-pack",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "totally-invented-never-in-vault",
                    "authority_level": "none",
                    "provenance": [{"kind": "source", "ref": "sources/fake.md"}],
                }
            ],
        )


def test_compile_context_refuses_empty_provenance(tmp_path: Path) -> None:
    """RT-ADV-004 — empty/missing provenance fail closed (no invent backfill)."""
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="provenance-empty"):
        compile_context(
            vault,
            pack_id="empty-prov",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-alpha",
                    "authority_level": "none",
                    "provenance": [],
                }
            ],
        )
    with pytest.raises(Runtime22Error, match="provenance-missing"):
        compile_context(
            vault,
            pack_id="missing-prov",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-alpha",
                }
            ],
        )
    with pytest.raises(Runtime22Error, match="provenance-empty"):
        compile_context(
            vault,
            pack_id="dropped-prov",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-alpha",
                    "provenance": [
                        {"kind": "source", "ref": "../../etc/passwd"},
                        "string-elem",
                    ],
                }
            ],
        )


def test_compile_context_sanitizes_provenance_and_dedupes(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    package = compile_context(
        vault,
        pack_id="sanitize",
        candidates=[
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "provenance": [
                    {"kind": "source", "ref": "sources/ok.md"},
                    "string-elem",
                    {"kind": "source", "ref": "../../etc/passwd"},
                    {"kind": "source", "ref": "/abs/path"},
                    {"kind": "evil", "ref": "sources/x.md"},
                ],
            },
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "provenance": [{"kind": "index", "ref": "generated/indexes/claims"}],
            },
        ],
        budget=5,
    )
    assert package["entry_count"] == 1
    assert package["input_hygiene"]["duplicates_collapsed"] == 1
    assert package["input_hygiene"]["provenance_elems_dropped"] >= 3
    refs = [p["ref"] for p in package["entries"][0]["provenance"]]
    assert refs == ["sources/ok.md"]
    assert all(".." not in r for r in refs)


def test_compile_context_rejects_float_budget(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="context-budget-invalid"):
        compile_context(vault, pack_id="x", candidates=[], budget=3.5)  # type: ignore[arg-type]


def test_compile_context_output_contract_golden(tmp_path: Path) -> None:
    """Deterministic compile_context contract — schema + byte-stable golden."""
    vault = _mini_vault(tmp_path)
    candidates = [
        {
            "record_type": "claim",
            "record_id": "claim-beta",
            "slot": "lexical_exact",
            "authority_level": "derived",
            "provenance": [{"kind": "source", "ref": "sources/b.md"}],
        },
        {
            "record_type": "claim",
            "record_id": "claim-alpha",
            "slot": "lexical_exact",
            "authority_level": "none",
            "provenance": [{"kind": "source", "ref": "sources/a.md"}],
        },
    ]
    package = compile_context(
        vault,
        pack_id="golden-pack",
        candidates=candidates,
        budget=10,
    )
    validate_record(package, "runtime-context-compiler")
    assert package["artifact_kind"] == COMPILER_KIND
    assert package["compat_snapshot_id"] == SNAPSHOT_ID
    assert [e["entry_id"] for e in package["entries"]] == [
        "claim:claim-alpha",
        "claim:claim-beta",
    ]

    expected = {
        "artifact_kind": "runtime-context-compiler",
        "authority": {
            "candidates_caller_supplied": True,
            "estate_facts_invented": False,
            "level": "derived",
            "llm_authority": False,
            "pilot": False,
        },
        "budget": 10,
        "compat_snapshot_id": SNAPSHOT_ID,
        "entries": [
            {
                "authority_level": "derived",
                "entry_id": "claim:claim-alpha",
                "provenance": [{"kind": "source", "ref": "sources/a.md"}],
                "record_id": "claim-alpha",
                "record_type": "claim",
                "slot": "lexical_exact",
            },
            {
                "authority_level": "derived",
                "entry_id": "claim:claim-beta",
                "provenance": [{"kind": "source", "ref": "sources/b.md"}],
                "record_id": "claim-beta",
                "record_type": "claim",
                "slot": "lexical_exact",
            },
        ],
        "entry_count": 2,
        "generated": {"by": "project-atlas"},
        "input_hygiene": {
            "duplicates_collapsed": 0,
            "empty_provenance_policy": "refuse",
            "provenance_elems_dropped": 0,
            "skipped_malformed": 0,
        },
        "pack_id": "golden-pack",
        "project_id": PROJECT_A,
        "package_id": PACKAGE_ID,
        "pipeline": [
            "candidates",
            "vault_presence",
            "provenance_gate",
            "authority_stamp",
            "budget",
            "package",
        ],
        "profile_id": "p0-readonly",
        "schema_version": 1,
        "truncated": False,
        "truth_boundary": ("CONTEXT COMPILER ≠ ESTATE FACTS / ≠ PILOT / ≠ LLM AUTHORITY"),
    }
    assert package == expected
    assert package_to_json(package) == package_to_json(expected)
    again = compile_context(
        vault,
        pack_id="golden-pack",
        candidates=candidates,
        budget=10,
    )
    assert package_to_json(again) == package_to_json(expected)


def test_compile_context_no_layer_b_write(tmp_path: Path) -> None:
    vault = _mini_vault(tmp_path)
    projects = vault / "projects"
    projects.mkdir()
    marker = projects / "untouched.md"
    marker.write_text("human\n", encoding="utf-8")
    compile_context(
        vault,
        pack_id="readonly",
        candidates=[
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "provenance": [{"kind": "source", "ref": "sources/a.md"}],
            },
        ],
        write=True,
    )
    assert marker.read_text(encoding="utf-8") == "human\n"
    assert not any(projects.rglob("*-context-compiler.json"))
    assert (vault / "generated" / "context-compiler").is_dir()


def _p2_vault(tmp_path: Path) -> Path:
    """Mini vault with claims, portfolio freshness, and an unresolved conflict."""
    vault = _mini_vault(tmp_path)
    portfolio = vault / "generated" / "portfolio"
    portfolio.mkdir(parents=True)
    (portfolio / "stale-knowledge.json").write_text(
        json.dumps(
            {
                "sources": [
                    {"source_id": "a", "freshness": "stale"},
                    {"source_id": "b", "freshness": "fresh"},
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    conflicts = vault / "review" / "conflicts"
    conflicts.mkdir(parents=True)
    (conflicts / "conflicts.json").write_text(
        json.dumps(
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
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return vault


def test_compile_context_p2_pipeline_authority_freshness_conflicts(
    tmp_path: Path,
) -> None:
    """P2: authority/freshness/conflicts/relevance/budget with receipt."""
    vault = _p2_vault(tmp_path)
    package = compile_context(
        vault,
        pack_id="p2-pack",
        profile_id="p2-readonly",
        budget=10,
        candidates=[
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "slot": "lexical_exact",
                "authority_level": "derived",
                "provenance": [{"kind": "source", "ref": "sources/a.md"}],
            },
            {
                "record_type": "claim",
                "record_id": "claim-beta",
                "slot": "lexical_exact",
                "authority_level": "inferred",
                "provenance": [{"kind": "source", "ref": "sources/b.md"}],
            },
        ],
    )
    assert package["profile_id"] == "p2-readonly"
    assert package["pipeline"] == [
        "candidates",
        "vault_presence",
        "provenance_gate",
        "authority",
        "freshness",
        "conflicts",
        "relevance",
        "budget",
        "package",
    ]
    by_id = {e["record_id"]: e for e in package["entries"]}
    assert by_id["claim-alpha"]["conflict_state"] == "unresolved"
    assert by_id["claim-alpha"]["authority_level"] == "conflicting"
    assert by_id["claim-alpha"]["freshness"] == "stale"
    assert by_id["claim-alpha"]["reason_included"] == "conflict-sidecar"
    assert by_id["claim-alpha"]["conflict_ids"] == ["conflict-alpha-status"]
    assert by_id["claim-beta"]["conflict_state"] == "none"
    assert by_id["claim-beta"]["authority_level"] == "inferred"
    assert by_id["claim-beta"]["freshness"] == "fresh"
    # Relevance: derived/inferred before conflicting.
    assert [e["record_id"] for e in package["entries"]] == [
        "claim-beta",
        "claim-alpha",
    ]
    assert package["pipeline_receipt"]["unresolved_conflicts_retained"] == 1
    assert package["authority"]["llm_authority"] is False
    assert package["authority"]["estate_facts_invented"] is False
    validate_record(package, "runtime-context-compiler")
    again = compile_context(
        vault,
        pack_id="p2-pack",
        profile_id="p2-readonly",
        budget=10,
        candidates=[
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "slot": "lexical_exact",
                "authority_level": "derived",
                "provenance": [{"kind": "source", "ref": "sources/a.md"}],
            },
            {
                "record_type": "claim",
                "record_id": "claim-beta",
                "slot": "lexical_exact",
                "authority_level": "inferred",
                "provenance": [{"kind": "source", "ref": "sources/b.md"}],
            },
        ],
    )
    assert package_to_json(again) == package_to_json(package)


def test_compile_context_p2_freshness_launder_fails(tmp_path: Path) -> None:
    vault = _p2_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="freshness-launder"):
        compile_context(
            vault,
            pack_id="launder",
            profile_id="p2-readonly",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-alpha",
                    "freshness": "fresh",
                    "provenance": [{"kind": "source", "ref": "sources/a.md"}],
                }
            ],
        )


def test_compile_context_p2_budget_overflow_fail(tmp_path: Path) -> None:
    vault = _p2_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="budget-overflow"):
        compile_context(
            vault,
            pack_id="overflow",
            profile_id="p2-readonly",
            budget=1,
            on_overflow="fail",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-alpha",
                    "provenance": [{"kind": "source", "ref": "sources/a.md"}],
                },
                {
                    "record_type": "claim",
                    "record_id": "claim-beta",
                    "provenance": [{"kind": "source", "ref": "sources/b.md"}],
                },
            ],
        )


def test_compile_context_p2_exclude_unresolved_conflicts(tmp_path: Path) -> None:
    vault = _p2_vault(tmp_path)
    package = compile_context(
        vault,
        pack_id="no-conflict",
        profile_id="p2-readonly",
        include_unresolved_conflicts=False,
        candidates=[
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "provenance": [{"kind": "source", "ref": "sources/a.md"}],
            },
            {
                "record_type": "claim",
                "record_id": "claim-beta",
                "provenance": [{"kind": "source", "ref": "sources/b.md"}],
            },
        ],
    )
    assert package["entry_count"] == 1
    assert package["entries"][0]["record_id"] == "claim-beta"
    receipt = package["pipeline_receipt"]
    assert receipt["conflicts_excluded"] == 1
    assert receipt["unresolved_conflicts_retained"] == 0
    assert receipt["excluded_conflict_ids"] == ["conflict-alpha-status"]
    assert receipt["excluded_conflicts_detail"] == [
        {
            "conflict_id": "conflict-alpha-status",
            "claim_ids": ["claim-alpha"],
            "subject": "project",
            "field": "status",
            "excluded_claim_id": "claim-alpha",
        }
    ]
    validate_record(package, "runtime-context-compiler")


def test_compile_context_p2_freshness_conservative_multi_provenance(
    tmp_path: Path,
) -> None:
    """ADV005-001/005: stale supporting evidence wins over unrelated fresh."""
    vault = _p2_vault(tmp_path)
    (vault / "generated" / "indexes" / "claims.json").write_text(
        json.dumps(
            {
                "by_claim_id": {
                    "claim-c": ["claim-c"],
                    "claim-alpha": ["claim-alpha"],
                    "claim-beta": ["claim-beta"],
                },
                "by_field": {},
                "by_concept_id": {},
                "by_source_lineage_id": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    claims = {
        "claims": [
            {
                "claim_id": "claim-c",
                "field": "status",
                "project_id": PROJECT_A,
                "provenance": [{"ref": "sources/a.md"}],
            },
            *json.loads((vault / "state" / "claims" / "claims.json").read_text(encoding="utf-8"))[
                "claims"
            ],
        ]
    }
    (vault / "state" / "claims" / "claims.json").write_text(
        json.dumps(claims, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    candidate = {
        "record_type": "claim",
        "record_id": "claim-c",
        "provenance": [
            {"kind": "source", "ref": "sources/b.md"},
            {"kind": "source", "ref": "sources/a.md"},
        ],
    }
    laundered = compile_context(
        vault,
        pack_id="adv005-001",
        profile_id="p2-readonly",
        candidates=[candidate],
    )
    assert laundered["entries"][0]["freshness"] == "stale"
    order_ab = compile_context(
        vault,
        pack_id="adv005-005-ab",
        profile_id="p2-readonly",
        candidates=[
            {
                **candidate,
                "provenance": [
                    {"kind": "source", "ref": "sources/a.md"},
                    {"kind": "source", "ref": "sources/b.md"},
                ],
            }
        ],
    )
    order_ba = compile_context(
        vault,
        pack_id="adv005-005-ba",
        profile_id="p2-readonly",
        candidates=[candidate],
    )
    assert order_ab["entries"][0]["freshness"] == "stale"
    assert order_ba["entries"][0]["freshness"] == "stale"
    validate_record(laundered, "runtime-context-compiler")


def test_compile_context_p2_authority_spoof(tmp_path: Path) -> None:
    vault = _p2_vault(tmp_path)
    with pytest.raises(Runtime22Error, match="authority-spoof"):
        compile_context(
            vault,
            pack_id="spoof",
            profile_id="p2-readonly",
            candidates=[
                {
                    "record_type": "claim",
                    "record_id": "claim-beta",
                    "authority_level": "primary",
                    "provenance": [{"kind": "source", "ref": "sources/b.md"}],
                }
            ],
        )


def test_compile_context_p2_unknown_freshness_default(tmp_path: Path) -> None:
    """Absent portfolio match → freshness unknown (never invent fresh)."""
    vault = _mini_vault(tmp_path)
    package = compile_context(
        vault,
        pack_id="unknown-fresh",
        profile_id="p2-readonly",
        candidates=[
            {
                "record_type": "claim",
                "record_id": "claim-alpha",
                "freshness": "fresh",
                "provenance": [{"kind": "source", "ref": "sources/missing.md"}],
            }
        ],
    )
    assert package["entries"][0]["freshness"] == "unknown"
    assert package["pipeline_receipt"]["freshness_unknown_count"] == 1
