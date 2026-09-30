"""AT3-042 — Cross-LLM conflict detection. Does not collapse state/intent/history."""

from __future__ import annotations

import re
from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error

PACKAGE_ID: Final[str] = "AT3-042"
_PG = re.compile(r"postgresql?\s*(\d+)", re.I)
_INTENT = re.compile(r"\b(planned|later|after|migrate|migration)\b", re.I)


def conflict_capability() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "collapsed_to_scalar": False,
        "picks_winner": False,
        "collapses_state_intent_history": False,
        "auto_promote_to_truth_core": False,
    }


def detect_conflicts(
    items: list[dict[str, Any]],
    *,
    current_state_text: str | None = None,
) -> dict[str, Any]:
    if not isinstance(items, list):
        raise Atlas3Error("CONFLICT_INVALID", "items must be a list")
    versions: dict[str, list[str]] = {}
    intents: list[str] = []
    scoped: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise Atlas3Error("CONFLICT_INVALID", "item is not an object")
        explicit = item.get("project_id")
        if explicit is not None and str(explicit).strip():
            scoped.add(str(explicit))
        if len(scoped) > 1:
            raise Atlas3Error(
                "PROJECT_MISMATCH",
                f"mixed-project conflict batch: {sorted(scoped)}",
            )
        text = str(item.get("text") or "")
        match = _PG.search(text)
        if not match:
            continue
        version = match.group(1)
        provider = str(item.get("provider") or "unknown")
        if _INTENT.search(text):
            intents.append(f"{provider}:{version}")
        else:
            versions.setdefault(version, []).append(provider)

    current = None
    if current_state_text:
        found = _PG.search(current_state_text)
        if found:
            current = found.group(1)

    conflicted = len(versions) > 1 or (
        current is not None and any(version != current for version in versions)
    )
    return {
        "package": PACKAGE_ID,
        "current_state": current,
        "observed_versions": {key: sorted(set(value)) for key, value in sorted(versions.items())},
        "intent_versions": sorted(set(intents)),
        "conflicted_history": conflicted,
        "collapsed_to_scalar": False,
        "winner": None,
    }
