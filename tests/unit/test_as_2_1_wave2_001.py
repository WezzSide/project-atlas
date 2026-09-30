"""AS-2.1 Wave-2 productionization tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.ask_atlas_live import ask_atlas_live
from project_atlas.authz import AuthzError, elevated_operator
from project_atlas.chatgpt_bridge import bridge_chatgpt_export
from project_atlas.collab_live import append_collab_action, open_collab_session
from project_atlas.obs_live import build_live_observability_receipt
from project_atlas.provider_live import run_local_model_adapter
from project_atlas.web_actions import WebActionError, submit_web_action


def test_chatgpt_bridge(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "chatgpt.md"
    export.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")
    report = bridge_chatgpt_export(vault, export, bridge_id="br-a")
    assert report["chatgpt_bridge"] is True
    assert report["llm_authority"] is False
    assert report["live_chatgpt_api"] is False


def test_collab_session_reconstructable(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    session = open_collab_session(vault, session_id="sess-a", subject="review-item-1")
    assert session["live_collab"] is True
    assert session["network_multiuser"] is False
    updated = append_collab_action(
        vault,
        session_id="sess-a",
        action_name="note",
        detail="looks fine",
    )
    assert len(updated["actions"]) == 2


def test_web_action_requires_capability(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(AuthzError):
        submit_web_action(vault, action_id="act-a", action_type="refresh-status")


def test_web_action_ledger(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("web-op", extra={"web.action"})
    txn = submit_web_action(
        vault,
        action_id="act-a",
        action_type="open-lens",
        payload={"lens": "projects"},
        operator=op,
    )
    assert txn["canonical_write"] is False
    assert txn["authority"] is False
    with pytest.raises(WebActionError, match="authority-fields"):
        submit_web_action(
            vault,
            action_id="act-b",
            action_type="queue-review",
            payload={"promote": True},
            operator=op,
        )


def test_provider_live_local_model(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    op = elevated_operator("prov-op", extra={"provider.live"})
    report = run_local_model_adapter(
        vault, run_id="run-a", prompt="summarize inventory", operator=op
    )
    assert report["prov_live"] is True
    assert report["remote_sdk"] is False
    assert report["llm_authority"] is False


def test_ask_atlas_live(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    (vault / "projects" / "alpha").mkdir(parents=True)
    report = ask_atlas_live(vault, query="alpha")
    assert report["live_ask"] is True
    assert report["canonical_write"] is False
    assert report["matches"]["projects"][0]["project_id"] == "alpha"


def test_ask_atlas_live_matches_knowledge_title(tmp_path: Path) -> None:
    """DEMO-FINDING-001 residual: NL tokens match listing title/summary/value_text."""
    vault = tmp_path / "v"
    (vault / "projects" / "harbor-database").mkdir(parents=True)
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    (answers / "ans-postgres-conflict.json").write_text(
        json.dumps(
            {
                "answer_id": "ans-postgres-conflict",
                "subject": "harbor-database",
                "field": "engine_version",
                "title": "PostgreSQL version conflict",
                "summary": "Unresolved 15 vs 16",
                "value": None,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = ask_atlas_live(vault, query="PostgreSQL")
    assert report["live_ask"] is True
    knowledge = report["matches"]["knowledge"]
    assert len(knowledge) == 1
    assert knowledge[0]["answer_id"] == "ans-postgres-conflict"
    assert knowledge[0]["title"] == "PostgreSQL version conflict"


def test_api_ask_and_actions(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    (vault / "projects" / "beta").mkdir(parents=True)
    op = elevated_operator(
        "api-op",
        extra={"web.action"},
    )
    server = serve_api(vault, host="127.0.0.1", port=0, operator=op)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        read_auth = session_credentials(server).auth_headers()
        priv_auth = session_credentials(server).auth_headers(privileged=True)
        with urlopen(
            Request(
                f"http://{host}:{port}/v1/ask?q={quote('beta')}",
                headers=read_auth,
            ),
            timeout=2,
        ) as resp:
            ask = json.loads(resp.read().decode("utf-8"))
        assert ask["live_ask"] is True
        req = Request(
            f"http://{host}:{port}/v1/actions",
            data=json.dumps(
                {
                    "action_id": "act-live",
                    "action_type": "refresh-status",
                    "payload": {},
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", **priv_auth},
            method="POST",
        )
        with urlopen(req, timeout=2) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["accepted"] is True
    finally:
        server.shutdown()


def test_obs_live_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    report = build_live_observability_receipt(vault)
    assert report["rollup"] == "unknown"
    assert report["authority_plane"] == "none"
