"""AT3-055 — isolated local ranked-context serve."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.context_serve import (
    PACKAGE_ID,
    context_serve_capability,
    serve_ranked_context,
)


def _item(
    *,
    text: str,
    project_id: str = "harbor-api",
    freshness: str = "CURRENT",
    item_type: str = "claim_candidate",
) -> dict[str, object]:
    return {
        "text": text,
        "item_type": item_type,
        "provider": "chatgpt",
        "project_id": project_id,
        "freshness": freshness,
        "authority": "NON_CANONICAL",
    }


def test_capability_keeps_live_serve_blocked() -> None:
    cap = context_serve_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["local_ranked_pack"] == "IMPLEMENTED"
    assert cap["live_provider_serve"] == "EXTERNAL_BLOCKED"
    assert cap["new_cli_command"] is False
    assert cap["merge_authorization"] == "NOT_GRANTED"


def test_serves_local_pack_to_allowed_provider() -> None:
    report = serve_ranked_context(
        [_item(text="production uses PostgreSQL 15")],
        project_id="harbor-api",
        target_provider="claude",
        freshness_requirement="CURRENT",
    )
    assert report["package_id"] == PACKAGE_ID
    assert report["target_provider"] == "claude"
    assert report["live_provider_serve"] == "EXTERNAL_BLOCKED"
    assert report["live_serve_used"] is False
    assert report["write_applied"] is False
    assert report["promoted_to_truth_core"] == 0
    assert report["served"]["layers"]["current_reconciled_memory"][0]["text"].startswith(
        "production"
    )


def test_unknown_target_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        serve_ranked_context(
            [_item(text="x")],
            project_id="harbor-api",
            target_provider="marketplace",
        )
    assert exc.value.code == "CONTEXT_SERVE_TARGET_INVALID"


def test_cross_project_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        serve_ranked_context(
            [_item(text="x", project_id="other-api")],
            project_id="harbor-api",
            target_provider="gemini",
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_mixed_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        serve_ranked_context(
            [_item(text="ok"), "corrupt"],  # type: ignore[list-item]
            project_id="harbor-api",
            target_provider="cursor",
        )
    assert exc.value.code == "CONTEXT_INVALID"


def test_cli_serve_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register_atlas3_parsers(sub)
    items = tmp_path / "items.json"
    items.write_text(
        '[{"text":"PostgreSQL 15","item_type":"claim_candidate",'
        '"provider":"chatgpt","project_id":"harbor-api","freshness":"CURRENT"}]',
        encoding="utf-8",
    )
    assert (
        dispatch_atlas3(
            parser.parse_args(
                [
                    "memory",
                    "context",
                    "--items",
                    str(items),
                    "--project",
                    "harbor-api",
                    "--target-provider",
                    "claude",
                    "--freshness",
                    "CURRENT",
                ]
            )
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["package_id"] == PACKAGE_ID
    assert report["target_provider"] == "claude"
    assert report["live_serve_used"] is False


def test_module_does_not_touch_certified_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/context_serve.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.runtime_22",
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "from project_atlas.ask2",
        "write_json_atomic",
    ):
        assert name not in source
