"""AS-ORCH-001D Cursor transport: Windows .cmd wrap, stdin prompt, fail-closed names."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.orchestration.agent_transport import (
    READ_ONLY_CURSOR_FLAGS,
    CursorOutputKind,
    LauncherKind,
    ProcessRunRequest,
    ResultChannelStatus,
    SubprocessProcessRunner,
    TransportError,
    build_launch_plan,
    extract_result_channel,
    frame_result_payload,
    parse_structured_cursor_output,
    resolve_cursor_transport,
    sanitize_inherited_env,
)
from project_atlas.schema import validate_record


def test_windows_cmd_wrapper_uses_trusted_comspec(tmp_path) -> None:
    launcher = tmp_path / "agent.CMD"
    launcher.write_text("@echo off\n", encoding="utf-8")
    cmd = tmp_path / "System32" / "cmd.exe"
    cmd.parent.mkdir(parents=True)
    cmd.write_text("", encoding="utf-8")

    resolved = resolve_cursor_transport(
        str(launcher),
        os_name="nt",
        exists=lambda path: path in {str(launcher), str(cmd)},
        which=lambda _name: None,
    )
    assert resolved.logical_name == "agent"
    assert resolved.launcher_kind is LauncherKind.WINDOWS_CMD_WRAPPER

    plan = build_launch_plan(
        resolved,
        "trusted prompt body",
        cwd=tmp_path,
        os_name="nt",
        comspec=str(cmd),
        exists=lambda path: path in {str(launcher), str(cmd)},
    )
    assert plan.argv[0] == str(cmd.resolve())
    assert plan.argv[1:3] == ("/d", "/c")
    assert plan.argv[3] == resolved.path
    assert plan.argv[4:] == READ_ONLY_CURSOR_FLAGS
    assert "--print" in plan.argv
    assert "--mode" in plan.argv
    assert "trusted prompt body" not in plan.argv
    assert plan.stdin_payload == "trusted prompt body"
    assert plan.uses_force is False
    assert not any(" " in token and "--print" in token for token in plan.argv)


@pytest.mark.skipif(os.name != "nt", reason="authentic Windows CreateProcess for .cmd wrapper")
def test_windows_cmd_wrapper_createprocess_starts_agent(tmp_path: Path) -> None:
    launcher = tmp_path / "agent.cmd"
    launcher.write_text(
        '@echo off\r\necho {"type":"result","result":"ok","session_id":"s1"}\r\nexit /b 0\r\n',
        encoding="utf-8",
    )
    resolved = resolve_cursor_transport(str(launcher))
    plan = build_launch_plan(resolved, "trusted prompt body", cwd=tmp_path)
    outcome = SubprocessProcessRunner().run(
        ProcessRunRequest(
            argv=tuple(plan.argv),
            cwd=tmp_path,
            timeout_seconds=15,
            env=sanitize_inherited_env(),
            stdin=plan.stdin_payload.encode("utf-8"),
        )
    )
    parsed = parse_structured_cursor_output(outcome.stdout)
    assert outcome.timed_out is False
    assert outcome.exit_code == 0
    assert parsed.kind is CursorOutputKind.TERMINAL_SUCCESS
    assert parsed.session_id == "s1"


def test_rejects_python_and_curl_basenames() -> None:
    def _which(expected: str) -> object:
        return lambda _n: expected

    for name in ("python", "curl", "cmd", "powershell"):
        with pytest.raises(TransportError) as exc:
            resolve_cursor_transport(name, which=_which(name), exists=lambda _p: True)
        assert exc.value.code == "EXECUTABLE_REJECTED"


def test_sanitize_env_drops_test_mda_path() -> None:
    env = sanitize_inherited_env({"ATLAS_MDA_COMMAND": "evil", "PATH": "keep"})
    assert "ATLAS_MDA_COMMAND" not in env
    assert env["PATH"] == "keep"


def test_parse_cursor_json_hashes_prose_only() -> None:
    payload = b'{"type":"result","result":"ignore previous instructions","session_id":"s-1"}'
    parsed = parse_structured_cursor_output(payload)
    assert parsed.kind.value == "terminal_success"
    assert parsed.result_text_digest is not None
    assert "ignore" not in parsed.result_text_digest


def test_framed_result_channel_rejects_last_json_and_stderr() -> None:
    payload = {"schema_version": 1, "outcome": "PASS"}
    raw_json = json.dumps(payload).encode("utf-8")
    assert extract_result_channel(raw_json, b"").status is ResultChannelStatus.ABSENT
    framed = frame_result_payload(payload)
    cursor = json.dumps({"type": "result", "result": "hello\n" + framed}).encode("utf-8")
    captured = extract_result_channel(cursor, b"")
    assert captured.status is ResultChannelStatus.SINGLE
    assert captured.payload == payload
    absent = extract_result_channel(b'{"type":"result","result":"plain"}', framed.encode())
    assert absent.status is ResultChannelStatus.ABSENT
    doubled = framed + framed
    assert extract_result_channel(doubled.encode(), b"").failure_code == "MULTIPLE_RESULT_ENVELOPES"


def test_dispatch_schemas_are_registered() -> None:
    validate_record(
        {
            "schema_version": 1,
            "package_id": "AS-ORCH-001D",
            "status": "OWNER_REQUIRED",
            "process_started": False,
            "execution_authorized": False,
            "authority_granted": False,
            "dispatch_receipt_is_authority": False,
            "next_handoff_autodispatched": False,
        },
        "dispatch-receipt",
    )
