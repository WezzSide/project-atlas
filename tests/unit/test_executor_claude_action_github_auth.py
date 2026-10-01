"""CLAUDE_ACTION_GITHUB_AUTH: the pinned claude-code-action must receive the job token explicitly.

Regression for live run 36837188650 (E2): with ``github_token`` unset the pinned action's
``setupGitHubToken()`` falls back to a GitHub OIDC -> App-token exchange, which needs
``id-token: write`` (deliberately not granted) and failed with ``Unable to get
ACTIONS_ID_TOKEN_REQUEST_URL`` before the model started. Passing the job's own ``GITHUB_TOKEN``
selects the action's provided-token path (``OVERRIDE_GITHUB_TOKEN``) and requests no OIDC token.
The permission envelope, secret-backed Anthropic auth, branch model and tool/path constraints
must stay exactly as they were.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / ".github/workflows/atlas-agent-execute.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(TEXT)
STEPS = WORKFLOW["jobs"]["execute"]["steps"]
PINNED_ACTION = "anthropics/claude-code-action@756cc22e19660d20e8cc9496b4f242475a7f7790"


def _agent_step() -> dict:
    steps = [
        s for s in STEPS if str(s.get("uses", "")).startswith("anthropics/claude-code-action@")
    ]
    assert len(steps) == 1
    return steps[0]


def test_action_pin_is_unchanged():
    assert _agent_step()["uses"].split(" ")[0] == PINNED_ACTION


def test_job_token_is_passed_explicitly_and_anthropic_auth_stays_secret_backed():
    with_ = _agent_step()["with"]
    assert with_["github_token"] == "${{ secrets.GITHUB_TOKEN }}"
    assert with_["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"


def test_no_other_credential_reaches_the_action():
    with_ = _agent_step()["with"]
    secret_inputs = {k: v for k, v in with_.items() if "secrets." in str(v) or "token" in k.lower()}
    assert set(secret_inputs) == {"github_token", "anthropic_api_key"}
    assert "env" not in _agent_step() or "secrets." not in str(_agent_step()["env"])


def test_permission_envelope_is_not_widened_and_no_oidc_path_is_enabled():
    assert WORKFLOW["permissions"] == {"contents": "write"}
    assert "jobs" in WORKFLOW and "permissions" not in WORKFLOW["jobs"]["execute"]
    code = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    assert "id-token" not in code
    assert "anthropic_federation_rule_id" not in code
    assert "anthropic_organization_id" not in code


def test_job_token_is_only_used_by_the_action_and_the_dedicated_branch_push():
    users = [
        s.get("name", s.get("uses")) for s in STEPS if "secrets.GITHUB_TOKEN" in yaml.safe_dump(s)
    ]
    assert users == [
        "Run agent (bounded prompt, restricted tools)",
        "Commit and push dedicated branch",
    ]


def test_dedicated_branch_and_tool_constraints_are_unchanged():
    names = [s.get("name") for s in STEPS]
    assert names.index("Create dedicated agent branch") < names.index(
        "Run agent (bounded prompt, restricted tools)"
    )
    assert 'branch="atlas/agent-${RUN_ID}-${RUN_ATTEMPT}"' in TEXT
    assert 'git push -u origin "HEAD:${AGENT_BRANCH}"' in TEXT
    args = _agent_step()["with"]["claude_args"]
    assert "--max-turns 20" in args
    tools = "Edit Read Write Glob Grep Bash(pytest:*) Bash(ruff:*) Bash(bash:*)"
    assert f"--allowedTools {tools}" in args
    assert _agent_step()["with"]["base_branch"] == "${{ env.AGENT_BRANCH }}"
