"""AS-2.0-MCP-001 MCP tool registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.mcp_registry import (
    McpRegistryError,
    McpTool,
    build_mcp_tool_registry,
)
from project_atlas.schema import available_schemas, validate_record


def test_mcp_registry_deny_by_default(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_mcp_tool_registry(vault, registry_id="default")
    assert report["live_server"] is False
    assert report["default_policy"] == "deny"
    validate_record(report, "mcp-tool-registry")
    assert (vault / "generated" / "ops" / "mcp" / "default-tool-registry.json").is_file()


def test_mcp_rejects_live_server(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(McpRegistryError, match="live-server-forbidden"):
        build_mcp_tool_registry(vault, registry_id="default", enable_live_server=True)


def test_mcp_rejects_enabling_write(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(McpRegistryError, match="enable-forbidden"):
        build_mcp_tool_registry(
            vault,
            registry_id="bad",
            tools=[
                McpTool("atlas.vault.write", "vault-write", True, "nope"),
            ],
        )


def test_mcp_docs_and_schema() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-MCP-001.md").is_file()
    assert "mcp-tool-registry" in available_schemas()
