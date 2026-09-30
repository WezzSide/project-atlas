"""AS-2.1 Track B deepen-003: API/MCP/Web/actions/perf ADV."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.authz import elevated_operator
from project_atlas.mcp_server import invoke_mcp_tool, list_mcp_tools
from project_atlas.perf_baselines import run_perf_baselines
from project_atlas.web_actions import (
    WebActionError,
    list_recent_actions,
    submit_web_action,
)


def test_mcp_projects_list_tool(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    listing = list_mcp_tools()
    assert "atlas.projects.list.read" in listing["tools"]
    assert listing["write_tools"] == []
    report = invoke_mcp_tool(vault, "atlas.projects.list.read")
    assert report["live_mcp_read"] is True
    assert "projects" in report["result"]


def test_api_obs_authz_and_limits(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        auth = session_credentials(server).auth_headers()
        with urlopen(Request(f"http://{host}:{port}/v1/meta", headers=auth), timeout=2) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        assert meta["obs_live"] is True
        assert meta["authz_profile"] is True
        with urlopen(Request(f"http://{host}:{port}/v1/obs", headers=auth), timeout=2) as resp:
            obs = json.loads(resp.read().decode("utf-8"))
        assert obs["authority_plane"] == "none"
        with urlopen(Request(f"http://{host}:{port}/v1/authz", headers=auth), timeout=2) as resp:
            authz = json.loads(resp.read().decode("utf-8"))
        assert authz["authority"] is False
        assert "api.read" in authz["capabilities"]
        with urlopen(
            Request(f"http://{host}:{port}/v1/projects?limit=1", headers=auth), timeout=2
        ) as resp:
            projects = json.loads(resp.read().decode("utf-8"))
        assert projects["limit"] == 1
        bad = Request(
            f"http://{host}:{port}/v1/meta",
            headers={"Host": "evil.example", **auth},
        )
        from urllib.error import HTTPError

        with pytest.raises(HTTPError) as excinfo:
            urlopen(bad, timeout=2)
        assert excinfo.value.code == 403
    finally:
        server.shutdown()


def test_web_actions_recent_and_cap(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("web-op", extra={"web.action"})
    submit_web_action(vault, action_id="act-1", action_type="refresh-status", operator=op)
    recent = list_recent_actions(vault, limit=5)
    assert recent["count"] == 1
    assert recent["authority"] is False
    with pytest.raises(WebActionError, match="limit-out-of-range"):
        list_recent_actions(vault, limit=0)


def test_perf_includes_mcp_list(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = run_perf_baselines(vault, baseline_id="b2", iterations=1)
    assert "mcp_list_tools_ms" in report["measurements"]
    assert report["release_blocking"] is False


def test_docs_board_tip_not_empty() -> None:
    root = Path(__file__).resolve().parents[2]
    board = (root / "docs" / "atlas-2.1" / "PACKAGE-BOARD.md").read_text(encoding="utf-8")
    assert "ATLAS_2_1_RELEASE_CERTIFIED = NO" in board
    assert "not** empty" in board or "not empty" in board.lower()
    assert "OWNER_BLOCKED" in board
