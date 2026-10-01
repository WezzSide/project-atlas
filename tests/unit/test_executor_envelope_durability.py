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
CLASSIFY = "Classify envelope-rejected model success (structured fields only)"
PERSIST = "Persist envelope-rejected result on the dedicated branch (candidate only)"
UPLOAD_SALVAGE = "Upload envelope salvage evidence"
INFRA = "Infra runner suite (corroboration only, non-gating, outcome recorded)"

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="executes the Linux worker bash run blocks"
)


def _step(name: str) -> dict:
    return STEPS[NAMES.index(name)]


def _classify_script() -> str:
    run = _step(CLASSIFY)["run"]
    m = re.search(r"<<'ATLAS_ENVELOPE_CLASSIFY'\n(.*?)\n\s*ATLAS_ENVELOPE_CLASSIFY\n?", run, re.S)
    assert m, "classifier heredoc missing"
    return m.group(1)


# -- workflow contract ----------
def test_step_order_and_placement():
    i = NAMES.index(PUSH)
    assert NAMES[i + 1 : i + 4] == [CLASSIFY, PERSIST, UPLOAD_SALVAGE]
    assert NAMES.index(UPLOAD_SALVAGE) < NAMES.index(INFRA)


def test_agent_step_is_not_masked_and_normal_push_is_unchanged():
    assert "continue-on-error" not in _step(AGENT)
    push = _step(PUSH)
    assert "if" not in push and "continue-on-error" not in push


def test_conditions_require_failure_and_failed_agent():
    for name in (CLASSIFY, PERSIST, UPLOAD_SALVAGE):
        cond = _step(name)["if"]
        assert "failure()" in cond and "steps.agent.outcome == 'failure'" in cond, name
        assert _step(name)["continue-on-error"] is True
    assert "steps.sdk_diag.outcome == 'success'" in _step(CLASSIFY)["if"]
    assert "salvage == 'true'" in _step(PERSIST)["if"]
    assert "salvage_persist.outcome == 'success'" in _step(UPLOAD_SALVAGE)["if"]


def test_max_turns_literal_matches_agent_cap_in_both_steps():
    cap = re.search(r"--max-turns (\d+)", _step(AGENT)["with"]["claude_args"])
    assert cap
    for name in (CLASSIFY, PERSIST):
        assert _step(name)["env"]["MAX_TURNS"] == cap.group(1), name


def test_only_persist_step_gets_the_token_and_classifier_has_none():
    assert "secrets." not in yaml.safe_dump(_step(CLASSIFY))
    assert "secrets.GITHUB_TOKEN" in yaml.safe_dump(_step(PERSIST))
    assert "secrets." not in yaml.safe_dump(_step(UPLOAD_SALVAGE))


def test_classifier_does_not_read_free_text_or_transcripts():
    script = _classify_script()
    assert "claude-sdk-diagnostic.json" in script
    for banned in ("execution", "stderr", "error_message", "subprocess", "urllib", "socket"):
        assert banned not in script, banned
    assert "print(" in script and script.count("print(") == 1


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


# -- classifier behaviour ----------
def _diag(**over):
    d = {
        "schema": 1,
        "agent_step_outcome": "failure",
        "diagnostic_truncated": False,
        "result": {
            "subtype": "success",
            "is_error": False,
            "terminal_reason": "completed",
            "stop_reason": "end_turn",
            "errors_count": 0,
            "num_turns": 25,
            "permission_denials_count": 9,
        },
    }
    res = dict(d["result"])
    for k, v in over.items():
        if k.startswith("r_"):
            if v is _DEL:
                res.pop(k[2:], None)
            else:
                res[k[2:]] = v
        elif v is _DEL:
            d.pop(k, None)
        else:
            d[k] = v
    d["result"] = res
    return d


_DEL = object()


def _classify(tmp_path: Path, diag, *, raw: str | None = None, max_turns: str = "20"):
    tmp = tmp_path / "rt"
    tmp.mkdir(exist_ok=True)
    if raw is not None:
        (tmp / "claude-sdk-diagnostic.json").write_text(raw, encoding="utf-8")
    elif diag is not None:
        (tmp / "claude-sdk-diagnostic.json").write_text(json.dumps(diag), encoding="utf-8")
    out = tmp_path / "gh_output"
    out.write_text("")
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-"],
        input=_classify_script(),
        capture_output=True,
        text=True,
        env={"DIAG_TEMP": str(tmp), "GITHUB_OUTPUT": str(out), "MAX_TURNS": max_turns},
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return out.read_text(encoding="utf-8"), proc.stdout


@_POSIX_ONLY
def test_e1_failure_mode_is_classified_as_salvageable(tmp_path):
    out, stdout = _classify(tmp_path, _diag())
    assert "salvage=true" in out and "num_turns=25" in out and "permission_denials=9" in out
    assert "CLASSIFIED:ENVELOPE_REJECTED_MODEL_SUCCESS" in stdout


@_POSIX_ONLY
@pytest.mark.parametrize(
    "over,reason",
    [
        ({"r_is_error": True}, "is_error_not_false"),
        ({"r_is_error": _DEL}, "is_error_not_false"),
        ({"r_subtype": "error_max_turns"}, "subtype_not_success"),
        ({"r_terminal_reason": "max_turns"}, "terminal_reason_not_completed"),
        ({"r_stop_reason": "max_tokens"}, "stop_reason_not_end_turn"),
        ({"r_errors_count": 1}, "errors_present"),
        ({"r_errors_count": False}, "errors_present"),
        ({"r_num_turns": 20}, "within_turn_budget"),
        ({"r_num_turns": 3}, "within_turn_budget"),
        ({"r_num_turns": True}, "turns_not_numeric"),
        ({"r_num_turns": "25"}, "turns_not_numeric"),
        ({"agent_step_outcome": "success"}, "agent_outcome_not_failure"),
        ({"diagnostic_truncated": True}, "diagnostic_truncated"),
        ({"diagnostic_truncated": _DEL}, "diagnostic_truncated"),
        ({"parse_error": "x"}, "parse_error_present"),
        ({"schema": 2}, "bad_schema"),
        ({"result": None}, "no_result"),
    ],
)
def test_negative_controls_do_not_salvage(tmp_path, over, reason):
    d = _diag(**{k: v for k, v in over.items() if k != "result"})
    if "result" in over:
        d["result"] = over["result"]
    out, stdout = _classify(tmp_path, d)
    assert "salvage=false" in out
    assert f"NOT_SALVAGED:{reason}" in stdout


@_POSIX_ONLY
@pytest.mark.parametrize("raw", ["", "not json", "[]", "{", '{"schema":1}'])
def test_missing_or_malformed_diagnostic_never_salvages(tmp_path, raw):
    out, _ = _classify(tmp_path, None, raw=raw)
    assert "salvage=false" in out


@_POSIX_ONLY
def test_absent_diagnostic_and_bad_budget_fail_closed(tmp_path):
    out, stdout = _classify(tmp_path, None)
    assert "salvage=false" in out and "no_diagnostic" in stdout
    out, stdout = _classify(tmp_path, _diag(), max_turns="abc")
    assert "salvage=false" in out and "bad_budget" in stdout


@_POSIX_ONLY
def test_oversized_diagnostic_is_rejected(tmp_path):
    out, _ = _classify(tmp_path, None, raw=json.dumps(_diag()) + " " * 20000)
    assert "salvage=false" in out


@_POSIX_ONLY
def test_free_text_in_diagnostic_is_never_echoed(tmp_path):
    d = _diag()
    d["error_text"] = "SECRET_FREE_TEXT sk-ant-xyz"
    d["result"]["message"] = "SECRET_FREE_TEXT"
    out, stdout = _classify(tmp_path, d)
    assert "SECRET_FREE_TEXT" not in out + stdout


# -- persist step ----------
def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _run_persist(tmp_path: Path, files: dict[str, str], *, break_remote: bool = False):
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
        "NUM_TURNS": "25",
        "DENIALS": "9",
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
