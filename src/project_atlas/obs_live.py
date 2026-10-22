"""AS-2.1 observability polish - live surface health receipt.

Records which 2.1 live packages are present as an ops observability snapshot.
Operational plane only; never authority.

Deepen (AS-2.1-OBS-PERF-001): lane visibility for API/MCP/query/sync +
perf receipt counts. Presence/counts only — Unknown ≠ healthy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_atlas.compat_anchor import SNAPSHOT_ID, require_compatibility_anchor

PACKAGE_ID = "AS-2.1-OBS-LIVE-001"
DEEPEN_PACKAGE_ID = "AS-2.1-OBS-PERF-001"
TRUTH_BOUNDARY = "OBS LIVE != AUTHORITY / UNKNOWN!=HEALTHY"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _count_json(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for p in directory.iterdir() if p.is_file() and p.suffix == ".json")


def build_live_observability_receipt(
    vault: Path,
    *,
    receipt_id: str = "live-obs",
    persist: bool = True,
) -> dict[str, Any]:
    """Build a deterministic live-surface observability receipt.

    ``persist=False`` computes the receipt in memory only. LIVE_API GET
    ``/v1/obs`` must use that mode: write_enabled=false.
    """
    require_compatibility_anchor()
    ops = vault / "generated" / "ops"
    surfaces = {
        "api_server": True,
        "mcp_server": True,
        "web_actions": (ops / "web-actions").exists(),
        "chatgpt_bridge": (ops / "chatgpt").exists(),
        "collab": (ops / "collab").exists(),
        "provider_live": (ops / "provider").exists(),
        "scheduler": (ops / "scheduler").exists(),
        "autonomy_l3": (ops / "autonomy").exists(),
        "oai_responses_poc": (ops / "oai-responses-poc").exists(),
        "authz_audit": (ops / "authz").exists(),
        "pilot_prep": (ops / "pilot").exists(),
        "perf_baselines": (ops / "perf").exists(),
        "ops_receipts": True,
        # OBS-PERF deepen presence flags (ops markers only).
        "ask_atlas_ops": (ops / "ask").exists(),
        "sync_plan_dry_run": (ops / "sync-plan-dry-run.json").exists(),
        "sync_ops": (ops / "sync").exists(),
        "query_ops": (ops / "query").exists(),
    }
    lanes = {
        "api": {
            "module_available": True,
            "ops_marker": surfaces["api_server"],
            "note": "AppService health/projects back LIVE_API reads",
        },
        "mcp": {
            "module_available": True,
            "ops_marker": surfaces["mcp_server"],
            "note": "list_tools + invoke read tools only",
        },
        "query": {
            "ask_atlas_module": True,
            "query_plan_module": True,
            "ops_ask": surfaces["ask_atlas_ops"],
            "ops_query": surfaces["query_ops"],
            "note": "Ask Atlas live + multi-query plan scaffolds",
        },
        "sync": {
            "sync_plan_scaffold": True,
            "ops_dry_run": surfaces["sync_plan_dry_run"],
            "ops_sync_dir": surfaces["sync_ops"],
            "note": "Dry-run plan only; != authentic SYNC PILOT",
        },
        "perf": {
            "baselines_dir": surfaces["perf_baselines"],
            "baseline_receipt_count": _count_json(ops / "perf"),
            "note": "duration_ms samples only; != release gate",
        },
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "deepen_package_id": DEEPEN_PACKAGE_ID,
        "compat_snapshot_id": SNAPSHOT_ID,
        "receipt_id": receipt_id,
        "surfaces": surfaces,
        "lanes": lanes,
        "rollup": "unknown",
        "note": "Presence flags only; missing surface != healthy",
        "truth_boundary": TRUTH_BOUNDARY,
        "authority_plane": "none",
        "generated": {"by": "project-atlas"},
    }
    if persist:
        out = ops / "obs" / f"{receipt_id}-live.json"
        _atomic_write_json(out, payload)
    return payload
