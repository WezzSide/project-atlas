"""Machine-readable managed-agent session state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .agent_identity import SAFE


def path(root: Path, session_id: str) -> Path:
    """Resolve a session file under ``<root>/.atlas/sessions/``.

    AS-CTRL-SESSION-F1: ``session_id`` is a single safe filename stem, never a
    relative path. Authentic ids may contain ``:`` (``AS-<stamp>-...``); they
    must still stay inside the sessions directory after resolve.
    """
    if not isinstance(session_id, str) or not SAFE.fullmatch(session_id):
        raise ValueError(f"unsafe session id: {session_id!r}")
    root_path = Path(root)
    sessions = (root_path / ".atlas" / "sessions").resolve()
    target = (root_path / ".atlas" / "sessions" / f"{session_id}.json").resolve()
    if not target.is_relative_to(sessions):
        raise ValueError(f"unsafe session id: {session_id!r}")
    return target


def save(root: Path, state: dict[str, Any]) -> Path:
    target = path(root, str(state["session"]["session_id"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def load(root: Path, session_id: str) -> dict[str, Any]:
    target = path(root, session_id)
    if not target.is_file():
        raise ValueError(f"session not found: {session_id}")
    return cast(dict[str, Any], json.loads(target.read_text(encoding="utf-8")))
