"""AS-2.1 Track B deepen-007: ops receipts + L3 job-matrix ADV + UX polish guards."""

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
    disable_bounded_l3,
    enable_bounded_l3,
    run_bounded_l3_loop,
)
from project_atlas.obs_live import build_live_observability_receipt
from project_atlas.ops_receipts import inventory_ops_receipts
from project_atlas.scheduler_live import arm_scheduler
from project_atlas.web_mission_workspace import build_mission_view, build_workspace_view


def test_ops_receipts_honest_empty(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    inv = inventory_ops_receipts(vault)
    assert inv["available"] is False
    assert inv["receipt_source"] == "unavailable"
    assert inv["receipt_rows"] == 0
    assert inv["completion_claimed"] is False
    assert inv["rollup"] == "unknown"


def test_ops_receipts_lists_obs(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    build_live_observability_receipt(vault, receipt_id="r1")
    inv = inventory_ops_receipts(vault)
    assert inv["available"] is True
    assert inv["receipt_rows"] >= 1
    assert any(r.get("kind") == "obs" for r in inv["receipts"])
    assert inv["completion_claimed"] is False


def test_api_ops_receipts_route(tmp_path: Path) -> None:
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
        assert meta["ops_receipts"] is True
        with urlopen(
            Request(f"http://{host}:{port}/v1/ops/receipts", headers=auth), timeout=2
        ) as resp:
            inv = json.loads(resp.read().decode("utf-8"))
        assert inv["completion_claimed"] is False
    finally:
        server.shutdown()


def test_mission_workspace_surface_polish(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    mission = build_mission_view(vault)
    workspace = build_workspace_view(vault)
    assert "surfaces" in mission
    assert "surfaces" in workspace
    assert mission["empty_projects"] is True
    assert workspace["empty_knowledge"] is True
    assert mission["authentic_pilot"] is False


def test_l3_job_matrix_allowed_jobs(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-m")
    op = elevated_operator("l3-m", extra={"autonomy.l3", "scheduler.dispatch"})
    enable_bounded_l3(vault, policy_id="pol-m", arm_id="arm-m", operator=op, max_jobs=3)
    report = run_bounded_l3_loop(
        vault,
        policy_id="pol-m",
        jobs=["version"],
        operator=op,
    )
    assert report["promoted"] is False
    assert len(report["jobs_run"]) == 1
    assert report["jobs_run"][0]["exit_code"] == 0
    assert report["levels_enabled"]["4"] is False
    assert report["levels_enabled"]["5"] is False


def test_l3_job_matrix_forbidden_disabled_and_exceed_max(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-m2")
    op = elevated_operator("l3-m2", extra={"autonomy.l3", "scheduler.dispatch"})
    enable_bounded_l3(vault, policy_id="pol-m2", arm_id="arm-m2", operator=op, max_jobs=2)
    with pytest.raises(AutonomyL3Error, match="job-forbidden"):
        run_bounded_l3_loop(vault, policy_id="pol-m2", jobs=["ingest"], operator=op)
    with pytest.raises(AutonomyL3Error, match="jobs-exceed-max"):
        run_bounded_l3_loop(
            vault,
            policy_id="pol-m2",
            jobs=["version", "version", "version"],
            operator=op,
        )
    disable_bounded_l3(vault, policy_id="pol-m2", operator=op)
    with pytest.raises(AutonomyL3Error, match="policy-disabled"):
        run_bounded_l3_loop(vault, policy_id="pol-m2", jobs=["version"], operator=op)


def test_pages_keep_accepted_and_authentic_false() -> None:
    root = Path(__file__).resolve().parents[2]
    mission = (
        root / "apps" / "web" / "src" / "pages" / "production" / "MissionControlPage.tsx"
    ).read_text(encoding="utf-8")
    workspace = (
        root / "apps" / "web" / "src" / "pages" / "production" / "WorkspacePage.tsx"
    ).read_text(encoding="utf-8")
    ops = (root / "apps" / "web" / "src" / "pages" / "production" / "OpsHealthPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "APPLICATION ACCEPTED = YES" in mission
    assert "authentic_pilot=false" in mission
    assert "APPLICATION ACCEPTED = YES" in workspace
    assert "useOpsReceipts" in ops
    assert "completion_claimed" in ops
