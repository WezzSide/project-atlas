"""AS-SEC-SCAN-F1 — bare GitHub/OpenAI token prefixes must fail closed.

Independently reproduced on main b87b4a22: scan_text missed ghp_/sk-
shapes, so chatgpt_bridge persisted a GitHub PAT and obsidian_capture
persisted an OpenAI-shaped token in source_metadata (NFR-004 / AT-014).
Synthetic credentials only. Findings remain metadata-only (CODEX-SEC-006).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.capture_sources import build_capture_request
from project_atlas.chatgpt_bridge import ChatgptBridgeError, bridge_chatgpt_export
from project_atlas.obsidian_capture import CaptureError, capture
from project_atlas.secrets import REDACTED_PLACEHOLDER, redact_text, scan_text

# Synthetic only — never a live credential.
_GHP = "ghp_" + ("A" * 36)
_SK = "sk-" + ("x" * 48)
_SK_PROJ = "sk-proj-" + ("B" * 20)
_GITHUB_PAT = "github_pat_" + ("C" * 22)


def test_scan_text_detects_bare_token_prefixes() -> None:
    for token, pattern in (
        (_GHP, "github-pat"),
        (_SK, "openai-api-key"),
        (_SK_PROJ, "openai-project-key"),
        (_GITHUB_PAT, "github-fine-grained-pat"),
    ):
        findings = scan_text(f"note {token} tail")
        assert [f.pattern for f in findings] == [pattern]
        dumped = json.dumps([f.__dict__ for f in findings])
        assert token not in dumped


def test_scan_text_does_not_match_short_sk_fixtures() -> None:
    assert scan_text("sk-test") == []
    assert scan_text("sk-test-not-a-real-key") == []
    assert scan_text("sk_test_SYNTHETIC_SHORT") == []


def test_redact_text_strips_bare_token_spans() -> None:
    blob = f"keep {_GHP} and {_SK}"
    redacted = redact_text(blob)
    assert _GHP not in redacted
    assert _SK not in redacted
    assert REDACTED_PLACEHOLDER in redacted


def test_chatgpt_bridge_rejects_ghp_export_before_write(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    export = tmp_path / "export.md"
    export.write_text(f"User: hi\nAssistant: token is {_GHP}\n", encoding="utf-8")
    with pytest.raises(ChatgptBridgeError, match="chatgpt-export-secret-findings"):
        bridge_chatgpt_export(vault, export, bridge_id="demo")
    out = vault / "generated" / "ops" / "chatgpt" / "demo-bridge.json"
    assert not out.exists()
    generated = vault / "generated"
    if generated.exists():
        blob = "".join(p.read_text(encoding="utf-8") for p in generated.rglob("*") if p.is_file())
        assert _GHP not in blob


def test_obsidian_capture_rejects_sk_metadata_before_write(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "p1").mkdir(parents=True)
    req = build_capture_request(
        content="hello",
        source_type="text",
        source_application="test",
        source_adapter="text",
        source_metadata={"note": _SK},
    )
    with pytest.raises(CaptureError) as excinfo:
        capture(vault, req, render=False)
    assert excinfo.value.code == "SECRET_CONTENT"
    assert _SK not in str(excinfo.value)
    assert "openai-api-key" in str(excinfo.value)
    generated = vault / "generated"
    assert not generated.exists() or not any(generated.rglob("*"))
