"""AS-2.1-WEB-ACTIONS-001 - reconstructable web action transactions.

Records operator web actions as append-only transaction receipts.
Never mutates Layer B / claims / authority. UI != truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from project_atlas.authz import OperatorProfile, default_operator
from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-2.1-WEB-ACTIONS-001"
TRUTH_BOUNDARY = "WEB ACTION TXN != CANONICAL WRITE / UI!=TRUTH / != AUTHORITY"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MAX_LEDGER_TRANSACTIONS = 500
ActionType = Literal[
    "ask-query",
    "refresh-status",
    "open-lens",
    "queue-review",
    "acknowledge-finding",
]


class WebActionError(ValueError):
    """Fail-closed web action error."""


ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        "ask-query",
        "refresh-status",
        "open-lens",
        "queue-review",
        "acknowledge-finding",
    }
)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _ledger_path(vault: Path) -> Path:
    return vault / "generated" / "ops" / "web-actions" / "action-ledger.json"


def _payload_has_secrets(value: Any) -> bool:
    """Scan decoded payload strings. JSON \\u escapes are already resolved."""
    if isinstance(value, str):
        return bool(scan_text(value))
    if isinstance(value, dict):
        return any(_payload_has_secrets(item) for item in value.values())
    if isinstance(value, list):
        return any(_payload_has_secrets(item) for item in value)
    return False


def load_action_ledger(vault: Path) -> dict[str, Any]:
    """Load or initialize the reconstructable action ledger."""
    path = _ledger_path(vault)
    if path.is_file():
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("transactions")
        if isinstance(rows, list):
            # AS-SEC-SCAN-WEB-ACTIONS-LEDGER-JSON-ESC-001: rewrite of an
            # existing ledger must not persist decoded \\u payload secrets.
            raw["transactions"] = [
                row
                for row in rows
                if not (isinstance(row, dict) and _payload_has_secrets(row))
            ]
        return raw
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "transactions": [],
        "truth_boundary": TRUTH_BOUNDARY,
        "generated": {"by": "project-atlas"},
    }


def submit_web_action(
    vault: Path,
    *,
    action_id: str,
    action_type: ActionType,
    payload: dict[str, Any] | None = None,
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Append one reconstructable web action transaction."""
    require_compatibility_anchor()
    op = operator or default_operator()
    op.require("web.action")
    aid = action_id.strip()
    if not _ID_RE.fullmatch(aid):
        raise WebActionError("web-action-id-invalid")
    if action_type not in ALLOWED_ACTIONS:
        raise WebActionError(f"web-action-type-forbidden:{action_type}")
    body = payload or {}
    if any(k in body for k in ("promote", "authority", "claim_id", "vault_write")):
        raise WebActionError("web-action-authority-fields-forbidden")
    # AS-SEC-SCAN-WEBACT-JSON-001: json.loads decodes \u before persist.
    if _payload_has_secrets(body):
        raise WebActionError("web-action-secret-findings")
    txn = {
        "action_id": aid,
        "action_type": action_type,
        "payload": body,
        "operator_id": op.operator_id,
        "canonical_write": False,
        "authority": False,
    }
    txn["txn_hash"] = hashlib.sha256(
        json.dumps(txn, sort_keys=True).encode("utf-8")
    ).hexdigest()
    ledger = load_action_ledger(vault)
    txns = list(ledger.get("transactions") or [])
    if any(t.get("action_id") == aid for t in txns):
        raise WebActionError("web-action-id-duplicate")
    if len(txns) >= MAX_LEDGER_TRANSACTIONS:
        raise WebActionError("web-action-ledger-full")
    txns.append(txn)
    ledger["transactions"] = txns
    ledger["ledger_hash"] = hashlib.sha256(
        json.dumps(txns, sort_keys=True).encode("utf-8")
    ).hexdigest()
    ledger["web_actions_live"] = True
    ledger["max_transactions"] = MAX_LEDGER_TRANSACTIONS
    _atomic_write_json(_ledger_path(vault), ledger)
    return txn


def list_recent_actions(
    vault: Path,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Return the most recent reconstructable actions (read-only)."""
    if limit < 1 or limit > 200:
        raise WebActionError("web-action-limit-out-of-range")
    ledger = load_action_ledger(vault)
    txns = list(ledger.get("transactions") or [])
    recent = txns[-limit:]
    return {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "limit": limit,
        "count": len(recent),
        "transactions": recent,
        "truth_boundary": TRUTH_BOUNDARY,
        "authority": False,
        "generated": {"by": "project-atlas"},
    }
