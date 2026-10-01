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


def _run(tmp: Path, messages, *, raw: str | None = None, outcome: str = "failure") -> dict:
    src = tmp / "claude-execution-output.json"
    if raw is not None:
        src.write_text(raw, encoding="utf-8")
    elif messages is not None:
        src.write_text(json.dumps(messages), encoding="utf-8")
    env = {
        "RUNNER_TEMP": str(tmp),
        "AGENT_OUTCOME": outcome,
        "ATLAS_DIAG_SKIP_PROBE": "1",
        "PATH": "",
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-"],
        input=_script(),
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = tmp / "claude-sdk-diagnostic.json"
    assert out.exists()
    for s in SECRETS:
        assert s not in proc.stdout, "secret/prompt content reached the job log"
        assert s not in out.read_text(encoding="utf-8"), "secret/prompt content persisted"
    return json.loads(out.read_text(encoding="utf-8"))


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
        "result": "API Error: 401 invalid x-api-key " + SECRETS[0],
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
                        "input": {"command": SECRETS[3], "env": SECRETS[1]},
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
    assert set(step["env"]) == {"AGENT_OUTCOME"}
    assert "secrets." not in yaml.safe_dump(step)
    assert "infra/atlas-runner" not in step["run"] and "scripts/" not in step["run"]


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
    assert (
        r["duration_ms"] == 191374 and r["permission_denials_count"] == 0 and r["model_usage"] == {}
    )
    assert r["api_error_status"] == 401 and r["stop_reason"] is None
    assert "[REDACTED]" in r["error_text"] and SECRETS[0] not in r["error_text"]
    assert d["init"]["model"] == "claude-opus-5-5" and d["init"]["tools_count"] == 2
    assert d["api_retry_total"] == 2 and d["api_retries"][1]["error"] == "authentication_failed"
    assert d["assistant_errors"] == {"authentication_failed": 1}
    assert {"API_AUTH", "API_CONNECTION_NO_HTTP_RESPONSE"} <= set(d["classification_hints"])
    assert d["agent_step_outcome"] == "failure" and d["execution_file_present"] is True
    assert d["api_probe"] == {"skipped": True}
    text = json.dumps(d)
    assert '"content"' not in text and "tool_use" not in text and '"input"' not in text


def test_non_error_result_text_is_never_kept(tmp_path):
    msgs = _e3_like({"is_error": False, "result": "SENTINEL_PROMPT_TEXT_do_not_leak final answer"})
    d = _run(tmp_path, msgs)
    assert "error_text" not in d["result"]


def test_unknown_error_enums_and_oversized_strings_are_bounded(tmp_path):
    msgs = _e3_like({"errors": ["x" * 5000, "e2"], "terminal_reason": "t" * 500})
    msgs.insert(1, {"type": "assistant", "error": "SENTINEL_TOOL_PAYLOAD_do_not_leak"})
    d = _run(tmp_path, msgs)
    assert d["assistant_errors"].get("other") == 1
    assert (
        all(len(e) <= 400 for e in d["result"]["errors"])
        and len(d["result"]["terminal_reason"]) <= 60
    )
    assert (tmp_path / "claude-sdk-diagnostic.json").stat().st_size <= 16 * 1024 + 2


def test_many_api_retries_stay_within_the_size_cap(tmp_path):
    msgs = _e3_like() + [
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": i,
            "max_retries": 99,
            "retry_delay_ms": i,
            "error_status": 529,
            "error": "overloaded",
        }
        for i in range(500)
    ]
    d = _run(tmp_path, msgs)
    assert d["api_retry_total"] == 502 and len(d["api_retries"]) <= 25
    assert "API_OVERLOADED" in d["classification_hints"]


def test_missing_corrupt_and_wrong_shape_files_still_produce_a_diagnostic(tmp_path):
    d = _run(tmp_path / "a", None) if (tmp_path / "a").mkdir() is None else None
    assert d["execution_file_present"] is False
    (tmp_path / "b").mkdir()
    assert _run(tmp_path / "b", None, raw="{not json")["parse_error"] == "JSONDecodeError"
    (tmp_path / "c").mkdir()
    assert _run(tmp_path / "c", None, raw='{"a": 1}')["parse_error"] == "not_a_message_list"


def test_failed_probe_is_recorded_as_a_network_hint(tmp_path):
    src = tmp_path / "claude-execution-output.json"
    src.write_text(json.dumps(_e3_like()), encoding="utf-8")
    script = _script().replace('"https://api.anthropic.com/"', '"https://127.0.0.1:9/"')
    env = {"RUNNER_TEMP": str(tmp_path), "AGENT_OUTCOME": "failure", "PATH": ""}
    proc = subprocess.run(
        [sys.executable, "-I", "-"],
        input=script,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    d = json.loads((tmp_path / "claude-sdk-diagnostic.json").read_text(encoding="utf-8"))
    assert (
        d["api_probe"]["reachable"] is False
        and "NETWORK_OR_TLS_TO_API_FAILED" in d["classification_hints"]
    )
