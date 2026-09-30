"""AT3-061 — Intent vs current-state honesty wrapper.

Makes INTENT != CURRENT STATE a fail-closed isolated surface.
Composes AT3-043 extract_intent_report. Never writes Truth Core.

Honesty:
- INTENT != CURRENT STATE
- CURRENT STATE != INTENT
- LAYERS MUST NOT COLLAPSE
- STALE != CURRENT
- MODEL != OWNER
- MEMORY != TRUTH CORE
- MERGE_AUTHORIZATION = NOT_GRANTED
"""

from __future__ import annotations

from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.intent import extract_intent_report

PACKAGE_ID: Final[str] = "AT3-061"
GENERATOR_ID: Final[str] = "atlas3-honesty-061"
COMPOSED_FROM: Final[str] = "AT3-043"
TRUTH_BOUNDARY: Final[str] = (
    "INTENT != CURRENT STATE / CURRENT STATE != INTENT / "
    "LAYERS MUST NOT COLLAPSE / STALE != CURRENT / "
    "MODEL != OWNER / MEMORY != TRUTH CORE / "
    "MERGE_AUTHORIZATION = NOT_GRANTED"
)

_INTENT_TYPES = frozenset({"proposed_decision", "next_step", "idea", "action_item"})
_STATE_TYPES = frozenset({"claim_candidate", "constraint", "observation", "research_finding"})


def _declared_layers(item: dict[str, Any]) -> list[str]:
    raw = item.get("layer")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(value).strip() for value in raw if str(value).strip()]
    text = str(raw).strip()
    return [text] if text else []


def _identity(row: dict[str, Any]) -> str:
    explicit = str(row.get("id") or "").strip()
    if explicit:
        return explicit
    return str(row.get("text") or "").strip().lower()


def _preflight_collapse(items: list[dict[str, Any]]) -> None:
    for raw in items:
        if not isinstance(raw, dict):
            raise Atlas3Error("INTENT_ITEM_INVALID", "memory item is not an object")
        if raw.get("promoted_to_truth_core") is True:
            raise Atlas3Error(
                "TRUTH_CORE_PROMOTION_ATTEMPT",
                "honesty wrapper refuses Truth Core promotion",
            )
        if raw.get("collapse_layers") is True or raw.get("collapse") is True:
            raise Atlas3Error("LAYER_COLLAPSE", "explicit layer collapse is forbidden")
        layers = _declared_layers(raw)
        if "intent" in layers and "current_state" in layers:
            raise Atlas3Error("LAYER_COLLAPSE", "item declared both intent and current_state")
        item_type = str(raw.get("item_type") or "").strip()
        if item_type in _INTENT_TYPES and "current_state" in layers:
            raise Atlas3Error(
                "INTENT_COLLAPSED_TO_STATE",
                "intent item must not be declared as current_state",
            )
        if item_type in _STATE_TYPES and "intent" in layers:
            raise Atlas3Error(
                "STATE_PRESENTED_AS_INTENT",
                "current-state item must not be declared as intent",
            )
        if raw.get("present_as_current") is True and item_type in _INTENT_TYPES:
            raise Atlas3Error(
                "INTENT_COLLAPSED_TO_STATE",
                "intent item must not be presented as current",
            )


def _assert_layers_disjoint(layers: dict[str, list[dict[str, Any]]]) -> None:
    intent_ids = {_identity(row) for row in layers.get("intent", []) if _identity(row)}
    state_ids = {_identity(row) for row in layers.get("current_state", []) if _identity(row)}
    overlap = intent_ids & state_ids
    if overlap:
        raise Atlas3Error(
            "LAYER_COLLAPSE",
            "same identity classified as both intent and current_state",
        )
    for row in layers.get("intent", []):
        if row.get("layer") == "current_state":
            raise Atlas3Error(
                "INTENT_COLLAPSED_TO_STATE",
                "intent layer row must not carry current_state",
            )
    for row in layers.get("current_state", []):
        if row.get("layer") == "intent":
            raise Atlas3Error(
                "STATE_PRESENTED_AS_INTENT",
                "current_state layer row must not carry intent",
            )


def wrap_intent_state_honesty(
    items: list[dict[str, Any]],
    *,
    requested_project_id: str,
    owner_origin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail-closed INTENT vs CURRENT STATE wrapper. Never writes."""
    pid = requested_project_id.strip()
    if not pid:
        raise Atlas3Error("PROJECT_REQUIRED", "requested_project_id is required")
    _preflight_collapse(items)
    report = extract_intent_report(
        items,
        requested_project_id=pid,
        owner_origin=owner_origin,
    )
    layers = report.get("layers")
    if not isinstance(layers, dict):
        raise Atlas3Error("LAYER_COLLAPSE", "intent report layers are missing")
    report_pid = report.get("project_id")
    if report_pid is not None and str(report_pid) != pid:
        raise Atlas3Error(
            "CROSS_PROJECT",
            f"REQUESTED_PROJECT_ID={pid} ITEM_PROJECT_ID={report_pid}",
        )
    typed_layers: dict[str, list[dict[str, Any]]] = {}
    for name, rows in layers.items():
        if not isinstance(rows, list):
            raise Atlas3Error("LAYER_COLLAPSE", f"layer {name!r} must be a list")
        typed: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise Atlas3Error(
                    "INTENT_ITEM_INVALID",
                    f"layer {name!r} row[{index}] is not an object",
                )
            row_pid = row.get("project_id")
            if row_pid is not None and str(row_pid) != pid:
                raise Atlas3Error(
                    "CROSS_PROJECT",
                    f"REQUESTED_PROJECT_ID={pid} ITEM_PROJECT_ID={row_pid}",
                )
            typed.append(row)
        typed_layers[name] = typed
    _assert_layers_disjoint(typed_layers)
    honesty = report.get("honesty")
    if honesty is not None and not isinstance(honesty, dict):
        raise Atlas3Error("LAYER_COLLAPSE", "composed honesty block must be an object")
    if isinstance(honesty, dict) and honesty.get("intent_is_current_state") is True:
        raise Atlas3Error("INTENT_COLLAPSED_TO_STATE", "composed report collapsed intent")
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "generated": {"by": GENERATOR_ID},
        "composed_from": COMPOSED_FROM,
        "project_id": pid,
        "truth_boundary": TRUTH_BOUNDARY,
        "counts": report.get("counts"),
        "layers": typed_layers,
        "collapsed": False,
        "promoted_to_truth_core": 0,
        "write_applied": False,
        "honesty": {
            "intent_is_current_state": False,
            "current_state_is_intent": False,
            "layers_collapsed": False,
            "stale_is_current": False,
            "model_is_owner": False,
            "memory_is_truth_core": False,
            "promoted_to_truth_core": False,
            "write_applied": False,
            "MERGE_AUTHORIZATION": "NOT_GRANTED",
            "unknown_stays_unknown": True,
        },
    }
