"""AS-2.1 Track B deepen-005: mission/workspace LIVE + L3 loop + OAI size."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.authz import elevated_operator
from project_atlas.autonomy_l3 import (
    AutonomyL3Error,
    enable_bounded_l3,
    run_bounded_l3_loop,
)
from project_atlas.openai_import_real import OpenAIRealImportError, import_openai_export
from project_atlas.scheduler_live import arm_scheduler
from project_atlas.web_mission_workspace import build_mission_view, build_workspace_view


def test_mission_workspace_never_invent_pilot(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    mission = build_mission_view(vault)
    workspace = build_workspace_view(vault)
    assert mission["pilot_estate_rows"] == []
    assert mission["authentic_pilot"] is False
    assert workspace["pilot_estate_rows"] == []
    assert workspace["authentic_pilot"] is False
    assert mission["ui_canonical"] is False
    assert workspace["graph_authority"] is False


def test_api_mission_workspace_routes(tmp_path: Path) -> None:
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
        assert meta["mission_live"] is True
        assert meta["workspace_live"] is True
        with urlopen(Request(f"http://{host}:{port}/v1/mission", headers=auth), timeout=2) as resp:
            mission = json.loads(resp.read().decode("utf-8"))
        assert mission["pilot_estate_rows"] == []
        with urlopen(
            Request(f"http://{host}:{port}/v1/workspace", headers=auth), timeout=2
        ) as resp:
            workspace = json.loads(resp.read().decode("utf-8"))
        assert workspace["authentic_pilot"] is False
    finally:
        server.shutdown()


def test_l3_loop_runs_version(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-loop")
    op = elevated_operator("l3-loop-op", extra={"autonomy.l3", "scheduler.dispatch"})
    enable_bounded_l3(vault, policy_id="pol-loop", arm_id="arm-loop", operator=op, max_jobs=2)
    report = run_bounded_l3_loop(vault, policy_id="pol-loop", jobs=["version"], operator=op)
    assert report["l3_loop"] is True
    assert report["promoted"] is False
    assert report["jobs_run"][0]["job"] == "version"
    assert report["jobs_run"][0]["exit_code"] == 0


def test_l3_loop_rejects_over_max(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-loop")
    op = elevated_operator("l3-loop-op", extra={"autonomy.l3", "scheduler.dispatch"})
    enable_bounded_l3(vault, policy_id="pol-max", arm_id="arm-loop", operator=op, max_jobs=1)
    with pytest.raises(AutonomyL3Error, match="exceed-max"):
        run_bounded_l3_loop(
            vault,
            policy_id="pol-max",
            jobs=["version", "validate"],
            operator=op,
        )


def test_oai_import_rejects_oversized(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "huge.md"
    export.write_bytes(b"User: x\n" + (b"a" * 2_000_100))
    with pytest.raises(OpenAIRealImportError, match="size-out-of-range"):
        import_openai_export(vault, export, import_id="big-1")


def test_web_stubs_demo_isolated() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("sample-mission-control.json", "sample-workspace.json"):
        payload = json.loads((root / "apps" / "web" / "public" / name).read_text(encoding="utf-8"))
        assert payload["demo_isolated"] is True
        assert payload["pilot_estate_rows"] == []
        assert payload.get("authentic_pilot") is False
