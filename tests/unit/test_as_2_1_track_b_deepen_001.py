"""AS-2.1 Track B deepen: web/sched/L3/chatgpt/ask/perf/ADV."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.ask_atlas_live import ask_atlas_live
from project_atlas.authz import elevated_operator
from project_atlas.autonomy_l3 import disable_bounded_l3, enable_bounded_l3
from project_atlas.chatgpt_bridge import bridge_chatgpt_export
from project_atlas.openai_importer_fixtures import parse_chat_export
from project_atlas.perf_baselines import run_perf_baselines
from project_atlas.scheduler_live import arm_scheduler, dispatch_supervised_job


def test_chatgpt_human_ai_variant() -> None:
    turns = parse_chat_export("Human: hi\nAI: hello there\n")
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"


def test_chatgpt_json_role_array(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "conv.json"
    export.write_text(
        json.dumps(
            [
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
            ]
        ),
        encoding="utf-8",
    )
    report = bridge_chatgpt_export(vault, export, bridge_id="json-a")
    assert report["turn_count"] == 2
    assert report["export_variant"] == "json"
    assert report["llm_authority"] is False


def test_chatgpt_mapping_export() -> None:
    payload = {
        "mapping": {
            "n1": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["hello"]},
                }
            },
            "n2": {
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": ["world"]},
                }
            },
        }
    }
    turns = parse_chat_export(json.dumps(payload))
    assert len(turns) == 2


def test_sched_arm_timeout_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm = arm_scheduler(vault, arm_id="arm-t", default_timeout_s=30)
    assert arm["default_timeout_s"] == 30
    op = elevated_operator("sched", extra={"scheduler.dispatch"})
    report = dispatch_supervised_job(
        vault, arm_id="arm-t", job="version", operator=op, timeout_s=30
    )
    assert report["timed_out"] is False
    assert report["timeout_s"] == 30
    assert "duration_ms" in report


def test_l3_disable_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    arm_scheduler(vault, arm_id="arm-l3")
    op = elevated_operator("l3-op", extra={"autonomy.l3"})
    enable_bounded_l3(
        vault,
        policy_id="pol-b",
        arm_id="arm-l3",
        operator=op,
        job_timeout_s=45,
    )
    disabled = disable_bounded_l3(vault, policy_id="pol-b", operator=op)
    assert disabled["enabled"] is False
    assert disabled["l3_bounded_autonomy"] is False


def test_ask_health_keywords(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = ask_atlas_live(vault, query="what is vault health status")
    assert "health" in report["matches"]["health_keywords"]
    assert report["canonical_write"] is False


def test_perf_baseline_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = run_perf_baselines(vault, baseline_id="b1", iterations=2)
    assert report["release_blocking"] is False
    assert report["authentic_pilot_substitute"] is False
    assert report["measurements"]["app_service_snapshot_ms"]["max_ms"] >= 0


def test_web_hook_source_types_present() -> None:
    # Guard TS contract via public sample stub shape used by demo isolation.
    root = Path(__file__).resolve().parents[2]
    stub = json.loads(
        (root / "apps" / "web" / "public" / "sample-read-status.json").read_text(encoding="utf-8")
    )
    assert stub["ui_canonical"] is False
    assert stub["read_plane"] == "stub"
    assert stub.get("data_source") == "demo_stub"
    assert stub.get("demo_isolated") is True


def test_adv_docs_suite_lists_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "docs" / "atlas-2.1" / "ADV-LIVE-SUITE.md").read_text(encoding="utf-8")
    for token in ("ADV-2.1-01", "API", "MCP", "AUTHZ", "SCHED", "ASK"):
        assert token in text
