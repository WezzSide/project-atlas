"""AS-2.1-MCP-ADV-001 — adversarial MCP read-only surface.

Probes: unknown tools, capability escalation, write-via-read, path traversal,
malformed args, deterministic replay. MCP stays READ ONLY (T-2.1-03).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from project_atlas.authz import AuthzError, OperatorProfile, elevated_operator
from project_atlas.mcp_server import (
    ADV_PACKAGE_ID,
    McpServerError,
    handle_mcp_request_line,
    invoke_mcp_tool,
    list_mcp_tools,
)


def _vault_snapshot(vault: Path) -> dict[str, str]:
    """Relative path -> sha256 hex for every regular file under vault."""
    out: dict[str, str] = {}
    for path in sorted(vault.rglob("*")):
        if path.is_file():
            rel = path.relative_to(vault).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[rel] = digest
    return out


def test_adv_unknown_tool_denied(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(McpServerError, match="mcp-tool-denied"):
        invoke_mcp_tool(vault, "atlas.unknown.fabricate")
    with pytest.raises(McpServerError, match="mcp-tool-denied"):
        invoke_mcp_tool(vault, "atlas.provider.generate")


def test_adv_escalation_write_tools_denied_even_with_vault_write(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    elev = elevated_operator("mcp-adv-elev", extra={"vault.write"})
    assert elev.allows("vault.write")
    assert elev.allows("mcp.read")
    for tool in (
        "atlas.vault.write",
        "atlas.estate.scan",
        "atlas.provider.generate",
    ):
        with pytest.raises(McpServerError, match="mcp-tool-denied"):
            invoke_mcp_tool(vault, tool, operator=elev)
    listing = list_mcp_tools(operator=elev)
    assert listing["write_tools"] == []
    assert "atlas.vault.write" not in listing["tools"]


def test_adv_mcp_read_capability_required(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    bare = OperatorProfile(operator_id="no-mcp", capabilities=frozenset({"api.read"}))
    with pytest.raises(AuthzError, match=r"authz-denied:mcp\.read"):
        invoke_mcp_tool(vault, "atlas.ops.health.read", operator=bare)
    with pytest.raises(AuthzError, match=r"authz-denied:mcp\.read"):
        list_mcp_tools(operator=bare)


def test_adv_write_via_read_leaves_vault_unchanged(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "projects").mkdir()
    marker = vault / "canary.txt"
    marker.write_text("pre-mcp-read\n", encoding="utf-8")
    before = _vault_snapshot(vault)
    for tool in (
        "atlas.ops.health.read",
        "atlas.knowledge.query.read",
        "atlas.explain.receipt.read",
        "atlas.projects.list.read",
        "atlas.brief.read",
        "atlas.next.read",
        "atlas.changed.read",
        "atlas.overview.read",
        "atlas.decisions.read",
        "atlas.unknown.read",
        "atlas.state.read",
        "atlas.architecture.read",
        "atlas.roadmap.read",
        "atlas.portfolio.read",
        "atlas.bitemporal.read",
        "atlas.indexes.read",
    ):
        report = invoke_mcp_tool(vault, tool)
        assert report["live_mcp_read"] is True
        assert report["tool_id"] == tool
        assert _vault_snapshot(vault) == before
    assert marker.read_text(encoding="utf-8") == "pre-mcp-read\n"


def test_adv_path_traversal_tool_ids_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    probes = (
        "../atlas.ops.health.read",
        "..\\atlas.ops.health.read",
        "atlas/../ops.health.read",
        "atlas.ops.health.read/../../etc/passwd",
        "%2e%2e/atlas.ops.health.read",
        "C:/Windows/System32",
        "/etc/passwd",
    )
    for probe in probes:
        with pytest.raises(McpServerError, match="mcp-tool-path-traversal"):
            invoke_mcp_tool(vault, probe)


def test_adv_path_traversal_request_keys_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    payloads = (
        {"tool": "atlas.ops.health.read", "path": "../../etc/passwd"},
        {"tool": "atlas.ops.health.read", "vault": "/tmp/evil"},
        {"tool": "atlas.ops.health.read", "args": {"path": "../x"}},
        {"tool": "atlas.ops.health.read", "write": True},
        {"tool": "atlas.ops.health.read", "destination": "projects/hijack.md"},
    )
    for payload in payloads:
        with pytest.raises(McpServerError, match="mcp-request-forbidden-key"):
            handle_mcp_request_line(vault, json.dumps(payload))


def test_adv_malformed_args_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(McpServerError, match="mcp-json-invalid"):
        handle_mcp_request_line(vault, "{not-json")
    with pytest.raises(McpServerError, match="mcp-request-not-object"):
        handle_mcp_request_line(vault, json.dumps(["atlas.ops.health.read"]))
    with pytest.raises(McpServerError, match="mcp-tool-missing"):
        handle_mcp_request_line(vault, json.dumps({}))
    with pytest.raises(McpServerError, match="mcp-tool-missing"):
        handle_mcp_request_line(vault, json.dumps({"tool": ""}))
    with pytest.raises(McpServerError, match="mcp-tool-missing"):
        handle_mcp_request_line(vault, json.dumps({"tool": None}))
    with pytest.raises(McpServerError, match="mcp-tool-missing"):
        handle_mcp_request_line(vault, json.dumps({"tool": 12}))
    with pytest.raises(McpServerError, match="mcp-request-unexpected-key"):
        handle_mcp_request_line(vault, json.dumps({"tool": "atlas.ops.health.read", "extra": 1}))
    with pytest.raises(McpServerError, match="mcp-tool-id-malformed"):
        invoke_mcp_tool(vault, "Atlas.Ops.Health.Read")
    with pytest.raises(McpServerError, match="mcp-tool-id-nul"):
        invoke_mcp_tool(vault, "atlas.ops.health.read\x00.write")


def test_adv_replay_byte_identical(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "projects").mkdir()
    line = json.dumps({"tool": "atlas.projects.list.read"}, sort_keys=True)
    first = handle_mcp_request_line(vault, line)
    second = handle_mcp_request_line(vault, line)
    assert first == second
    a = invoke_mcp_tool(vault, "atlas.ops.health.read")
    b = invoke_mcp_tool(vault, "atlas.ops.health.read")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_adv_docs_and_package_id() -> None:
    assert ADV_PACKAGE_ID == "AS-2.1-MCP-ADV-001"
    root = Path(__file__).resolve().parents[2]
    suite = (root / "docs" / "atlas-2.1" / "ADV-LIVE-SUITE.md").read_text(encoding="utf-8")
    assert "| ADV-2.1-23 | MCP ADV |" in suite
    assert "AS-2.1-MCP-ADV-001" in suite
    assert "AS-2.1-L3-JOB-MATRIX-ADV" in suite
    assert "AS-2.1-API-ADV-DEEPEN" in suite
    assert "| ADV-2.1-24 | L3 job-matrix ADV |" in suite
    assert "| ADV-2.1-30 | API |" in suite
    board = (root / "docs" / "atlas-2.1" / "PACKAGE-BOARD.md").read_text(encoding="utf-8")
    assert "AS-2.1-MCP-ADV-001" in board
    assert "OPEN (this PR)" not in board
    assert "**MERGED** #165" in board
    assert "**MERGED** #166" in board
    assert "**MERGED** #164" in board
