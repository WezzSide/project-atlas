"""EXECUTOR_SDK_OBSERVABILITY: a failed Claude run must leave a sanitized, durable diagnostic.

Regression for live run 36845679958 (E3): the SDK returned ``subtype=success, is_error=true,
num_turns=1, cost 0`` and the only evidence of *why* lived in
``$RUNNER_TEMP/claude-execution-output.json``, which runner cleanup deletes (the public action log
hides message details by design). The workflow now reduces that file to a whitelisted, redacted,
bounded summary on agent-step failure only. These tests run the real inline script
from the workflow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / ".github/workflows/atlas-agent-execute.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(TEXT)
STEPS = WORKFLOW["jobs"]["execute"]["steps"]
NAMES = [s.get("name", s.get("uses")) for s in STEPS]
AGENT = "Run agent (bounded prompt, restricted tools)"
DIAG = "Claude SDK failure diagnostics (sanitized, failure-only)"
UPLOAD = "Upload sanitized SDK diagnostic"
FAILURE_IF = "${{ failure() && steps.agent.outcome == 'failure' }}"

MARKERS = [
    "QZX_MARKER_RESULT_TEXT",
    "QZX_MARKER_ERRORS_ITEM",
    "QZX_MARKER_ASSISTANT_TEXT",
    "QZX_MARKER_TOOL_INPUT",
    "QZX_MARKER_NESTED",
    "QZX_MARKER_PROVIDER_BODY",
    "QZX_MARKER_TYPE",
    "QZX_MARKER_KEYSRC",
    "QZX_MARKER_MCP",
    "QZX_MARKER_USAGE_KEY",
    "QZX_MARKER_MODEL",
    "SENTINEL_PROMPT_TEXT_do_not_leak",
    "SENTINEL_TOOL_PAYLOAD_do_not_leak",
    "sk-ant-api03-SENTINELKEY1234567890abcdef",
    "ghs_SENTINELTOKEN0123456789abcdefABCD",
]
SECRETS = [
    "sk-ant-api03-SENTINELKEY1234567890abcdef",
    "ghs_SENTINELTOKEN0123456789abcdefABCD",
    "SENTINEL_PROMPT_TEXT_do_not_leak",
    "SENTINEL_TOOL_PAYLOAD_do_not_leak",
    "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5",
]


def _step(name: str) -> dict:
    return STEPS[NAMES.index(name)]


def _script() -> str:
    run = _step(DIAG)["run"]
    head = "python3 -I - <<'ATLAS_SDK_DIAG'\n"
    assert head in run
    body = run.split(head, 1)[1]
    return body.rsplit("\nATLAS_SDK_DIAG", 1)[0]


LOCAL_PROBE = "https://127.0.0.1:9/"  # deterministic refused connection, no network needed


def _exec(tmp: Path, *, probe_url: str = LOCAL_PROBE, outcome: str = "failure"):
    """Run a test-transformed copy of the inline script (probe URL swapped in the copy only)."""
    script = _script()
    assert 'PROBE_URL = "https://api.anthropic.com/"' in script
    script = script.replace(
        'PROBE_URL = "https://api.anthropic.com/"', f'PROBE_URL = "{probe_url}"'
    )
    env = {"DIAG_TEMP": str(tmp), "AGENT_OUTCOME": outcome, "PATH": ""}
    proc = subprocess.run(
        [sys.executable, "-I", "-"],
        input=script,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def _run(tmp: Path, messages, *, raw: str | None = None, outcome: str = "failure") -> dict:
    src = tmp / "claude-execution-output.json"
    if raw is not None:
        src.write_text(raw, encoding="utf-8")
    elif messages is not None:
        src.write_text(json.dumps(messages), encoding="utf-8")
    proc = _exec(tmp, outcome=outcome)
    out = tmp / "claude-sdk-diagnostic.json"
    assert out.exists()
    artifact = out.read_text(encoding="utf-8")
    for s in MARKERS:
        assert s not in proc.stdout, f"free text {s!r} reached the job log"
        assert s not in artifact, f"free text {s!r} persisted"
    assert len(artifact.encode("utf-8")) <= 16 * 1024 + 1
    data = json.loads(artifact)  # must ALWAYS parse
    assert isinstance(data["diagnostic_truncated"], bool)
    return data


def _e3_like(extra_result: dict | None = None):
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "duration_ms": 191374,
        "duration_api_ms": 0,
        "num_turns": 1,
        "total_cost_usd": 0,
        "modelUsage": {},
        "permission_denials": [],
        "stop_reason": None,
        "result": "API Error: 401 QZX_MARKER_RESULT_TEXT " + SECRETS[0],
        "errors": ["QZX_MARKER_ERRORS_ITEM", {"detail": "QZX_MARKER_NESTED"}],
        "api_error_status": 401,
    }
    result.update(extra_result or {})
    return [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-opus-5-5",
            "apiKeySource": "ANTHROPIC_API_KEY",
            "claude_code_version": "2.1.283",
            "permissionMode": "default",
            "tools": ["Read", "Edit"],
            "mcp_servers": [{"name": "github", "status": "connected"}],
        },
        {"type": "user", "message": {"role": "user", "content": SECRETS[2]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "QZX_MARKER_ASSISTANT_TEXT"}]},
        },
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 1,
            "max_retries": 10,
            "retry_delay_ms": 500,
            "error_status": None,
            "error": "unknown",
        },
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 2,
            "max_retries": 10,
            "retry_delay_ms": 1000,
            "error_status": 401,
            "error": "authentication_failed",
        },
        {
            "type": "assistant",
            "error": "authentication_failed",
            "message": {
                "stop_reason": None,
                "content": [
                    {"type": "text", "text": SECRETS[2]},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {
                            "command": SECRETS[3],
                            "env": SECRETS[1],
                            "x": "QZX_MARKER_TOOL_INPUT",
                        },
                    },
                ],
            },
        },
        result,
    ]


# -- workflow contract ----------------------------------------------------------------------
def test_agent_step_has_an_id_and_diagnostics_follow_it_directly():
    assert _step(AGENT)["id"] == "agent"
    i = NAMES.index(AGENT)
    assert NAMES[i + 1] == DIAG and NAMES[i + 2] == UPLOAD
    assert NAMES[i + 3] == "Deterministic tests (infra runner subset)"


def test_diagnostics_run_only_after_a_failed_agent_step_and_cannot_mask_the_failure():
    for name in (DIAG, UPLOAD):
        assert _step(name)["if"] == FAILURE_IF
        assert _step(name)["continue-on-error"] is True
    assert "continue-on-error" not in _step(AGENT)


def test_normal_success_path_steps_are_unchanged():
    for name in (
        "Deterministic tests (infra runner subset)",
        "Commit and push dedicated branch",
        "Smoke workload (evidence fragment)",
        "Upload executor evidence",
    ):
        step = _step(name)
        assert "if" not in step and "continue-on-error" not in step
    with_ = _step(AGENT)["with"]
    assert with_["github_token"] == "${{ secrets.GITHUB_TOKEN }}"
    assert with_["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert "show_full_output" not in TEXT.replace("show_full_output stays off", "")
    assert WORKFLOW["permissions"] == {"contents": "write"}


def test_diagnostic_step_gets_no_secrets_and_runs_inline_not_from_the_checkout():
    step = _step(DIAG)
    assert step["env"] == {
        "AGENT_OUTCOME": "${{ steps.agent.outcome }}",
        "DIAG_TEMP": "${{ runner.temp }}",
    }
    assert "secrets." not in yaml.safe_dump(step)
    assert "infra/atlas-runner" not in step["run"] and "scripts/" not in step["run"]


def test_no_production_bypass_or_free_text_primitives_in_the_script():
    script = "\n".join(ln for ln in _script().splitlines() if not ln.lstrip().startswith("#"))
    assert "ATLAS_DIAG_SKIP_PROBE" not in script and "SKIP" not in script.upper().replace(
        "SKIPPED", ""
    )
    for forbidden in ("repr(", "str(v", "error_text", "[:MAX_OUT]", "text[:"):
        assert forbidden not in script
    assert "environ.get(" in script and script.count("os.environ") == 2  # DIAG_TEMP, AGENT_OUTCOME


def test_only_the_sanitized_summary_is_uploaded_never_the_transcript():
    up = _step(UPLOAD)
    assert up["with"]["path"] == "${{ runner.temp }}/claude-sdk-diagnostic.json"
    assert up["with"]["name"] == "atlas-agent-sdk-diagnostic"
    assert "claude-execution-output" not in up["with"]["path"]
    assert "claude-execution-output" not in str(up["with"]["name"])
    assert up["uses"].split(" ")[0].startswith("actions/upload-artifact@")


# -- behaviour of the real script -------------------------------------------------------------
def test_e3_like_failure_keeps_classification_fields_and_drops_all_content(tmp_path):
    d = _run(tmp_path, _e3_like())
    r = d["result"]
    assert (r["subtype"], r["is_error"], r["num_turns"], r["total_cost_usd"]) == (
        "success",
        True,
        1,
        0,
    )
    assert r["duration_ms"] == 191374 and r["permission_denials_count"] == 0
    assert r["model_usage"] == {} and r["api_error_status"] == 401 and r["stop_reason"] is None
    assert (
        "error_text" not in r and r["errors_count"] == 2 and r["errors_enums"] == ["other", "other"]
    )
    assert d["init"]["model"] == "claude-opus-5-5" and d["init"]["tools_count"] == 2
    assert (
        d["init"]["claude_code_version"] == "2.1.283"
        and d["init"]["apiKeySource"] == "ANTHROPIC_API_KEY"
    )
    assert d["init"]["mcp_status_counts"] == {"connected": 1}
    assert d["api_retry_total"] == 2 and d["api_retries"][1]["error"] == "authentication_failed"
    assert d["assistant_errors"] == {"authentication_failed": 1}
    assert {"API_AUTH", "API_CONNECTION_NO_HTTP_RESPONSE"} <= set(d["classification_hints"])
    assert d["agent_step_outcome"] == "failure" and d["execution_file_present"] is True
    assert d["diagnostic_truncated"] is False and d["api_probe"]["reachable"] is False
    text = json.dumps(d)
    assert '"content"' not in text and "tool_use" not in text and '"input"' not in text


def test_probe_failure_is_a_network_hint_and_probe_always_runs(tmp_path):
    d = _run(tmp_path, _e3_like())
    assert "skipped" not in d["api_probe"]
    assert "NETWORK_OR_TLS_TO_API_FAILED" in d["classification_hints"]


def test_environment_cannot_suppress_the_probe(tmp_path):
    (tmp_path / "claude-execution-output.json").write_text("[]", encoding="utf-8")
    script = _script().replace(
        'PROBE_URL = "https://api.anthropic.com/"', f'PROBE_URL = "{LOCAL_PROBE}"'
    )
    env = {
        "DIAG_TEMP": str(tmp_path),
        "AGENT_OUTCOME": "failure",
        "PATH": "",
        "ATLAS_DIAG_SKIP_PROBE": "1",
        "SKIP_PROBE": "1",
    }
    subprocess.run(
        [sys.executable, "-I", "-"],
        input=script,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
        check=True,
    )
    d = json.loads((tmp_path / "claude-sdk-diagnostic.json").read_text(encoding="utf-8"))
    assert d["api_probe"]["reachable"] is False


def test_free_text_in_every_field_is_structurally_excluded(tmp_path):
    hostile_text = "QZX_MARKER_PROVIDER_BODY secret-ish free text"
    msgs = [
        {"type": "QZX_MARKER_TYPE", "subtype": "QZX_MARKER_TYPE"},
        {
            "type": "system",
            "subtype": "init",
            "model": "QZX_MARKER_MODEL",
            "apiKeySource": "QZX_MARKER_KEYSRC",
            "permissionMode": hostile_text,
            "claude_code_version": hostile_text,
            "tools": ["QZX_MARKER_TOOL_INPUT"],
            "mcp_servers": [{"name": "QZX_MARKER_MCP", "status": "QZX_MARKER_MCP"}],
        },
        {"type": "assistant", "error": hostile_text, "message": hostile_text},
        {
            "type": "result",
            "subtype": hostile_text,
            "is_error": True,
            "result": hostile_text,
            "stop_reason": hostile_text,
            "terminal_reason": hostile_text,
            "errors": [hostile_text, {"a": hostile_text}, [hostile_text], None, 7],
            "modelUsage": {"QZX_MARKER_USAGE_KEY": {"inputTokens": 5, "note": hostile_text}},
            "permission_denials": [hostile_text],
        },
    ]
    d = _run(tmp_path, msgs)
    assert d["init"]["model"] == d["init"]["apiKeySource"] == "other"
    assert d["init"]["mcp_status_counts"] == {"other": 1}
    assert d["result"]["errors_enums"] == ["other"] * 5
    assert d["result"]["model_usage"] == {"other": {"inputTokens": 5}}
    assert d["result"]["subtype"] == "other" and d["result"]["stop_reason"] == "other"


def test_wrongly_typed_and_hostile_structures_never_stop_the_artifact(tmp_path):
    msgs = [
        "QZX_MARKER_NESTED",
        5,
        None,
        [1, 2],
        {"type": ["QZX_MARKER_TYPE"], "subtype": {"a": 1}},
        {"type": "assistant", "error": {"x": "QZX_MARKER_NESTED"}},
        {"type": "assistant", "error": ["QZX_MARKER_NESTED"]},
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": "QZX_MARKER_NESTED",
            "error": {"k": "QZX_MARKER_NESTED"},
            "error_status": float("nan"),
            "retry_delay_ms": 10**3000,
        },
        {"type": "system", "subtype": "init", "tools": "QZX_MARKER_TOOL_INPUT", "mcp_servers": "x"},
        {
            "type": "result",
            "modelUsage": ["QZX_MARKER_USAGE_KEY"],
            "errors": "QZX_MARKER_ERRORS_ITEM",
            "permission_denials": "QZX_MARKER_NESTED",
            "duration_ms": "QZX_MARKER_NESTED",
            "total_cost_usd": True,
        },
    ]
    d = _run(tmp_path, msgs)
    assert d["message_count"] == len(msgs) and d["malformed_messages"] >= 4
    r = d["result"]
    assert r["duration_ms"] is None and r["total_cost_usd"] is None and r["errors_count"] == 0
    assert (
        d["api_retries"][0]["error_status"] is None
        and d["api_retries"][0]["retry_delay_ms"] is None
    )


def test_unicode_control_chars_and_huge_strings_are_excluded_and_bounded(tmp_path):
    nasty = "QZX_MARKER_RESULT_TEXT\x00\x1b[31m\u202e\ud83d" + "A" * 5_000_000
    msgs = _e3_like({"result": nasty, "terminal_reason": nasty, "stop_reason": nasty})
    msgs.insert(1, {"type": nasty, "subtype": nasty, "error": nasty})
    d = _run(tmp_path, msgs)
    assert d["result"]["terminal_reason"] == "other"


def test_size_pressure_always_yields_valid_json(tmp_path):
    msgs = [{"type": f"t{i}"} for i in range(5000)]
    msgs += [
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": i,
            "error_status": 529,
            "error": "overloaded",
        }
        for i in range(500)
    ]
    msgs += [
        {
            "type": "result",
            "is_error": True,
            "modelUsage": {f"claude-m{i}": {"inputTokens": i} for i in range(1000)},
        }
    ]
    d = _run(tmp_path, msgs)
    assert d["api_retry_total"] == 500 and len(d["api_retries"]) <= 25 and len(d["types"]) <= 21
    assert "API_OVERLOADED" in d["classification_hints"]


def test_overflow_falls_back_to_a_minimal_valid_object(tmp_path):
    script = (
        _script().replace("MAX_OUT, MAX_RETRY", "MAX_OUT, MAX_RETRY").replace("16 * 1024", "400")
    )
    (tmp_path / "claude-execution-output.json").write_text(json.dumps(_e3_like()), encoding="utf-8")
    script = script.replace(
        'PROBE_URL = "https://api.anthropic.com/"', f'PROBE_URL = "{LOCAL_PROBE}"'
    )
    env = {"DIAG_TEMP": str(tmp_path), "AGENT_OUTCOME": "failure", "PATH": ""}
    subprocess.run(
        [sys.executable, "-I", "-"],
        input=script,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
        check=True,
    )
    d = json.loads((tmp_path / "claude-sdk-diagnostic.json").read_text(encoding="utf-8"))
    assert d["diagnostic_truncated"] is True and d["schema"] == 1
    assert "classification_hints" in d and "api_probe" in d and "init" not in d


def test_missing_corrupt_and_wrong_shape_files_still_produce_a_diagnostic(tmp_path):
    for sub in "abcde":
        (tmp_path / sub).mkdir()
    assert _run(tmp_path / "a", None)["execution_file_present"] is False
    assert _run(tmp_path / "b", None, raw="{not json")["parse_error"] == "JSONDecodeError"
    assert _run(tmp_path / "c", None, raw='{"a": 1}')["parse_error"] == "not_a_message_list"
    assert _run(tmp_path / "d", None, raw="[" * 100000)["parse_error"] in {
        "RecursionError",
        "JSONDecodeError",
    }
    (tmp_path / "e" / "claude-execution-output.json").write_bytes(
        b"\xff\xfe QZX_MARKER_RESULT_TEXT"
    )
    assert _run(tmp_path / "e", None, raw=None)["parse_error"] == "UnicodeDecodeError"


def test_unknown_agent_outcome_is_bounded(tmp_path):
    (tmp_path / "claude-execution-output.json").write_text("[]", encoding="utf-8")
    d = _run(tmp_path, None, outcome="QZX_MARKER_NESTED")
    assert d["agent_step_outcome"] == "other"
