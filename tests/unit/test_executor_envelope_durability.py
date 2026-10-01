"""EXECUTOR_TURN_CAP_DISCARDS_SUCCESSFUL_RESULT: a completed model success must stay recoverable.

Regression for live run 36905529693 (DEVQ-0002-E1): the SDK result was a completed success
(25 turns, is_error=false) but the action wrapper failed the step because the turn cap (20) was
exceeded; the push step was skipped and the product bytes were lost. The workflow now classifies
that exact structured class from the sanitized diagnostic and persists the working tree to the
dedicated agent branch as *candidate only* (no acceptance/verification/integration/merge
authority); the agent step is not masked, so the job stays red.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / ".github/workflows/atlas-agent-execute.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(TEXT)
STEPS = WORKFLOW["jobs"]["execute"]["steps"]
NAMES = [s.get("name", s.get("uses")) for s in STEPS]
AGENT = "Run agent (bounded prompt, restricted tools)"
UPLOAD_DIAG = "Upload sanitized SDK diagnostic"
PUSH = "Commit and push dedicated branch"
DIAG = "Claude SDK failure diagnostics (sanitized, failure-only)"
PERSIST = "Persist envelope-rejected result on the dedicated branch (candidate only)"
UPLOAD_SALVAGE = "Upload envelope salvage evidence"
INFRA = "Infra runner suite (corroboration only, non-gating, outcome recorded)"

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="executes the Linux worker bash run blocks"
)


def _step(name: str) -> dict:
    return STEPS[NAMES.index(name)]


def _diag_script() -> str:
    run = _step(DIAG)["run"]
    head = "\"$PY\" -I -S - <<'ATLAS_SDK_DIAG'\n"
    assert head in run
    script = run.split(head, 1)[1].rsplit("\nATLAS_SDK_DIAG", 1)[0]
    probe = 'PROBE_URL = "https://api.anthropic.com/"'
    assert probe in script
    return script.replace(probe, 'PROBE_URL = "https://127.0.0.1:9/"')  # offline, test copy only


# -- workflow contract ----------------------------------------------------------------------------
SALVAGE_COND = (
    "failure() && steps.agent.outcome == 'failure' && steps.sdk_diag.outcome == 'success' "
    "&& steps.sdk_diag.outputs.envelope_salvage == 'true'"
)


def test_step_order_and_placement():
    i = NAMES.index(PUSH)
    assert NAMES[i + 1 : i + 3] == [PERSIST, UPLOAD_SALVAGE]
    assert NAMES.index(UPLOAD_SALVAGE) < NAMES.index(INFRA)
    assert not any("Classify" in n for n in NAMES), "no second security decision"


def test_agent_step_is_not_masked_and_normal_push_is_unchanged():
    assert "continue-on-error" not in _step(AGENT)
    push = _step(PUSH)
    assert "if" not in push and "continue-on-error" not in push


def test_persist_condition_requires_trusted_sdk_diag_output():
    persist = _step(PERSIST)
    assert persist["if"].replace("${{", "").replace("}}", "").strip() == SALVAGE_COND
    assert persist["continue-on-error"] is True
    assert "steps.sdk_diag.outputs.envelope_salvage == 'true'" in persist["if"]
    up = _step(UPLOAD_SALVAGE)
    assert "salvage_persist.outcome == 'success'" in up["if"] and up["continue-on-error"] is True


def test_salvage_inputs_originate_only_from_sdk_diag_step_outputs():
    persist = _step(PERSIST)
    env = persist["env"]
    assert env["NUM_TURNS"] == "${{ steps.sdk_diag.outputs.envelope_num_turns }}"
    assert env["DENIALS"] == "${{ steps.sdk_diag.outputs.envelope_permission_denials }}"
    # no salvage fact derives from GITHUB_ENV, files, or an agent-addressable env variable
    assert "GITHUB_ENV" not in persist["run"] and "claude-sdk-diagnostic" not in persist["run"]
    assert "salvage_classify" not in TEXT
    # the only writer of the envelope outputs is the sdk_diag script (via GITHUB_OUTPUT)
    writers = [
        n for n, s in zip(NAMES, STEPS, strict=True) if "envelope_salvage=" in str(s.get("run"))
    ]
    assert writers == [DIAG]


def test_max_turns_literal_matches_agent_cap_in_both_steps():
    cap = re.search(r"--max-turns (\d+)", _step(AGENT)["with"]["claude_args"])
    assert cap
    for name in (DIAG, PERSIST):
        assert _step(name)["env"]["MAX_TURNS"] == cap.group(1), name


def test_only_persist_step_gets_the_token_and_diag_has_none():
    assert "secrets." not in yaml.safe_dump(_step(DIAG))
    assert "secrets.GITHUB_TOKEN" in yaml.safe_dump(_step(PERSIST))
    assert "secrets." not in yaml.safe_dump(_step(UPLOAD_SALVAGE))


def test_allowlist_is_narrow():
    args = _step(AGENT)["with"]["claude_args"]
    assert "Bash(python:*)" not in args and "Bash(python3:*)" not in args
    assert "Bash(mypy:*)" in args and "Bash(pytest:*)" in args and "Bash(ruff:*)" in args


# -- allowlist vs rendered acceptance command form ----------
def _allowed_prefixes() -> list[str]:
    args = shlex.split(_step(AGENT)["with"]["claude_args"])
    tools = args[args.index("--allowedTools") + 1 :]
    out = []
    for t in tools:
        if t.startswith("--"):
            break
        m = re.fullmatch(r"Bash\((.+):\*\)", t)
        if m:
            out.append(m.group(1))
    return out


def runnable_under_allowlist(cmd: str) -> bool:
    """A command is runnable only if it is a plain invocation of an allowlisted program."""
    first = cmd.strip().split()[0] if cmd.strip() else ""
    return first in _allowed_prefixes()


@pytest.mark.parametrize(
    "cmd",
    [
        "pytest tests/unit/test_orchestration_dev_github_port.py -q",
        "ruff check src tests",
        "ruff format --check src tests",
        "mypy src",
    ],
)
def test_canonical_acceptance_commands_are_executable(cmd):
    assert runnable_under_allowlist(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "python -m pytest -q",
        "python -m ruff check .",
        "python -m mypy src",
        "PYTHONPATH=src pytest",
    ],
)
def test_noncanonical_forms_are_not_executable(cmd):
    assert not runnable_under_allowlist(cmd)


# -- sdk_diag-derived salvage predicate (single canonical derivation) ----
def _result(**over):
    r = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "terminal_reason": "completed",
        "stop_reason": "end_turn",
        "errors": [],
        "num_turns": 25,
        "permission_denials": [{}] * 9,
    }
    r.update(over)
    return r


def _msgs(**over):
    return [{"type": "system", "subtype": "init", "model": "claude-opus-5-5"}, _result(**over)]


def _run_diag(
    tmp_path: Path,
    msgs,
    *,
    raw: str | None = None,
    max_turns: str | None = "20",
    outcome: str = "failure",
    pre_diag: str | None = None,
    pre_diag_readonly: bool = False,
    pre_diag_as_dir: bool = False,
    src_readonly: bool = False,
    gh_output: bool = True,
):
    tmp = tmp_path / "rt"
    tmp.mkdir(exist_ok=True)
    out_file = tmp_path / "gh_output"
    out_file.write_text("")
    diag = tmp / "claude-sdk-diagnostic.json"
    if pre_diag_as_dir:
        diag.mkdir()
    elif pre_diag is not None:
        diag.write_text(pre_diag, encoding="utf-8")
        if pre_diag_readonly:
            diag.chmod(0o444)
    src = tmp / "claude-execution-output.json"
    if raw is not None:
        src.write_text(raw, encoding="utf-8")
    elif msgs is not None:
        src.write_text(json.dumps(msgs), encoding="utf-8")
    if src_readonly and src.exists():
        src.chmod(0o444)
    env = {"DIAG_TEMP": str(tmp), "AGENT_OUTCOME": outcome, "PATH": ""}
    if max_turns is not None:
        env["MAX_TURNS"] = max_turns
    if gh_output:
        env["GITHUB_OUTPUT"] = str(out_file)
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-"],
        input=_diag_script(),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    outputs = dict(
        ln.split("=", 1) for ln in out_file.read_text(encoding="utf-8").splitlines() if ln
    )
    return proc, outputs, diag


FORGED_SALVAGEABLE = json.dumps(
    {
        "schema": 1,
        "agent_step_outcome": "failure",
        "diagnostic_truncated": False,
        "result": {
            "subtype": "success",
            "is_error": False,
            "terminal_reason": "completed",
            "stop_reason": "end_turn",
            "errors_count": 0,
            "num_turns": 99,
            "permission_denials_count": 0,
        },
    }
)
FORGED_NONSALVAGEABLE = json.dumps({"schema": 1, "agent_step_outcome": "failure"})


@_POSIX_ONLY
def test_e1_exact_semantic_fixture_yields_trusted_salvage_outputs(tmp_path):
    proc, out, diag = _run_diag(tmp_path, _msgs())  # 25 turns, max 20, 9 denials
    assert proc.returncode == 0, proc.stderr
    assert out == {
        "envelope_salvage": "true",
        "envelope_num_turns": "25",
        "envelope_permission_denials": "9",
    }
    assert json.loads(diag.read_text(encoding="utf-8"))["result"]["num_turns"] == 25


@_POSIX_ONLY
@pytest.mark.parametrize(
    "over",
    [
        {"is_error": True},
        {"is_error": None},
        {"subtype": "error_max_turns"},
        {"subtype": "error_during_execution"},
        {"terminal_reason": "max_turns"},
        {"terminal_reason": "model_error"},
        {"stop_reason": "max_tokens"},
        {"stop_reason": "tool_use"},
        {"errors": ["authentication_failed"]},
        {"num_turns": 20},
        {"num_turns": 3},
        {"num_turns": True},
        {"num_turns": "25"},
        {"num_turns": None},
        {"num_turns": 10**9},
    ],
)
def test_negative_controls_do_not_salvage(tmp_path, over):
    proc, out, _ = _run_diag(tmp_path, _msgs(**over))
    assert proc.returncode == 0, proc.stderr
    assert out == {"envelope_salvage": "false"}


@_POSIX_ONLY
def test_agent_step_must_have_failed_and_budget_must_be_valid(tmp_path):
    _, out, _ = _run_diag(tmp_path, _msgs(), outcome="success")
    assert out == {"envelope_salvage": "false"}
    for bad in (None, "", "abc", "0", "-1", "20; echo", "999999"):
        _, out, _ = _run_diag(tmp_path, _msgs(), max_turns=bad)
        assert out == {"envelope_salvage": "false"}, bad


@_POSIX_ONLY
def test_missing_or_malformed_current_execution_data_never_salvages(tmp_path):
    for raw in ("", "not json", "{}", "[]", '[{"type":"result"}]', '["x", 1, null]'):
        _, out, _ = _run_diag(tmp_path, None, raw=raw)
        assert out == {"envelope_salvage": "false"}, raw
    _, out, _ = _run_diag(tmp_path, None)  # no execution file at all
    assert out == {"envelope_salvage": "false"}


@_POSIX_ONLY
def test_oversized_execution_data_never_salvages(tmp_path):
    big = json.dumps(_msgs() + [{"type": "assistant", "x": "y" * 400}] * 50000)
    assert len(big) < 64 * 1024 * 1024
    _, out, _ = _run_diag(tmp_path, None, raw=big)
    assert out["envelope_salvage"] in {"true", "false"}  # parsed fine: still bounded outputs
    # message-count pressure that overflows the bounded diagnostic must fail closed
    hostile = [{"type": "result", **_result(modelUsage={f"claude-m{i}": {} for i in range(5000)})}]
    _, out, _ = _run_diag(tmp_path, hostile)
    assert set(out) <= {"envelope_salvage", "envelope_num_turns", "envelope_permission_denials"}


# -- old vulnerability: agent-controlled stale diagnostic has no authority ----
@_POSIX_ONLY
def test_adv1_precreated_valid_looking_diagnostic_cannot_authorize(tmp_path):
    proc, out, diag = _run_diag(
        tmp_path, _msgs(is_error=True), pre_diag=FORGED_SALVAGEABLE
    )  # current SDK result is NOT salvageable
    assert proc.returncode == 0, proc.stderr
    assert out == {"envelope_salvage": "false"}
    assert json.loads(diag.read_text(encoding="utf-8"))["result"]["is_error"] is True  # replaced


@_POSIX_ONLY
def test_adv2_readonly_precreated_diagnostic_is_replaced_never_trusted(tmp_path):
    proc, out, diag = _run_diag(
        tmp_path, _msgs(is_error=True), pre_diag=FORGED_SALVAGEABLE, pre_diag_readonly=True
    )
    assert out == {"envelope_salvage": "false"}
    assert "99" not in diag.read_text(encoding="utf-8")  # stale bytes did not survive
    assert proc.returncode == 0  # atomic replace works even over a read-only file


@_POSIX_ONLY
def test_adv3_forged_nonsalvageable_file_cannot_veto_current_genuine_success(tmp_path):
    _, out, _ = _run_diag(tmp_path, _msgs(), pre_diag=FORGED_NONSALVAGEABLE)
    assert out["envelope_salvage"] == "true" and out["envelope_num_turns"] == "25"


@_POSIX_ONLY
def test_adv4_unwritable_output_path_fails_closed_for_salvage(tmp_path):
    proc, out, _ = _run_diag(tmp_path, _msgs(), pre_diag_as_dir=True)  # os.replace over a dir fails
    assert proc.returncode != 0
    assert out == {}  # not even a "false": the step fails, so its outcome is not 'success'
    assert "FAILED to publish" in proc.stdout


@_POSIX_ONLY
def test_adv5_no_stale_file_or_temp_litter_after_publish_failure(tmp_path):
    _, _, diag = _run_diag(tmp_path, _msgs(), pre_diag_as_dir=True)
    assert [p.name for p in diag.parent.iterdir() if p.name.endswith(".tmp")] == []


@_POSIX_ONLY
def test_adv6_readonly_execution_file_is_not_fresh_and_gets_no_authority(tmp_path):
    proc, out, diag = _run_diag(tmp_path, _msgs(), src_readonly=True)
    assert proc.returncode == 0
    assert out == {"envelope_salvage": "false"}
    assert json.loads(diag.read_text(encoding="utf-8"))["parse_error"] == "source_not_fresh"


@_POSIX_ONLY
def test_adv7_symlinked_execution_file_gets_no_authority(tmp_path):
    tmp = tmp_path / "rt"
    tmp.mkdir()
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps(_msgs()), encoding="utf-8")
    (tmp / "claude-execution-output.json").symlink_to(real)
    out_file = tmp_path / "gho"
    out_file.write_text("")
    env = {
        "DIAG_TEMP": str(tmp), "AGENT_OUTCOME": "failure", "PATH": "", "MAX_TURNS": "20",
        "GITHUB_OUTPUT": str(out_file),
    }  # fmt: skip
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-"],
        input=_diag_script(), capture_output=True, text=True, env=env, timeout=60,
    )  # fmt: skip
    assert proc.returncode == 0
    assert out_file.read_text() == "envelope_salvage=false\n"


@_POSIX_ONLY
def test_missing_github_output_means_no_outputs_and_no_crash(tmp_path):
    proc, out, _ = _run_diag(tmp_path, _msgs(), gh_output=False)
    assert proc.returncode == 0 and out == {}


@_POSIX_ONLY
def test_outputs_contain_only_booleans_and_bounded_decimals_and_no_free_text(tmp_path):
    secret = "SECRET_FREE_TEXT sk-ant-SENTINEL_TOKEN"
    msgs = _msgs(result=secret, errors=[secret], permission_denials=[{"tool": secret}] * 3)
    msgs.append({"type": "assistant", "message": secret, "error": secret})
    proc, out, diag = _run_diag(tmp_path, msgs)
    assert out["envelope_salvage"] in {"true", "false"}
    for k, v in out.items():
        assert k in {"envelope_salvage", "envelope_num_turns", "envelope_permission_denials"}
        assert v in {"true", "false"} or (v.isdigit() and int(v) <= 100000), (k, v)
    for blob in (proc.stdout, proc.stderr, diag.read_text(encoding="utf-8")):
        assert "SECRET_FREE_TEXT" not in blob and "SENTINEL_TOKEN" not in blob


@_POSIX_ONLY
def test_hijacked_path_and_python_env_cannot_forge_outputs(tmp_path):
    # the production step resolves the interpreter by fixed path with -I -S: re-assert the contract
    run = _step(DIAG)["run"]
    assert "/usr/bin/python3" in run and '"$PY" -I -S -' in run


# -- persist step ----------
def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _run_persist(
    tmp_path: Path,
    files: dict[str, str],
    *,
    break_remote: bool = False,
    turns: str = "25",
    denials: str = "9",
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    evdir = tmp_path / "rt"
    evdir.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    (work / "tracked.txt").write_text("base\n")
    _git(work, "add", "tracked.txt")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    for rel, content in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(content)
    url = "https://x-access-token:SENTINEL_TOKEN@github.com/o/r.git"
    summary = tmp_path / "summary.md"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "GH_TOKEN_PUSH": "SENTINEL_TOKEN",
        "GH_REPOSITORY": "o/r",
        "RUN_ID": "7",
        "RUN_ATTEMPT": "1",
        "AGENT_BRANCH": "atlas/agent-7-1",
        "NUM_TURNS": turns,
        "DENIALS": denials,
        "MAX_TURNS": "20",
        "EVIDENCE_DIR": str(evdir),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    if not break_remote:
        env |= {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
            "GIT_CONFIG_VALUE_0": url,
        }
    _git(work, "remote", "add", "origin", "https://example.invalid/placeholder.git")
    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step(PERSIST)["run"]],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc, work, bare, evdir


@_POSIX_ONLY
def test_e1_changes_are_persisted_as_candidate_only(tmp_path):
    proc, work, bare, ev = _run_persist(tmp_path, {"new.py": "x = 1\n", "tracked.txt": "changed\n"})
    assert proc.returncode == 0, proc.stderr
    assert _git(bare, "show", "atlas/agent-7-1:new.py") == "x = 1"
    assert _git(bare, "show", "atlas/agent-7-1:tracked.txt") == "changed"
    rec = json.loads((ev / "atlas-envelope-salvage.json").read_text(encoding="utf-8"))
    assert rec["classification"] == "ENVELOPE_REJECTED_MODEL_SUCCESS"
    assert rec["execution_policy"] == "TURN_BUDGET_EXCEEDED"
    assert rec["result_persisted"] is True and rec["persisted_branch"] == "atlas/agent-7-1"
    assert rec["candidate_head"] == _git(bare, "rev-parse", "atlas/agent-7-1")
    assert rec["candidate_tree"] == _git(bare, "rev-parse", "atlas/agent-7-1^{tree}")
    assert (rec["num_turns"], rec["max_turns"], rec["permission_denials_count"]) == (25, 20, 9)
    assert rec["acceptance"] == "NOT_RUN" and rec["verification"] == "NOT_VERIFIED"
    assert rec["integration"] == "NOT_AUTHORIZED"
    assert set(rec) == {
        "schema", "classification", "execution_policy", "run_id", "run_attempt", "num_turns",
        "max_turns", "permission_denials_count", "result_persisted", "persisted_branch",
        "candidate_head", "candidate_tree", "acceptance", "verification", "integration",
    }  # fmt: skip
    msg = _git(bare, "log", "-1", "--format=%s", "atlas/agent-7-1")
    assert "candidate only" in msg and "not verified" in msg
    assert "SENTINEL_TOKEN" not in proc.stdout + proc.stderr
    assert "SENTINEL_TOKEN" not in json.dumps(rec)
    assert "SENTINEL_TOKEN" not in (work / ".git" / "config").read_text()
    assert "SENTINEL_TOKEN" not in _git(work, "remote", "get-url", "origin")


@_POSIX_ONLY
def test_e1_end_to_end_trusted_outputs_drive_persist_and_job_stays_red(tmp_path):
    (tmp_path / "d").mkdir()
    _, out, _ = _run_diag(tmp_path / "d", _msgs())
    assert out["envelope_salvage"] == "true"
    proc, _work, bare, ev = _run_persist(
        tmp_path / "p",
        {"new.py": "x = 1\n"},
        turns=out["envelope_num_turns"],
        denials=out["envelope_permission_denials"],
    )
    assert proc.returncode == 0, proc.stderr
    rec = json.loads((ev / "atlas-envelope-salvage.json").read_text(encoding="utf-8"))
    assert (rec["num_turns"], rec["permission_denials_count"]) == (25, 9)
    assert (
        rec["execution_policy"] == "TURN_BUDGET_EXCEEDED" and rec["integration"] == "NOT_AUTHORIZED"
    )
    assert "continue-on-error" not in _step(AGENT)  # the agent failure is never masked
    assert _git(bare, "show", "atlas/agent-7-1:new.py") == "x = 1"


@_POSIX_ONLY
def test_no_changes_creates_no_commit_or_branch(tmp_path):
    proc, work, bare, ev = _run_persist(tmp_path, {})
    assert proc.returncode == 0, proc.stderr
    assert "no result commit created" in proc.stdout
    assert (
        subprocess.run(["git", "for-each-ref"], cwd=bare, capture_output=True, text=True).stdout
        == ""
    )
    rec = json.loads((ev / "atlas-envelope-salvage.json").read_text(encoding="utf-8"))
    assert rec["result_persisted"] is False and rec["persisted_branch"] is None
    assert _git(work, "rev-list", "--count", "HEAD") == "1"


@_POSIX_ONLY
def test_token_does_not_linger_when_push_fails(tmp_path):
    proc, work, _bare, ev = _run_persist(tmp_path, {"a.txt": "a\n"}, break_remote=True)
    assert proc.returncode != 0
    assert "SENTINEL_TOKEN" not in _git(work, "remote", "get-url", "origin")
    assert "SENTINEL_TOKEN" not in (work / ".git" / "config").read_text()
    assert not (ev / "atlas-envelope-salvage.json").exists()  # no claim of persistence


@_POSIX_ONLY
def test_branch_mismatch_and_bad_counters_fail_closed(tmp_path):
    run = _step(PERSIST)["run"]
    base = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GH_REPOSITORY": "o/r",
        "RUN_ID": "7",
        "RUN_ATTEMPT": "1",
        "AGENT_BRANCH": "atlas/agent-7-1",
        "NUM_TURNS": "25",
        "DENIALS": "9",
        "MAX_TURNS": "20",
        "EVIDENCE_DIR": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "s"),
        "GH_TOKEN_PUSH": "SENTINEL_TOKEN",
    }
    w = tmp_path / "w"
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "tracked.txt").write_text("base\n")
    _git(w, "add", "tracked.txt")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    (w / "dirty.txt").write_text("dirty\n")  # dirty tree: only validation can stop the step
    cases = (
        {"AGENT_BRANCH": "main"},
        {"NUM_TURNS": "25;rm"},
        {"NUM_TURNS": ""},
        {"DENIALS": ""},
        {"MAX_TURNS": ""},
    )
    for over in cases:
        proc = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", run],
            cwd=w,
            capture_output=True,
            text=True,
            env=base | over,
            timeout=30,
        )
        assert proc.returncode == 2, (over, proc.stderr)
        assert not (tmp_path / "atlas-envelope-salvage.json").exists()
        assert _git(w, "rev-list", "--count", "HEAD") == "1"  # nothing committed
