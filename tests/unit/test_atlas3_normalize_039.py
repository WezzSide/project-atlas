"""AT3-039 — Cross-provider conversation normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.memory.envelope import SCHEMA_NAME
from project_atlas.atlas3.memory.normalize import (
    PACKAGE_ID,
    normalize_capability,
    normalize_turns,
)


def test_capability_is_honest() -> None:
    cap = normalize_capability()
    assert cap["package"] == PACKAGE_ID
    assert cap["schema"] == SCHEMA_NAME
    assert cap["graph_is_authority"] is False
    assert cap["raw_transcript_persisted"] is False
    assert cap["partial_persist_on_corrupt"] is False


def test_role_aliases_and_parent_chain() -> None:
    envelopes = normalize_turns(
        [
            {"role": "human", "text": "which datastore?"},
            {"role": "ai", "content": "postgres 16"},
        ],
        provider="chatgpt",
        conversation_id="c-norm",
        import_mode="EXPORT",
        project_id="harbor-api",
    )
    assert len(envelopes) == 2
    assert envelopes[0]["role"] == "user"
    assert envelopes[1]["role"] == "assistant"
    assert envelopes[0]["schema"] == SCHEMA_NAME
    assert envelopes[0]["import_mode"] == "EXPORT"
    assert envelopes[0]["raw_transcript_persisted"] is False
    assert envelopes[1]["parent_message_id"] == envelopes[0]["message_id"]
    assert envelopes[0]["project_id"] == "harbor-api"


def test_non_list_turns_fail_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_turns(  # type: ignore[arg-type]
            {"role": "user", "text": "x"},
            provider="chatgpt",
            conversation_id="c1",
            import_mode="EXPORT",
        )
    assert exc.value.code == "NORMALIZE_INVALID"


def test_mixed_valid_and_corrupt_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_turns(
            [{"role": "user", "text": "ok"}, "corrupt"],  # type: ignore[list-item]
            provider="chatgpt",
            conversation_id="c1",
            import_mode="EXPORT",
        )
    assert exc.value.code == "NORMALIZE_INVALID"


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_turns(
            [{"role": "narrator", "text": "once upon a time"}],
            provider="chatgpt",
            conversation_id="c1",
            import_mode="EXPORT",
        )
    assert exc.value.code == "UNKNOWN_ROLE"


def test_unknown_import_mode_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_turns(
            [{"role": "user", "text": "ok"}],
            provider="chatgpt",
            conversation_id="c1",
            import_mode="SCRAPE",
        )
    assert exc.value.code == "UNKNOWN_IMPORT_MODE"


def test_malformed_provider_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        normalize_turns(
            [{"role": "user", "text": "ok"}],
            provider="ChatGPT!",
            conversation_id="c1",
            import_mode="EXPORT",
        )
    assert exc.value.code == "MALFORMED_PROVIDER"


def test_module_does_not_touch_2x_bridges() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/memory/normalize.py").read_text(encoding="utf-8")
    for name in (
        "from project_atlas.chatgpt_bridge",
        "from project_atlas.chatgpt_capture",
        "knowledge_compiler",
        "from project_atlas.ingestion",
        "write_text(",
    ):
        assert name not in source
