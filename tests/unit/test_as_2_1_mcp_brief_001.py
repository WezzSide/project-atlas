"""AS-2.1-MCP-BRIEF-001 — zero-arg vault-scoped Coder Alpha brief MCP tool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from project_atlas.authz import AuthzError, OperatorProfile
from project_atlas.mcp_server import (
    BRIEF_PACKAGE_ID,
    McpServerError,
    handle_mcp_request_line,
    invoke_mcp_tool,
    list_mcp_tools,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _snapshot(vault: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(vault.rglob("*")):
        if path.is_file():
            out[path.relative_to(vault).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _seed_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "projects" / "dark-factory-02ee94d0").mkdir(parents=True)
    (vault / "projects" / "harbor-portal").mkdir(parents=True)
    _write(
        vault / "generated" / "ops" / "project-brief-dark-factory-02ee94d0.json",
        {
            "project_id": "dark-factory-02ee94d0",
            "purpose": "Factory compile",
            "suggested_next_work": ["Resolve factory conflict"],
        },
    )
    _write(
        vault / "generated" / "ops" / "project-brief-harbor-portal.json",
        {
            "project_id": "harbor-portal",
            "purpose": "Portal UI",
            "suggested_next_work": ["SECRET-PORTAL-VALUE"],
        },
    )
    return vault


def test_brief_tool_is_allow_listed() -> None:
    listing = list_mcp_tools()
    assert "atlas.brief.read" in listing["tools"]
    assert listing["write_tools"] == []


def test_empty_vault_does_not_invent_briefs(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    report = invoke_mcp_tool(vault, "atlas.brief.read")
    result = report["result"]
    assert result["package_id"] == BRIEF_PACKAGE_ID
    assert result["project_count"] == 0
    assert result["briefs"] == []
    assert result["honesty"]["unknown_is_valid"] is True
    assert result["honesty"]["mcp_is_authority"] is False
    assert result["honesty"]["portfolio_implicit_all"] is False
    assert result["honesty"]["request_contains_project"] is False


def test_missing_brief_file_is_unknown_not_invented(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    (vault / "projects" / "sparse-proj").mkdir(parents=True)
    report = invoke_mcp_tool(vault, "atlas.brief.read")
    rows = report["result"]["briefs"]
    assert len(rows) == 1
    assert rows[0]["project_id"] == "sparse-proj"
    brief = rows[0]["brief"]
    assert brief["purpose"] == "UNKNOWN"
    assert brief.get("available") is False
    assert brief["honesty"]["unknown_is_valid"] is True
    assert brief["honesty"]["fabricated_fields"] is False


def test_cross_project_isolation(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path)
    report = invoke_mcp_tool(vault, "atlas.brief.read")
    rows = {row["project_id"]: row["brief"] for row in report["result"]["briefs"]}
    assert set(rows) == {"dark-factory-02ee94d0", "harbor-portal"}
    factory = json.dumps(rows["dark-factory-02ee94d0"], sort_keys=True)
    assert "SECRET-PORTAL-VALUE" not in factory
    assert "Portal UI" not in factory
    assert rows["harbor-portal"]["purpose"] == "Portal UI"
    assert "SECRET-PORTAL-VALUE" in json.dumps(rows["harbor-portal"])


def test_zero_arg_protocol_and_no_write(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path)
    before = _snapshot(vault)
    line = json.dumps({"tool": "atlas.brief.read"}, sort_keys=True)
    first = handle_mcp_request_line(vault, line)
    second = handle_mcp_request_line(vault, line)
    assert first == second
    assert _snapshot(vault) == before
    with pytest.raises(McpServerError, match="mcp-request-forbidden-key"):
        handle_mcp_request_line(
            vault,
            json.dumps({"tool": "atlas.brief.read", "args": {"project": "harbor-portal"}}),
        )
    with pytest.raises(McpServerError, match="mcp-request-unexpected-key"):
        handle_mcp_request_line(
            vault,
            json.dumps({"tool": "atlas.brief.read", "project": "harbor-portal"}),
        )


def test_mcp_read_required(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    bare = OperatorProfile(operator_id="no-mcp", capabilities=frozenset({"api.read"}))
    with pytest.raises(AuthzError, match=r"authz-denied:mcp\.read"):
        invoke_mcp_tool(vault, "atlas.brief.read", operator=bare)


def test_determinism(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path)
    a = invoke_mcp_tool(vault, "atlas.brief.read")
    b = invoke_mcp_tool(vault, "atlas.brief.read")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ids = [row["project_id"] for row in a["result"]["briefs"]]
    assert ids == sorted(ids)
