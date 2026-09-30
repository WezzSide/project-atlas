"""AS-QUERY-MULTI-001 tip-safe multi-subject / multi-project query plans."""

from __future__ import annotations

import json
from typing import Any

import pytest

from project_atlas.query_plan import (
    PACKAGE_ID,
    PLAN_KIND,
    QueryPlanError,
    build_query_plan,
    plan_to_json,
    rejection_to_json,
    validate_query_plan,
)
from project_atlas.schema import validate_record


def _point(subject: str, field: str, *, kind: str = "authoritative") -> dict[str, Any]:
    return {"shape": "point", "kind": kind, "subject": subject, "field": field}


def _multifield(subject: str, fields: list[str], *, kind: str = "authoritative") -> dict[str, Any]:
    return {"shape": "multifield", "kind": kind, "subject": subject, "fields": fields}


def _list_item(*, kind: str = "authoritative") -> dict[str, Any]:
    return {"shape": "list", "kind": kind}


def test_multi_subject_plan_schema_valid_and_deterministic() -> None:
    """QM-FR-001/011 — schema-valid plan; identical inputs → byte-identical JSON."""
    projects = [
        {
            "project_id": "proj-a",
            "items": [
                _point("subj-1", "title"),
                _multifield("subj-2", ["status", "owner"]),
                _list_item(kind="temporal"),
            ],
        }
    ]
    plan_a = build_query_plan(projects, notes=["fixture multi-subject"])
    plan_b = build_query_plan(projects, notes=["fixture multi-subject"])
    assert plan_a == plan_b
    assert plan_to_json(plan_a) == plan_to_json(plan_b)
    assert plan_a["package"] == PACKAGE_ID
    assert plan_a["plan_kind"] == PLAN_KIND
    validate_record(plan_a, "query-multi-plan")
    # Caller order preserved (QM-FR-011).
    shapes = [item["shape"] for item in plan_a["projects"][0]["items"]]
    assert shapes == ["point", "multifield", "list"]


def test_multi_project_plan_project_scoped_not_cross_join() -> None:
    """QM-FR-004 — project-scoped lists; not XPROJ/graph joins."""
    plan = build_query_plan(
        [
            {"project_id": "proj-a", "items": [_point("s1", "f1")]},
            {"project_id": "proj-b", "items": [_list_item(kind="authoritative")]},
        ]
    )
    assert [p["project_id"] for p in plan["projects"]] == ["proj-a", "proj-b"]
    blob = json.dumps(plan)
    assert "global_entity" not in blob
    assert "graph_" not in blob
    validate_query_plan(plan)


def test_empty_plan_fail_closed() -> None:
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan([])
    assert exc.value.code == "request_invalid"
    rejection = exc.value.to_dict()
    assert rejection["status"] == "request_invalid"
    assert rejection["package"] == PACKAGE_ID


def test_empty_items_fail_closed() -> None:
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan([{"project_id": "p", "items": []}])
    assert exc.value.code == "request_invalid"


def test_unknown_shape_rejects_entire_plan() -> None:
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [
                        _point("ok", "title"),
                        {"shape": "batch", "kind": "authoritative", "subject": "x", "field": "y"},
                    ],
                }
            ]
        )
    assert exc.value.code == "request_invalid"
    assert any(i.get("reason") == "unknown_or_missing_shape" for i in exc.value.invalid_items)
    # Never a success plan with the valid item smuggled through.
    assert "projects" not in exc.value.to_dict()


def test_mixed_valid_invalid_no_silent_partial_success() -> None:
    """QM-FR-010 / QM-ADV-002 — reject entire plan; list invalids."""
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan(
            [
                {
                    "project_id": "p1",
                    "items": [
                        _point("a", "title"),
                        {"shape": "point", "kind": "authoritative", "subject": "b"},
                    ],
                },
                {
                    "project_id": "p2",
                    "items": [
                        {"shape": "list", "kind": "nope"},
                    ],
                },
            ]
        )
    assert len(exc.value.invalid_items) >= 2
    text = rejection_to_json(exc.value)
    assert '"status": "request_invalid"' in text
    assert "projects" not in json.loads(text)


def test_plan_is_not_answer_envelope() -> None:
    """QM-ADV-001 — plan ≠ answer."""
    plan = build_query_plan([{"project_id": "p", "items": [_point("s", "f")]}])
    assert "value" not in plan
    assert "answer" not in plan
    assert "answers" not in plan
    assert plan["plan_kind"] == "multi_query_plan"
    for item in plan["projects"][0]["items"]:
        assert "value" not in item
        assert "claim_id" not in item
        assert "authority_disposition" not in item


def test_forbidden_trust_and_graph_fields() -> None:
    """QM-ADV-003 / QM-ADV-004."""
    with pytest.raises(QueryPlanError):
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [
                        {
                            **_point("s", "f"),
                            "trust_score": 0.9,
                        }
                    ],
                }
            ]
        )
    with pytest.raises(QueryPlanError):
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [
                        {
                            **_point("s", "f"),
                            "graph_id": "g-1",
                        }
                    ],
                }
            ]
        )
    with pytest.raises(QueryPlanError):
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "confidence": 1.0,
                    "items": [_point("s", "f")],
                }
            ]
        )


def test_list_item_rejects_subject_fields() -> None:
    with pytest.raises(QueryPlanError):
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [{"shape": "list", "kind": "authoritative", "subject": "nope"}],
                }
            ]
        )


def test_secret_shaped_notes_redacted() -> None:
    """QM-ADV-007 / NFR-004."""
    plan = build_query_plan(
        [{"project_id": "p", "items": [_point("s", "f")]}],
        notes=["api_key = SuperSecretValue1234567890"],
    )
    assert plan["notes"] == ["plan note redacted (secret-shaped content)"]
    assert "SuperSecret" not in plan_to_json(plan)


def test_attach_explain_receipt_hint_allowed() -> None:
    """QM-FR-006 — optional consume-only EXPLAIN Band A planning hint."""
    plan = build_query_plan(
        [
            {
                "project_id": "p",
                "items": [
                    {
                        **_point("s", "f", kind="explain"),
                        "attach_explain_receipt": True,
                    }
                ],
            }
        ]
    )
    assert plan["projects"][0]["items"][0]["attach_explain_receipt"] is True
    validate_record(plan, "query-multi-plan")


def test_oversized_batch_rejected() -> None:
    items = [_point(f"s{i}", "f") for i in range(257)]
    with pytest.raises(QueryPlanError) as exc:
        build_query_plan([{"project_id": "p", "items": items}])
    assert any(i.get("reason") == "oversized_items" for i in exc.value.invalid_items)


def test_duplicate_multifield_names_rejected() -> None:
    with pytest.raises(QueryPlanError):
        build_query_plan(
            [
                {
                    "project_id": "p",
                    "items": [_multifield("s", ["a", "a"])],
                }
            ]
        )


def test_validate_query_plan_rejects_wrong_package() -> None:
    plan = build_query_plan([{"project_id": "p", "items": [_point("s", "f")]}])
    bad = dict(plan)
    bad["package"] = "AS-QUERY-001"
    with pytest.raises(QueryPlanError):
        validate_query_plan(bad)
