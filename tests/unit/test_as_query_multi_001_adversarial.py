"""AS-QUERY-MULTI-001 adversarial / invariant defenses (QM-ADV-*)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from project_atlas import knowledge_query, query_plan
from project_atlas.query_plan import (
    QueryPlanError,
    build_query_plan,
    plan_to_json,
    rejection_to_json,
)

_ROOT = Path(__file__).resolve().parents[2]
_QUERY_PLAN = _ROOT / "src" / "project_atlas" / "query_plan.py"
_SCHEMA = _ROOT / "src" / "project_atlas" / "schemas" / "query-multi-plan.schema.json"


def test_adv_001_plan_smuggle_as_answer_forbidden() -> None:
    plan = build_query_plan(
        [
            {
                "project_id": "p",
                "items": [
                    {
                        "shape": "point",
                        "kind": "authoritative",
                        "subject": "s",
                        "field": "f",
                    }
                ],
            }
        ]
    )
    dumped = plan_to_json(plan)
    assert '"plan_kind": "multi_query_plan"' in dumped
    assert '"package": "AS-QUERY-MULTI-001"' in dumped
    assert '"status": "ok"' not in dumped
    assert '"value"' not in dumped


def test_adv_002_silent_partial_batch_success_forbidden() -> None:
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [
                        {
                            "shape": "point",
                            "kind": "authoritative",
                            "subject": "good",
                            "field": "title",
                        },
                        {
                            "shape": "point",
                            "kind": "authoritative",
                            "subject": "",
                            "field": "title",
                        },
                    ],
                }
            ]
        )
    rejection = exc.value.to_dict()
    assert rejection["status"] == "request_invalid"
    assert "projects" not in rejection


def test_adv_003_graph_subject_invention_forbidden() -> None:
    schema_text = _SCHEMA.read_text(encoding="utf-8")
    assert "graph_id" not in schema_text
    assert "resolved_entity" not in schema_text
    with pytest.raises(QueryPlanError):
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [
                        {
                            "shape": "point",
                            "kind": "authoritative",
                            "subject": "s",
                            "field": "f",
                            "resolved_entity_id": "ent-1",
                        }
                    ],
                }
            ]
        )


def test_adv_004_trust_score_forbidden_in_module_and_schema() -> None:
    schema_text = _SCHEMA.read_text(encoding="utf-8")
    module_text = _QUERY_PLAN.read_text(encoding="utf-8")
    assert "trust_score" not in schema_text
    assert "confidence_score" not in schema_text
    assert "trust_score" in module_text  # listed as forbidden key
    assert "FORBIDDEN" in module_text or "forbidden" in module_text.lower()


def test_adv_005_query_001_list_semantics_untouched() -> None:
    """MULTI must not reopen QUERY-001 list helpers."""
    source = _QUERY_PLAN.read_text(encoding="utf-8")
    assert "list_authoritative" not in source
    assert "list_temporal" not in source
    # knowledge_query still exports certified list helpers unchanged.
    assert callable(knowledge_query.list_authoritative)
    assert callable(knowledge_query.list_temporal)


def test_adv_006_no_explain_writer_import() -> None:
    tree = ast.parse(_QUERY_PLAN.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "project_atlas.explain_receipts" not in imported
    assert "project_atlas.explain_graph_sidecars" not in imported


def test_adv_008_caller_order_stable() -> None:
    projects = [
        {
            "project_id": "z-last",
            "items": [
                {"shape": "list", "kind": "temporal"},
                {
                    "shape": "point",
                    "kind": "authoritative",
                    "subject": "a",
                    "field": "f",
                },
            ],
        },
        {
            "project_id": "a-first",
            "items": [
                {
                    "shape": "multifield",
                    "kind": "authoritative",
                    "subject": "s",
                    "fields": ["b", "a"],
                }
            ],
        },
    ]
    plan = build_query_plan(projects)
    assert [p["project_id"] for p in plan["projects"]] == ["z-last", "a-first"]
    assert [i["shape"] for i in plan["projects"][0]["items"]] == ["list", "point"]
    assert plan["projects"][1]["items"][0]["fields"] == ["b", "a"]


def test_adv_009_invalid_item_does_not_invent_answers_for_others() -> None:
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [
                        {
                            "shape": "point",
                            "kind": "authoritative",
                            "subject": "ok",
                            "field": "title",
                        },
                        {"shape": "unknown", "kind": "authoritative"},
                    ],
                }
            ]
        )
    parsed = json.loads(rejection_to_json(exc.value))
    assert parsed["status"] == "request_invalid"
    assert "projects" not in parsed
    assert "value" not in parsed
    assert all("value" not in item for item in parsed["invalid_items"])


def test_adv_010_rel_001_not_opened() -> None:
    source = _QUERY_PLAN.read_text(encoding="utf-8")
    assert "AS-REL-001" not in source or "MUST NOT" in source
    assert "relationship" not in source.lower() or "plan" in source.lower()


def test_adv_011_cli_untouched_by_module() -> None:
    source = _QUERY_PLAN.read_text(encoding="utf-8")
    assert "cli" not in source.lower() or "deferred" in source.lower()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "project_atlas.cli":
            pytest.fail("query_plan must not import cli")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "project_atlas.cli"


def test_adv_012_no_compiler_or_authority_writers() -> None:
    tree = ast.parse(_QUERY_PLAN.read_text(encoding="utf-8"))
    forbidden = {
        "project_atlas.knowledge_compiler",
        "project_atlas.authority_evaluator",
        "project_atlas.temporal_evaluator",
        "project_atlas.graph_quarantine",
        "project_atlas.xproj_edges",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported.isdisjoint(forbidden)
    assert query_plan.PACKAGE_ID == "AS-QUERY-MULTI-001"
