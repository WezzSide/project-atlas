"""AS-2.1 Host/CORS ADV matrix deepen (release-hardening)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import (
    CORS_ORIGIN,
    DEFAULT_CORS_ORIGIN,
    ApiServerError,
    resolve_cors_origin,
    serve_api,
    session_credentials,
)


def test_adv_options_cors_headers(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request(
            f"http://{host}:{port}/v1/meta",
            method="OPTIONS",
            headers={"Origin": CORS_ORIGIN},
        )
        with urlopen(req, timeout=2) as resp:
            assert resp.status == 204
            assert resp.headers.get("Access-Control-Allow-Origin") == CORS_ORIGIN
            methods = resp.headers.get("Access-Control-Allow-Methods", "")
            assert "GET" in methods
            assert "POST" in methods
            allow_headers = resp.headers.get("Access-Control-Allow-Headers", "")
            assert "Authorization" in allow_headers
            assert "Content-Type" in allow_headers
    finally:
        server.shutdown()


def test_adv_get_rejects_evil_host(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = Request(
            f"http://{host}:{port}/v1/health",
            headers={"Host": "attacker.example"},
        )
        with pytest.raises(HTTPError) as excinfo:
            urlopen(req, timeout=2)
        assert excinfo.value.code == 403
        body = json.loads(excinfo.value.read().decode("utf-8"))
        assert body["error"] == "host-non-local-forbidden"
    finally:
        server.shutdown()


def test_adv_local_host_with_port_allowed(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth = session_credentials(server).auth_headers()
        req = Request(
            f"http://{host}:{port}/v1/meta",
            headers={"Host": f"127.0.0.1:{port}", **auth},
        )
        with urlopen(req, timeout=2) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        assert meta["live_api"] is True
    finally:
        server.shutdown()


def test_resolve_cors_origin_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROD-ADV-011: ATLAS_CORS_ORIGIN overrides default 5173 for session WebPort."""
    monkeypatch.delenv("ATLAS_CORS_ORIGIN", raising=False)
    assert resolve_cors_origin() == DEFAULT_CORS_ORIGIN
    assert resolve_cors_origin("") == DEFAULT_CORS_ORIGIN
    monkeypatch.setenv("ATLAS_CORS_ORIGIN", "http://127.0.0.1:18241")
    assert resolve_cors_origin() == "http://127.0.0.1:18241"
    assert resolve_cors_origin("http://127.0.0.1:18080") == "http://127.0.0.1:18080"


def test_resolve_cors_origin_rejects_non_local() -> None:
    with pytest.raises(ApiServerError, match="cors-origin-non-local-forbidden"):
        resolve_cors_origin("https://evil.example")
    with pytest.raises(ApiServerError, match="cors-origin-non-local-forbidden"):
        resolve_cors_origin("http://evil.example:5173")
    with pytest.raises(ApiServerError, match="cors-origin-non-local-forbidden"):
        resolve_cors_origin("*")


def test_adv_meta_cors_origin_follows_webport_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PROD-ADV-011: /v1/meta + ACAO advertise session WebPort origin, not hardcoded 5173."""
    monkeypatch.setenv("ATLAS_CORS_ORIGIN", "http://127.0.0.1:18241")
    vault = tmp_path / "v"
    vault.mkdir()
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth = session_credentials(server).auth_headers()
        req = Request(
            f"http://{host}:{port}/v1/meta",
            headers={"Origin": "http://127.0.0.1:18241", **auth},
        )
        with urlopen(req, timeout=2) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == ("http://127.0.0.1:18241")
            meta = json.loads(resp.read().decode("utf-8"))
        assert meta["cors_origin"] == "http://127.0.0.1:18241"
        assert meta["cors_origin"] != "http://127.0.0.1:5173"
    finally:
        server.shutdown()
