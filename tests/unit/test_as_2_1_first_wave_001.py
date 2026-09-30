"""AS-2.1 first-wave productionization tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.app_service import open_app_service
from project_atlas.authz import AuthzError, default_operator, elevated_operator
from project_atlas.mcp_server import McpServerError, invoke_mcp_tool
from project_atlas.openai_import_real import (
    OpenAIRealImportError,
    import_openai_export,
)
from project_atlas.pilot_auth_prep import scan_known_pilot_roots, write_pilot_prep_report
from project_atlas.scheduler_live import arm_scheduler, dispatch_supervised_job


def test_app_service_snapshot(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "projects").mkdir()
    snap = open_app_service(vault).snapshot()
    assert snap["package_id"] == "AS-2.1-APP-SVC-001"
    assert snap["projects"] == []
    assert snap["health"]["vault_health"]["rollup"] == "unknown"


def test_authz_deny_write() -> None:
    op = default_operator()
    with pytest.raises(AuthzError, match=r"authz-denied:vault\.write"):
        op.require("vault.write")


def test_api_server_health(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hdrs = session_credentials(server).auth_headers()
        req = Request(f"http://{host}:{port}/v1/meta", headers=hdrs)
        with urlopen(req, timeout=2) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        assert meta["live_api"] is True
        assert meta["write_enabled"] is False
    finally:
        server.shutdown()


def test_mcp_read_tool(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = invoke_mcp_tool(vault, "atlas.ops.health.read")
    assert report["live_mcp_read"] is True
    with pytest.raises(McpServerError, match="mcp-tool-denied"):
        invoke_mcp_tool(vault, "atlas.vault.write")


def test_oai_real_import(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "chat-export.md"
    export.write_text("User: hello\nAssistant: world\n", encoding="utf-8")
    report = import_openai_export(vault, export, import_id="imp-a")
    assert report["real_openai_export_import"] is True
    assert report["live_openai_api"] is False
    assert report["turn_count"] == 2


def test_oai_rejects_secrets(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "chat-export.md"
    export.write_text(
        "User: key\nAssistant: -----BEGIN RSA PRIVATE KEY-----\nMII\n"
        "-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    with pytest.raises(OpenAIRealImportError, match="secret-findings"):
        import_openai_export(vault, export, import_id="imp-b")


def test_scheduler_supervised_version(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-a")
    op = elevated_operator("disp", extra={"scheduler.dispatch"})
    report = dispatch_supervised_job(vault, arm_id="arm-a", job="version", operator=op)
    assert report["live_supervised_scheduler"] is True
    assert report["exit_code"] == 0


def test_pilot_prep_escalates_when_empty(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    missing = tmp_path / "no-estate"
    report = write_pilot_prep_report(
        vault,
        report_id="prep-a",
        candidates=[missing],
    )
    assert report["authentic_found"] == 0
    assert report["escalation_required"] is True
    assert report["pilot_pass"] is False
    scanned = scan_known_pilot_roots(candidates=[missing])
    assert scanned["authentic_found"] == 0


def test_docs_tree_present() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "atlas-2.1" / "PRODUCTIONIZATION-AUDIT.md").is_file()
    assert (root / "docs" / "atlas-2.1" / "FEATURE-MATURITY-MATRIX.md").is_file()
    assert (root / "docs" / "AS-2.1-WEB-LIVE-001.md").is_file()
