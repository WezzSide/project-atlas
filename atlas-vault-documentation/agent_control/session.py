"""Machine-readable managed-agent session state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast


def path(root: Path, session_id: str) -> Path:
    return root / ".atlas" / "sessions" / f"{session_id}.json"


def save(root: Path, state: dict[str, Any]) -> Path:
    target = path(root, str(state["session"]["session_id"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return target


def load(root: Path, session_id: str) -> dict[str, Any]:
    target = path(root, session_id)
    if not target.is_file():
        raise ValueError(f"session not found: {session_id}")
    return cast(dict[str, Any], json.loads(target.read_text(encoding="utf-8")))
