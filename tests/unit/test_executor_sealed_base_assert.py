"""SEALED_BASE_ASSERT: the execute workflow itself asserts the checked-out revision.

``dev_package.verify_checkout_ref`` is a pure pre-dispatch check; ``base_branch`` is a movable
name. Between that check and ``actions/checkout`` the branch can be pushed (or shadowed by a
same-named tag), so the workflow takes an OPTIONAL ``base_revision`` input and, when it is
non-empty, fails closed unless ``git rev-parse HEAD`` equals it -- before the toolchain steps,
before the dedicated agent branch exists and before the agent runs. An empty input keeps the
previous behaviour exactly.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / ".github/workflows/atlas-agent-execute.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(TEXT)
# PyYAML (YAML 1.1) parses the bare key ``on`` as boolean True.
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))
INPUTS = TRIGGERS["workflow_dispatch"]["inputs"]
JOB = WORKFLOW["jobs"]["execute"]
STEPS = JOB["steps"]
NAMES = [s.get("name", s.get("uses")) for s in STEPS]
ASSERT = "Assert checked-out HEAD is the sealed base_revision (fail closed)"
VALIDATE_BRANCH = "Validate base_branch shape (fail closed on injection)"
TOOLCHAIN = "Verify executor Python toolchain (fail closed)"
VENV = "Create isolated venv and install package with dev dependencies"
BRANCH = "Create dedicated agent branch"
AGENT = "Run agent (bounded prompt, restricted tools)"

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="executes the Linux worker bash run blocks"
)


def _step(name: str) -> dict:
    return STEPS[NAMES.index(name)]


def _checkout_index() -> int:
    hits = [
        i for i, s in enumerate(STEPS) if str(s.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(hits) == 1
    return hits[0]


# -- workflow contract ----------------------------------------------------------------------------
def test_base_revision_input_is_optional_with_an_empty_default():
    assert list(INPUTS) == ["task_prompt", "base_branch", "base_revision", "agent_type"]
    spec = INPUTS["base_revision"]
    assert spec["required"] is False
    assert spec["type"] == "string"
    assert spec["default"] == ""


def test_existing_inputs_are_unchanged():
    assert INPUTS["task_prompt"] == {
        "description": "Bounded task for the agent (scope-limited prompt).",
        "required": True,
        "type": "string",
    }
    assert INPUTS["base_branch"] == {
        "description": "Base branch to start the dedicated agent branch from.",
        "required": True,
        "type": "string",
        "default": "main",
    }
    assert INPUTS["agent_type"] == {
        "description": "Agent backend.",
        "required": True,
        "type": "choice",
        "options": ["claude"],
        "default": "claude",
    }


def test_assertion_sits_immediately_after_checkout_and_before_everything_else():
    checkout = _checkout_index()
    assert NAMES.index(ASSERT) == checkout + 1
    assert NAMES.index(VALIDATE_BRANCH) < checkout
    order = [NAMES.index(n) for n in (ASSERT, TOOLCHAIN, VENV, BRANCH, AGENT)]
    assert order == sorted(order) and len(set(order)) == len(order)
    # nothing that runs repository code, creates the branch or starts the agent precedes it
    before = STEPS[: NAMES.index(ASSERT)]
    assert [s.get("name", s.get("uses")) for s in before] == [VALIDATE_BRANCH, NAMES[checkout]]
    assert NAMES.count(ASSERT) == 1


def test_assertion_is_unconditional_and_cannot_be_masked():
    step = _step(ASSERT)
    assert set(step) == {"name", "env", "run"}  # no if / continue-on-error / shell / uses
    assert "|| true" not in step["run"] and "set +e" not in step["run"]
    assert step["run"].startswith("set -euo pipefail\n")


def test_input_reaches_the_script_only_through_env():
    step = _step(ASSERT)
    assert step["env"] == {"BASE_REVISION": "${{ inputs.base_revision }}"}
    assert "${{" not in step["run"]
    # the input expression appears exactly once in the whole workflow: in that env mapping
    assert TEXT.count("inputs.base_revision") == 1
    assert "secrets." not in yaml.safe_dump(step)
    assert "GITHUB_ENV" not in step["run"] and "GITHUB_OUTPUT" not in step["run"]


def test_script_validates_the_forty_hex_shape_and_compares_against_head():
    run = _step(ASSERT)["run"]
    assert "export LC_ALL=C" in run  # bracket ranges must not be locale-collated
    assert '(*[!0-9a-f]*) echo "invalid base_revision" >&2; exit 2 ;;' in run
    assert '[ "${#BASE_REVISION}" -eq 40 ]' in run
    assert 'head="$(git rev-parse HEAD)"' in run
    assert '[ "${head}" != "${BASE_REVISION}" ]' in run
    assert "exit 4" in run
    # shape validation precedes the comparison
    assert run.index("[!0-9a-f]") < run.index("-eq 40") < run.index("git rev-parse HEAD")


def test_empty_input_skips_the_assertion_before_touching_git():
    run = _step(ASSERT)["run"]
    skip = 'if [ -z "${BASE_REVISION}" ]; then'
    assert skip in run
    block = run.split(skip, 1)[1].split("fi", 1)[0]
    assert "exit 0" in block
    assert run.index(skip) < run.index("git rev-parse HEAD")
    assert run.count("exit 0") == 1  # the only success shortcut is the empty input


def test_checkout_and_permission_envelope_are_untouched():
    checkout = STEPS[_checkout_index()]
    assert checkout == {
        "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "with": {"ref": "${{ inputs.base_branch }}", "fetch-depth": 0},
    }
    assert WORKFLOW["permissions"] == {"contents": "write"}
    assert list(WORKFLOW["jobs"]) == ["execute"]
    assert "permissions" not in JOB
    assert len(STEPS) == 15  # the 14 pre-existing steps plus the assertion
    assert re.search(r"--max-turns 20\b", _step(AGENT)["with"]["claude_args"])
    assert len(re.findall(r"secrets\.[A-Z_]+", TEXT)) == 4
    assert set(re.findall(r"secrets\.[A-Z_]+", TEXT)) == {
        "secrets.ANTHROPIC_API_KEY",
        "secrets.GITHUB_TOKEN",
    }


def test_builder_input_name_matches_the_workflow_input():
    from project_atlas.orchestration.autonomy.dev_package import BASE_REVISION_INPUT

    assert BASE_REVISION_INPUT in INPUTS


# -- the step's script, executed against a real temporary repository ------------------------------
def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    _git(work, "commit", "-q", "--allow-empty", "-m", "sealed base")
    sealed = _git(work, "rev-parse", "HEAD")
    _git(work, "commit", "-q", "--allow-empty", "-m", "branch moved")
    moved = _git(work, "rev-parse", "HEAD")
    assert sealed != moved and re.fullmatch(r"[0-9a-f]{40}", sealed)
    return work, sealed, moved


def _run(work: Path, value: str | None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(work.parent)}
    if value is not None:
        env["BASE_REVISION"] = value
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step(ASSERT)["run"]],
        cwd=work,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


@_POSIX_ONLY
def test_matching_revision_passes(repo):
    work, _sealed, moved = repo
    proc = _run(work, moved)  # HEAD is ``moved``
    assert proc.returncode == 0, proc.stderr
    assert moved in proc.stdout


@_POSIX_ONLY
def test_moved_branch_fails_closed(repo):
    work, sealed, moved = repo
    proc = _run(work, sealed)  # sealed revision exists, but HEAD has moved past it
    assert proc.returncode == 4
    assert sealed in proc.stderr and moved in proc.stderr
    _git(work, "checkout", "-q", "--detach", sealed)
    assert _run(work, sealed).returncode == 0
    assert _run(work, moved).returncode == 4


@_POSIX_ONLY
def test_empty_input_is_a_no_op_even_outside_a_repository(repo, tmp_path):
    work, _sealed, _moved = repo
    proc = _run(work, "")
    assert proc.returncode == 0 and "not applicable" in proc.stdout
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert _run(outside, "").returncode == 0  # git is never consulted


@_POSIX_ONLY
def test_unset_variable_fails_closed(repo):
    # GitHub always sets the env key (possibly empty); a missing variable is not a skip.
    assert _run(repo[0], None).returncode != 0


@_POSIX_ONLY
@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h[:39],
        lambda h: h[:12],
        lambda h: h + "0",
        lambda h: h.upper(),
        lambda h: h[:-1] + "G",
        lambda h: " " + h,
        lambda h: h + " ",
        lambda h: h + "\n",
        lambda h: " ",
        lambda h: "HEAD",
        lambda h: "main",
        lambda h: "$(git rev-parse HEAD)",
        lambda h: "`git rev-parse HEAD`",
        lambda h: h + "; touch INJECTED",
        lambda h: h + " || true",
        lambda h: '"; touch INJECTED; echo "',
        lambda h: "${head}",
        lambda h: "*",
        lambda h: "-n",
        lambda h: h[:20] + "é" + h[21:],
    ],
    ids=[
        "39-chars",
        "short",
        "41-chars",
        "uppercase",
        "non-hex",
        "leading-space",
        "trailing-space",
        "trailing-newline",
        "blank",
        "symbolic-HEAD",
        "branch-name",
        "command-substitution",
        "backticks",
        "semicolon-injection",
        "or-true",
        "quote-breakout",
        "variable-reference",
        "glob",
        "dash-option",
        "non-ascii",
    ],
)
def test_malformed_or_injection_shaped_values_fail_closed(repo, mutate):
    work, _sealed, moved = repo
    proc = _run(work, mutate(moved))  # derived from the MATCHING revision: only shape is wrong
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "invalid base_revision" in proc.stderr
    assert not (work / "INJECTED").exists()
    assert _git(work, "rev-parse", "HEAD") == moved
    assert _git(work, "status", "--porcelain") == ""


@_POSIX_ONLY
def test_same_named_tag_cannot_satisfy_the_assertion(repo):
    """A ref name is never accepted as the revision; only the exact commit id is."""
    work, sealed, moved = repo
    _git(work, "tag", "atlas/agent-1-1", sealed)
    _git(work, "checkout", "-q", "--detach", "atlas/agent-1-1")  # what a shadowing tag yields
    assert _run(work, moved).returncode == 4
    assert _run(work, "atlas/agent-1-1").returncode == 2
