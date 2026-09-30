"""AS-2.1-COLLAB-001 - reconstructable collaboration sessions.

Creates local collaboration session receipts (review-queue / shared-receipt).
Not a multi-user network plane. Reconstructable actions only; never authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from project_atlas.authz import OperatorProfile, default_operator
from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor

PACKAGE_ID = "AS-2.1-COLLAB-001"
TRUTH_BOUNDARY = "COLLAB SESSION != MULTIUSER PLANE / != AUTHORITY"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SessionKind = Literal["review-queue", "shared-receipt", "comment-thread"]


class CollabError(ValueError):
    """Fail-closed collaboration error."""


def _require_session_id(session_id: str) -> str:
    """Confine session_id before it is interpolated into a vault path.

    AS-COLLAB-SESSION-ID-PATH-001: ``open_collab_session`` already applied
    ``_ID_RE``. ``append_collab_action`` / ``close_collab_session`` did
    not, so ``../../outside`` could target a file outside
    ``generated/ops/collab``.
    """
    sid = session_id.strip()
    if not _ID_RE.fullmatch(sid):
        raise CollabError("collab-session-id-invalid")
    return sid


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def open_collab_session(
    vault: Path,
    *,
    session_id: str,
    kind: SessionKind = "review-queue",
    subject: str,
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Open a reconstructable local collaboration session receipt."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("collab.session")
    sid = _require_session_id(session_id)
    if kind not in {"review-queue", "shared-receipt"}:
        raise CollabError(f"collab-kind-not-enabled:{kind}")
    subj = subject.strip()
    if not subj or len(subj) > 256:
        raise CollabError("collab-subject-invalid")
    action = {
        "action": "open-session",
        "kind": kind,
        "subject": subj,
        "operator_id": op.operator_id,
    }
    action_hash = hashlib.sha256(
        json.dumps(action, sort_keys=True).encode("utf-8")
    ).hexdigest()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "session_id": sid,
        "kind": kind,
        "subject": subj,
        "live_collab": True,
        "network_multiuser": False,
        "closed": False,
        "actions": [action],
        "action_hash": action_hash,
        "operator_id": op.operator_id,
        "truth_boundary": TRUTH_BOUNDARY,
        "authority": {
            "level": "derived",
            "note": "Collab sessions are reconstructable receipts only",
        },
        "generated": {"by": "project-atlas"},
    }
    out = vault / "generated" / "ops" / "collab" / f"{sid}-session.json"
    _atomic_write_json(out, payload)
    return payload


def append_collab_action(
    vault: Path,
    *,
    session_id: str,
    action_name: str,
    detail: str,
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Append a reconstructable action to an existing collab session."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("collab.session")
    sid = _require_session_id(session_id)
    path = vault / "generated" / "ops" / "collab" / f"{sid}-session.json"
    if not path.is_file():
        raise CollabError("collab-session-missing")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("closed") is True:
        raise CollabError("collab-session-closed")
    name = action_name.strip()
    if not re.fullmatch(r"^[a-z][a-z0-9-]{0,63}$", name):
        raise CollabError("collab-action-name-invalid")
    entry = {
        "action": name,
        "detail": detail.strip()[:512],
        "operator_id": op.operator_id,
    }
    actions = list(payload.get("actions") or [])
    actions.append(entry)
    payload["actions"] = actions
    payload["action_hash"] = hashlib.sha256(
        json.dumps(actions, sort_keys=True).encode("utf-8")
    ).hexdigest()
    _atomic_write_json(path, payload)
    return payload


def close_collab_session(
    vault: Path,
    *,
    session_id: str,
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Close a collab session (append-only close action; no network plane)."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("collab.session")
    sid = _require_session_id(session_id)
    path = vault / "generated" / "ops" / "collab" / f"{sid}-session.json"
    if not path.is_file():
        raise CollabError("collab-session-missing")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("closed") is True:
        raise CollabError("collab-session-already-closed")
    entry = {"action": "close-session", "operator_id": op.operator_id}
    actions = list(payload.get("actions") or [])
    actions.append(entry)
    payload["actions"] = actions
    payload["closed"] = True
    payload["action_hash"] = hashlib.sha256(
        json.dumps(actions, sort_keys=True).encode("utf-8")
    ).hexdigest()
    _atomic_write_json(path, payload)
    return payload
