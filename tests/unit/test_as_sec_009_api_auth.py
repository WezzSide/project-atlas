"""SEC-009 — LIVE_API request principal / session credential auth.

Loopback is NOT authentication. Unauthenticated and wrong-credential
requests must DENY. Read credential is READ ONLY; privileged actions
require an explicit privileged credential.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.authz import (
    PRIVILEGED_CAPABILITIES,
    READ_ONLY_CAPABILITIES,
    elevated_operator,
    mint_api_session,
)
from project_atlas.web_actions import load_action_ledger


def _wait_ready(host: str, port: int) -> None:
    """Wait until the loopback server accepts. HTTPError means it answered."""
    deadline = time.monotonic() + 5
    last: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://{host}:{port}/v1/meta", timeout=0.5):
                return
        except HTTPError as exc:
            exc.close()
            return
        except OSError as exc:
            last = exc
            time.sleep(0.05)
    raise AssertionError(f"LIVE_API did not accept on {host}:{port}: {last}")


def _start(vault: Path, *, operator=None):
    server = serve_api(vault, host="127.0.0.1", port=0, operator=operator)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_ready(str(host), int(port))
    return server, str(host), int(port)


def _request(
    host: str,
    port: int,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, dict[str, Any]]:
    req = Request(
        f"http://{host}:{port}{path}",
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urlopen(req, timeout=5) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def test_sec009_wait_ready_treats_http_error_as_ready(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server, host, port = _start(vault)
    try:
        _wait_ready(host, port)
    finally:
        server.shutdown()


def test_sec009_unauthenticated_denied(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server, host, port = _start(vault)
    try:
        code, body = _request(host, port, "/v1/meta")
        assert code == 401
        assert body["error"] == "auth-required"
        code2, body2 = _request(
            host,
            port,
            "/v1/actions",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=b'{"action_id":"x","action_type":"refresh-status","payload":{}}',
        )
        assert code2 == 401
        assert body2["error"] == "auth-required"
        assert load_action_ledger(vault)["transactions"] == []
    finally:
        server.shutdown()


def test_sec009_wrong_credential_denied(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server, host, port = _start(vault)
    try:
        code, body = _request(
            host,
            port,
            "/v1/meta",
            headers={"Authorization": "Bearer totally-wrong-token"},
        )
        assert code == 401
        assert body["error"] == "auth-invalid"
    finally:
        server.shutdown()


def test_sec009_read_credential_read_only_no_mutate(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("sec009", extra={"web.action"})
    server, host, port = _start(vault, operator=op)
    creds = session_credentials(server)
    try:
        assert creds.privileged_token is not None
        code, body = _request(host, port, "/v1/meta", headers=creds.auth_headers())
        assert code == 200
        assert body["session_auth"] is True
        assert body["operator_id"].endswith("-read")
        # Read principal must not carry privileged caps.
        code_a, authz = _request(host, port, "/v1/authz", headers=creds.auth_headers())
        assert code_a == 200
        caps = set(authz["capabilities"])
        assert "api.read" in caps
        assert caps.isdisjoint(PRIVILEGED_CAPABILITIES)
        code_p, body_p = _request(
            host,
            port,
            "/v1/actions",
            method="POST",
            headers={
                "Content-Type": "application/json",
                **creds.auth_headers(),
            },
            data=json.dumps(
                {
                    "action_id": "read-mut",
                    "action_type": "refresh-status",
                    "payload": {},
                }
            ).encode("utf-8"),
        )
        assert code_p == 400
        assert "authz-denied:web.action" in body_p["error"]
        assert load_action_ledger(vault)["transactions"] == []
    finally:
        server.shutdown()


def test_sec009_privileged_credential_explicit_capability(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("sec009-priv", extra={"web.action"})
    server, host, port = _start(vault, operator=op)
    creds = session_credentials(server)
    try:
        code, body = _request(
            host,
            port,
            "/v1/actions",
            method="POST",
            headers={
                "Content-Type": "application/json",
                **creds.auth_headers(privileged=True),
            },
            data=json.dumps(
                {
                    "action_id": "priv-ok",
                    "action_type": "refresh-status",
                    "payload": {},
                }
            ).encode("utf-8"),
        )
        assert code == 200
        assert body["accepted"] is True
        assert len(load_action_ledger(vault)["transactions"]) == 1
    finally:
        server.shutdown()


def test_sec009_host_gate_still_enforced(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server, host, port = _start(vault)
    creds = session_credentials(server)
    try:
        code, body = _request(
            host,
            port,
            "/v1/meta",
            headers={
                "Host": "attacker.example",
                **creds.auth_headers(),
            },
        )
        assert code == 403
        assert body["error"] == "host-non-local-forbidden"
    finally:
        server.shutdown()


def test_sec009_mint_high_entropy_distinct_tokens() -> None:
    store_a = mint_api_session(elevated_operator("a", extra={"web.action"}))
    store_b = mint_api_session(elevated_operator("b", extra={"web.action"}))
    ca = store_a.credentials
    cb = store_b.credentials
    assert len(ca.read_token) >= 32
    assert ca.read_token != ca.privileged_token
    assert ca.read_token != cb.read_token
    assert ca.privileged_token != cb.privileged_token
    assert set(ca.read_operator.capabilities).issubset(READ_ONLY_CAPABILITIES)
    priv_caps = ca.privileged_operator.capabilities if ca.privileged_operator else set()
    assert "web.action" in priv_caps


def test_sec009_default_launch_has_no_privileged_token(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server, _host, _port = _start(vault)
    try:
        creds = session_credentials(server)
        assert creds.privileged_token is None
        with pytest.raises(Exception, match="privileged-credential-unavailable"):
            creds.authorization_header(privileged=True)
    finally:
        server.shutdown()
