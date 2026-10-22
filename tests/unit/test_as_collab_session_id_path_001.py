"""AS-COLLAB-SESSION-ID-PATH-001 — confine session_id before path interpolate.

``open_collab_session`` already applied ``_ID_RE``. Append and close did not,
so ``../../outside`` could target a file outside ``generated/ops/collab``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.collab_live import (
    CollabError,
    append_collab_action,
    close_collab_session,
    open_collab_session,
)


def test_append_rejects_traversing_session_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    planted = tmp_path / "outside-session.json"
    planted.write_text("{}", encoding="utf-8")
    with pytest.raises(CollabError, match="collab-session-id-invalid"):
        append_collab_action(
            vault,
            session_id="../outside",
            action_name="note",
            detail="should-not-write",
        )
    assert planted.read_text(encoding="utf-8") == "{}"


def test_close_rejects_traversing_session_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(CollabError, match="collab-session-id-invalid"):
        close_collab_session(vault, session_id="../../outside")


def test_honest_append_and_close_still_work(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    opened = open_collab_session(vault, session_id="sess-ok", subject="item")
    assert opened["session_id"] == "sess-ok"
    updated = append_collab_action(
        vault, session_id="sess-ok", action_name="note", detail="ok"
    )
    assert len(updated["actions"]) == 2
    closed = close_collab_session(vault, session_id="sess-ok")
    assert closed["closed"] is True
    path = vault / "generated" / "ops" / "collab" / "sess-ok-session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["closed"] is True
