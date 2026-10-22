"""AS-2.1-AUTHZ-001 - local operator capability authorization.

Fail-closed capability checks for live 2.1 surfaces. No network identity
provider in this package — explicit operator profile only.

SEC-009: loopback is NOT authentication. API callers must present a
high-entropy per-launch session credential that maps to an explicit
OperatorProfile (request principal).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor

PACKAGE_ID = "AS-2.1-AUTHZ-001"
# SEC-ADV004-B-001 / CODEX-SEC-019 spirit: CLI elevation is never self-granted.
CLI_ELEVATE_CAPS_ENV: Final[str] = "ATLAS_CLI_ELEVATE_CAPS"
# SEC-ADV004-B-002: preferred sink for per-launch READ bearer (not stderr logs).
API_TOKEN_FILE_ENV: Final[str] = "ATLAS_API_TOKEN_FILE"
API_PRIVILEGED_TOKEN_FILE_ENV: Final[str] = "ATLAS_API_PRIVILEGED_TOKEN_FILE"
Capability = Literal[
    "api.read",
    "web.read",
    "mcp.read",
    "scheduler.arm",
    "scheduler.dispatch",
    "oai.import",
    "oai.responses",
    "pilot.scan",
    "autonomy.l3",
    "vault.write",
    "chatgpt.bridge",
    "collab.session",
    "web.action",
    "provider.live",
]

ALL_CAPABILITIES: Final[frozenset[Capability]] = frozenset(
    {
        "api.read",
        "web.read",
        "mcp.read",
        "scheduler.arm",
        "scheduler.dispatch",
        "oai.import",
        "oai.responses",
        "pilot.scan",
        "autonomy.l3",
        "vault.write",
        "chatgpt.bridge",
        "collab.session",
        "web.action",
        "provider.live",
    }
)

# Default local operator: read surfaces + import/pilot/collab/chatgpt/oai.responses;
# no write/L3/dispatch/provider.live/web.action.
DEFAULT_OPERATOR_CAPS: Final[frozenset[Capability]] = frozenset(
    {
        "api.read",
        "web.read",
        "mcp.read",
        "oai.import",
        "oai.responses",
        "pilot.scan",
        "scheduler.arm",
        "chatgpt.bridge",
        "collab.session",
    }
)

# Caps that a read session credential may carry (SEC-009: read MUST NOT mutate).
READ_ONLY_CAPABILITIES: Final[frozenset[Capability]] = frozenset(
    {
        "api.read",
        "web.read",
        "mcp.read",
        "oai.import",
        "oai.responses",
        "pilot.scan",
        "scheduler.arm",
        "chatgpt.bridge",
        "collab.session",
    }
)

# Privileged / mutating capabilities require an explicit privileged credential.
PRIVILEGED_CAPABILITIES: Final[frozenset[Capability]] = frozenset(
    {
        "web.action",
        "vault.write",
        "autonomy.l3",
        "scheduler.dispatch",
        "provider.live",
    }
)

_SESSION_TOKEN_BYTES: Final[int] = 32
# receipt_id is interpolated into generated/ops/authz/{receipt_id}.json;
# require a bare safe token so it can never steer a write outside that dir.
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class AuthzError(PermissionError):
    """Fail-closed authorization error."""


class ApiAuthError(PermissionError):
    """Fail-closed API request-principal authentication error (SEC-009)."""


@dataclass(frozen=True, slots=True)
class OperatorProfile:
    """Explicit local operator capability set."""

    operator_id: str
    capabilities: frozenset[Capability]
    package_id: str = PACKAGE_ID

    def allows(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        if capability not in ALL_CAPABILITIES:
            raise AuthzError(f"authz-unknown-capability:{capability}")
        if not self.allows(capability):
            raise AuthzError(f"authz-denied:{capability}")


@dataclass(frozen=True, slots=True)
class ApiSessionCredentials:
    """Per-launch API session credentials bound to request principals (SEC-009)."""

    read_token: str
    read_operator: OperatorProfile
    privileged_token: str | None = None
    privileged_operator: OperatorProfile | None = None

    def authorization_header(self, *, privileged: bool = False) -> str:
        if privileged:
            if self.privileged_token is None:
                raise ApiAuthError("auth-privileged-credential-unavailable")
            return f"Bearer {self.privileged_token}"
        return f"Bearer {self.read_token}"

    def auth_headers(self, *, privileged: bool = False) -> dict[str, str]:
        return {"Authorization": self.authorization_header(privileged=privileged)}


@dataclass
class ApiSessionStore:
    """In-memory token→principal map for one LIVE_API process launch."""

    credentials: ApiSessionCredentials
    _token_digest_to_operator: dict[bytes, OperatorProfile] = field(
        default_factory=dict, repr=False
    )

    def resolve_bearer(self, authorization_header: str | None) -> OperatorProfile:
        """Resolve Authorization Bearer to a bound OperatorProfile.

        unauthenticated → DENY; wrong credential → DENY (SEC-009).
        """
        if authorization_header is None or not authorization_header.strip():
            raise ApiAuthError("auth-required")
        raw = authorization_header.strip()
        if not raw.lower().startswith("bearer "):
            raise ApiAuthError("auth-invalid")
        token = raw[7:].strip()
        if not token:
            raise ApiAuthError("auth-invalid")
        digest = _token_digest(token)
        for stored_digest, operator in self._token_digest_to_operator.items():
            if hmac.compare_digest(stored_digest, digest):
                return operator
        raise ApiAuthError("auth-invalid")


def default_operator(operator_id: str = "local-operator") -> OperatorProfile:
    """Return the default supervised local operator profile."""
    return OperatorProfile(operator_id=operator_id, capabilities=DEFAULT_OPERATOR_CAPS)


def elevated_operator(
    operator_id: str,
    *,
    extra: set[Capability] | frozenset[Capability],
) -> OperatorProfile:
    """Build an elevated profile; vault.write / autonomy.l3 must be explicit."""
    caps = set(DEFAULT_OPERATOR_CAPS)
    caps.update(extra)
    unknown = caps - set(ALL_CAPABILITIES)
    if unknown:
        raise AuthzError(f"authz-unknown-capability:{sorted(unknown)[0]}")
    return OperatorProfile(operator_id=operator_id, capabilities=frozenset(caps))


def require_cli_elevated_operator(
    operator_id: str,
    *,
    required: set[Capability] | frozenset[Capability],
) -> OperatorProfile:
    """Fail-closed CLI elevation gate (SEC-ADV004-B-001).

    LIVE CLI must not mint privileged caps inline. Operators set
    ``ATLAS_CLI_ELEVATE_CAPS`` to an explicit comma-separated allow-list
    covering every required capability; otherwise elevation is refused.
    """
    needed = frozenset(required)
    if not needed:
        return default_operator(operator_id)
    unknown = needed - ALL_CAPABILITIES
    if unknown:
        raise AuthzError(f"authz-unknown-capability:{sorted(unknown)[0]}")
    raw = os.environ.get(CLI_ELEVATE_CAPS_ENV, "").strip()
    if not raw:
        raise AuthzError(
            "authz-cli-elevation-required:" + ",".join(sorted(needed))
        )
    granted = {part.strip() for part in raw.split(",") if part.strip()}
    missing = sorted(cap for cap in needed if cap not in granted)
    if missing:
        raise AuthzError(
            "authz-cli-elevation-incomplete:" + ",".join(missing)
        )
    return elevated_operator(operator_id, extra=set(needed))


def read_only_operator(operator_id: str = "api-read") -> OperatorProfile:
    """Return a read-only API principal (no privileged / mutating caps)."""
    return OperatorProfile(operator_id=operator_id, capabilities=READ_ONLY_CAPABILITIES)


def mint_api_session(
    launch_operator: OperatorProfile | None = None,
) -> ApiSessionStore:
    """Mint high-entropy per-launch credentials bound to explicit principals.

    Always issues a read credential (READ ONLY). When ``launch_operator``
    carries any privileged capability, also issues a distinct privileged
    credential bound to that operator (SEC-009).
    """
    base = launch_operator or default_operator()
    read_op = OperatorProfile(
        operator_id=f"{base.operator_id}-read",
        capabilities=frozenset(base.capabilities & READ_ONLY_CAPABILITIES)
        or READ_ONLY_CAPABILITIES,
    )
    if not read_op.allows("api.read"):
        read_op = OperatorProfile(
            operator_id=read_op.operator_id,
            capabilities=frozenset(set(read_op.capabilities) | {"api.read"}),
        )
    read_token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
    privileged_token: str | None = None
    privileged_op: OperatorProfile | None = None
    if base.capabilities & PRIVILEGED_CAPABILITIES:
        privileged_token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
        privileged_op = base
    creds = ApiSessionCredentials(
        read_token=read_token,
        read_operator=read_op,
        privileged_token=privileged_token,
        privileged_operator=privileged_op,
    )
    store = ApiSessionStore(credentials=creds)
    store._token_digest_to_operator[_token_digest(read_token)] = read_op
    if privileged_token is not None and privileged_op is not None:
        store._token_digest_to_operator[_token_digest(privileged_token)] = privileged_op
    return store


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="ascii", newline="\n")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def publish_api_session_credentials(
    creds: ApiSessionCredentials,
    *,
    stderr_isatty: bool | None = None,
) -> None:
    """Publish SEC-009 session credentials without dumping into shared logs.

    SEC-ADV004-B-002: prefer ``ATLAS_API_TOKEN_FILE``; when stderr is not a
    TTY and no file sink is set, print a redacted marker only (fail-closed
    against world-readable redirect logs).
    """
    tty = sys.stderr.isatty() if stderr_isatty is None else stderr_isatty
    token_file = os.environ.get(API_TOKEN_FILE_ENV, "").strip()
    priv_file = os.environ.get(API_PRIVILEGED_TOKEN_FILE_ENV, "").strip()

    if token_file:
        _write_token_file(Path(token_file), creds.read_token)
        print(f"ATLAS_API_READ_TOKEN_FILE={token_file}", file=sys.stderr)
        print("ATLAS_API_READ_TOKEN=[redacted]", file=sys.stderr)
    elif tty:
        print(f"ATLAS_API_READ_TOKEN={creds.read_token}", file=sys.stderr)
    else:
        print(
            "ATLAS_API_READ_TOKEN=[redacted; set ATLAS_API_TOKEN_FILE to capture]",
            file=sys.stderr,
        )

    if creds.privileged_token:
        if priv_file:
            _write_token_file(Path(priv_file), creds.privileged_token)
            print(
                f"ATLAS_API_PRIVILEGED_TOKEN_FILE={priv_file}",
                file=sys.stderr,
            )
            print("ATLAS_API_PRIVILEGED_TOKEN=[redacted]", file=sys.stderr)
        elif tty:
            print(
                f"ATLAS_API_PRIVILEGED_TOKEN={creds.privileged_token}",
                file=sys.stderr,
            )
        else:
            print(
                "ATLAS_API_PRIVILEGED_TOKEN=[redacted; set "
                "ATLAS_API_PRIVILEGED_TOKEN_FILE to capture]",
                file=sys.stderr,
            )
    else:
        print(
            "ATLAS_API_PRIVILEGED_TOKEN=(none; start with elevated "
            "operator for privileged actions)",
            file=sys.stderr,
        )


def write_authz_audit_receipt(
    vault: Path,
    *,
    receipt_id: str = "authz-audit",
    operator: OperatorProfile | None = None,
) -> dict[str, Any]:
    """Persist a reconstructable capability audit receipt (not authority)."""
    require_compatibility_anchor()
    rid = receipt_id.strip()
    if not _ID_RE.fullmatch(rid):
        raise AuthzError("authz-receipt-id-invalid")
    op = operator or default_operator()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "receipt_id": rid,
        "operator_id": op.operator_id,
        "capabilities": sorted(op.capabilities),
        "all_capabilities": sorted(ALL_CAPABILITIES),
        "denied_by_default": sorted(ALL_CAPABILITIES - op.capabilities),
        "authority": False,
        "generated": {"by": "project-atlas"},
    }
    path = vault / "generated" / "ops" / "authz" / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)
    return payload
