"""Fail-closed project_id stems for internal vault-relative joins."""

from __future__ import annotations

import re
from pathlib import Path

# Same family as agent_identity.SAFE, without colon (Windows filename).
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def require_project_id(project_id: str) -> str:
    """AS-CTRL-INTERNAL-PID-F1: project_id is a filename stem, never a path."""
    if not isinstance(project_id, str) or not _SAFE.fullmatch(project_id):
        raise ValueError(f"unsafe project id: {project_id!r}")
    return project_id


def confined_file(root: Path, project_id: str, name: str) -> Path:
    token = require_project_id(project_id)
    base = Path(root).resolve()
    target = (Path(root) / name.format(project_id=token)).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"unsafe project id: {project_id!r}")
    return target
