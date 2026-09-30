"""AT3-054 — isolated consume-only memory context compiler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import dispatch_atlas3, register_atlas3_parsers
from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.context_compiler import (
    PACKAGE_ID,
    compile_memory_context,
    context_compiler_capability,
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
        "source_content_hash": "sha256:" + "a" * 64,
        "authority": "NON_CANONICAL",
    }


def test_capability_is_consume_only() -> None:
    cap = context_compiler_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["consume_only"] is True
    assert cap["rewrites_certified_compiler"] is False
    assert cap["certified_compiler_write"] is False
    assert cap["ask2_replaced"] is False
    assert cap["writes_truth_core"] is False
    assert cap["stale_as_current"] is False
    assert cap["merge_authorization"] == "NOT_GRANTED"


def test_current_memory_ranks_below_project_evidence() -> None:
    report = compile_memory_context(
        [_item(text="assistant mentioned PostgreSQL 16 later")],
        project_id="harbor-api",
        project_evidence=["harbor-api production is PostgreSQL 15"],
        freshness_requirement="CURRENT",
    )
    assert report["package_id"] == PACKAGE_ID
    assert report["consume_only"] is True
    assert report["stale_presented_as_current"] is False
    assert report["recent_llm_outranks_project_evidence"] is False
    assert report["write_applied"] is False
    assert report["promoted_to_truth_core"] == 0
    layers = report["layers"]
    assert layers["authoritative_project_evidence"] == ["harbor-api production is PostgreSQL 15"]
    assert layers["current_reconciled_memory"][0]["text"].startswith("assistant")
    assert layers["stale_memory_historical_only"] == []


def test_stale_is_historical_only_when_allowed() -> None:
    report = compile_memory_context(
        [_item(text="old mention of PostgreSQL 16", freshness="STALE")],
        project_id="harbor-api",
        include_stale_historical=True,
        freshness_requirement="ALLOW_STALE_HISTORICAL",
    )
    assert report["layers"]["current_reconciled_memory"] == []
    assert report["layers"]["stale_memory_historical_only"][0]["freshness"] == "STALE"
    assert report["unknown_stays_unknown"] is True


def test_current_freshness_rejects_stale_historical_flag() -> None:
    with pytest.raises(Atlas3Error) as exc:
        compile_memory_context(
            [_item(text="stale", freshness="STALE")],
            project_id="harbor-api",
            include_stale_historical=True,
            freshness_requirement="CURRENT",
        )
    assert exc.value.code == "STALE_AS_CURRENT"


def test_stale_marked_current_fails_closed() -> None:
    row = _item(text="stale leak", freshness="STALE")
    row["status"] = "CURRENT"
    with pytest.raises(Atlas3Error) as exc:
        compile_memory_context([row], project_id="harbor-api")
    assert exc.value.code == "STALE_AS_CURRENT"


def test_unknown_question_stays_unknown() -> None:
    report = compile_memory_context(
        [_item(text="which datastore?", freshness="UNKNOWN", item_type="open_question")],
        project_id="harbor-api",
        freshness_requirement="CURRENT",
    )
    assert report["unknown_count"] == 1
    assert report["unknown_stays_unknown"] is True
    assert report["layers"]["current_reconciled_memory"] == []
    assert report["layers"]["unknown_open_questions"][0]["item_type"] == "open_question"


def test_cross_project_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        compile_memory_context(
            [
                _item(text="harbor", project_id="harbor-api"),
                _item(text="other", project_id="other-api"),
            ],
            project_id="harbor-api",
        )
    assert exc.value.code == "PROJECT_MISMATCH"


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        compile_memory_context(
            [_item(text="ok"), "corrupt"],  # type: ignore[list-item]
            project_id="harbor-api",
        )
    assert exc.value.code == "CONTEXT_INVALID"


def test_trust_score_fails_closed() -> None:
    row = _item(text="trusted")
    row["trust_score"] = 0.9
    with pytest.raises(Atlas3Error) as exc:
        compile_memory_context([row], project_id="harbor-api")
    assert exc.value.code == "AUTHORITY_CLAIM_FORBIDDEN"


def test_cli_capability_and_compile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register_atlas3_parsers(sub)
    assert dispatch_atlas3(parser.parse_args(["memory", "context"])) == 0
    cap = json.loads(capsys.readouterr().out)
    assert cap["package"] == PACKAGE_ID
    assert cap["consume_only"] is True

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
                    "--freshness",
                    "CURRENT",
                ]
            )
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["package_id"] == PACKAGE_ID
    assert report["project_id"] == "harbor-api"
    assert report["write_applied"] is False


def test_module_does_not_touch_certified_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/context_compiler.py").read_text(
        encoding="utf-8"
    )
    for name in (
        "from project_atlas.runtime_22",
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.knowledge_compiler",
        "from project_atlas.ask2",
        "write_json_atomic",
    ):
        assert name not in source
