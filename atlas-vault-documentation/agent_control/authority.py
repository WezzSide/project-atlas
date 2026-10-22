"""Independently issued, integrity-protected, revocable authority grants.

CODEX-SEC-015 / SEC-016 / SEC-019:

Separation of concerns (must not collapse):
  REQUEST != GRANT != AUTHORIZATION != EXECUTION

A session receipt is evidence of work, never authority.
CLI selection of a privileged operation must not create the grant that
approves that same operation. Grants are HMAC-protected under an issuer
secret that is independent of attacker-controlled receipt JSON.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from .agent_identity import SAFE


PURPOSE_PROMOTE_READINESS = "promote-readiness"
ISSUER_ENV = "ATLAS_AUTHORITY_ISSUER_KEY"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def resolve_issuer_key(explicit: str | None = None) -> bytes:
    value = explicit if explicit is not None else os.environ.get(ISSUER_ENV, "")
    if not value or len(value) < 32:
        raise ValueError(
            "authority issuer key missing or too short "
            f"(set {ISSUER_ENV} to a high-entropy secret >= 32 chars)"
        )
    return value.encode("utf-8")


def mac_for(payload: dict[str, Any], issuer_key: bytes) -> str:
    body = {key: value for key, value in payload.items() if key != "mac"}
    return hmac.new(issuer_key, _canonical(body), hashlib.sha256).hexdigest()


def issue_grant(
    *,
    store: Path,
    purpose: str,
    adapter_id: str,
    skill_id: str,
    skill_version: str,
    skill_sha256: str,
    issuer_id: str,
    requester_id: str | None = None,
    evidence_receipt_id: str | None = None,
    issuer_key: str | None = None,
) -> dict[str, Any]:
    """Issue an independent GRANT. Issuer must not equal requester (SEC-019)."""
    if purpose != PURPOSE_PROMOTE_READINESS:
        raise ValueError(f"unsupported authority purpose: {purpose}")
    if not issuer_id or issuer_id.strip() == "":
        raise ValueError("authority issuer_id is required")
    if requester_id and issuer_id == requester_id:
        raise ValueError("issuer must not equal requester (REQUEST != GRANT)")
    key = resolve_issuer_key(issuer_key)
    grant_id = "AAG-" + secrets.token_hex(8)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "grant_type": "atlas-authority-grant",
        "grant_id": grant_id,
        "purpose": purpose,
        "subject": {
            "adapter_id": adapter_id,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "skill_sha256": skill_sha256,
        },
        "issuer_id": issuer_id,
        "requester_id": requester_id,
        "evidence_receipt_id": evidence_receipt_id,
        "revoked": False,
        # Explicit: bound receipt is evidence only; grant is the authority.
        "authority_role": "grant",
        "receipt_is_authority": False,
    }
    payload["mac"] = mac_for(payload, key)
    target = _safe_grant_file(store, grant_id, folder="grants")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.is_file():
        raise ValueError("authority grant id collision")
    target.write_text(content, encoding="utf-8")
    return payload


def _safe_grant_file(store: Path, grant_id: str, *, folder: str) -> Path:
    """AS-CTRL-GRANT-F1: grant_id is a filename stem, never a relative path."""
    if not isinstance(grant_id, str) or not SAFE.fullmatch(grant_id):
        raise ValueError(f"unsafe grant id: {grant_id!r}")
    root = Path(store)
    folder_path = (root / folder).resolve()
    target = (root / folder / f"{grant_id}.json").resolve()
    if not target.is_relative_to(folder_path):
        raise ValueError(f"unsafe grant id: {grant_id!r}")
    return target


def revoke_grant(*, store: Path, grant_id: str, issuer_key: str | None = None) -> dict[str, Any]:
    key = resolve_issuer_key(issuer_key)
    path = _safe_grant_file(store, grant_id, folder="grants")
    if not path.is_file():
        raise ValueError(f"authority grant not found: {grant_id}")
    grant = json.loads(path.read_text(encoding="utf-8"))
    expected = mac_for(grant, key)
    if not hmac.compare_digest(str(grant.get("mac", "")), expected):
        raise ValueError("authority grant integrity check failed")
    grant["revoked"] = True
    grant["mac"] = mac_for(grant, key)
    path.write_text(json.dumps(grant, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rev = _safe_grant_file(store, grant_id, folder="revocations")
    rev.parent.mkdir(parents=True, exist_ok=True)
    rev.write_text(
        json.dumps({"grant_id": grant_id, "revoked": True}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return grant


def load_grant(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("authority grant is missing")
    grant = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(grant, dict):
        raise ValueError("authority grant is malformed")
    return grant


def verify_grant(
    path: Path,
    *,
    purpose: str,
    adapter_id: str,
    skill_id: str,
    skill_version: str,
    skill_sha256: str,
    requester_id: str | None = None,
    evidence_receipt_id: str | None = None,
    issuer_key: str | None = None,
    store: Path | None = None,
) -> dict[str, Any]:
    """Authorize EXECUTION of a privileged action using an independent GRANT.

    Rejects:
    - missing / malformed / MAC-invalid grants
    - revoked grants
    - purpose or subject mismatch
    - issuer == requester (self-approval / SEC-019)
    - receipt treated as authority (SEC-016)
    """
    key = resolve_issuer_key(issuer_key)
    grant = load_grant(path)
    if grant.get("grant_type") != "atlas-authority-grant":
        raise ValueError("not an atlas authority grant")
    if grant.get("receipt_is_authority") is True:
        raise ValueError("self-asserted receipt is not authority")
    if bool(grant.get("revoked")):
        raise ValueError("authority grant is revoked")
    if store is not None:
        rev = store / "revocations" / f"{grant.get('grant_id')}.json"
        if rev.is_file():
            raise ValueError("authority grant is revoked")
    expected = mac_for(grant, key)
    if not hmac.compare_digest(str(grant.get("mac", "")), expected):
        raise ValueError("authority grant integrity check failed")
    if grant.get("purpose") != purpose:
        raise ValueError("authority grant purpose mismatch")
    subject = grant.get("subject") if isinstance(grant.get("subject"), dict) else {}
    if (
        subject.get("adapter_id") != adapter_id
        or subject.get("skill_id") != skill_id
        or subject.get("skill_version") != skill_version
        or subject.get("skill_sha256") != skill_sha256
    ):
        raise ValueError("authority grant subject mismatch")
    issuer_id = str(grant.get("issuer_id") or "")
    if not issuer_id:
        raise ValueError("authority grant missing issuer")
    if requester_id and issuer_id == requester_id:
        raise ValueError("CLI/session cannot approve its own request (REQUEST != GRANT)")
    bound_requester = grant.get("requester_id")
    if bound_requester and requester_id and bound_requester != requester_id:
        raise ValueError("authority grant requester mismatch")
    bound_receipt = grant.get("evidence_receipt_id")
    if bound_receipt and evidence_receipt_id and bound_receipt != evidence_receipt_id:
        raise ValueError("authority grant evidence receipt mismatch")
    return grant
