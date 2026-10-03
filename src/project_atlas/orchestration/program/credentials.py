"""What credential a run will actually use, reported without printing any.

`DECLARED MECHANISM != EFFECTIVE SELECTION`. A profile says which mechanism it
intends. What the runtime does when it starts is a different question, and the
answer depends on the operator's environment. Reporting the intention alone is
how a run ends up billing an account nobody meant to use.

Nothing here reads, returns, logs or persists a credential VALUE. It reports
variable NAMES, whether they are set, and what the runtime's own precedence
rules mean for the combination -- which is everything an operator needs and
nothing an evidence file should ever contain.

The concrete hazard this exists for was observed, not imagined. Claude Code
prefers `ANTHROPIC_API_KEY` over a logged-in subscription when both are
present. During this package's development an inherited key in the operator's
shell sent every probe to a different account, which was out of credit, and the
runtime returned HTTP 400 `Credit balance is too low` in a payload that also
said `"subtype": "success"`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

from project_atlas.orchestration.program.adapters.base import BASE_ENV_NAMES
from project_atlas.orchestration.program.loader import LoadedProgram
from project_atlas.orchestration.program.profiles import (
    AdapterKind,
    AgentProfile,
    CredentialMechanism,
)

#: Variable names that carry, or redirect, an agent runtime's credentials.
#: Reported by NAME and presence only. Never read for their value.
_CREDENTIAL_NAMES: Final[dict[AdapterKind, tuple[str, ...]]] = {
    AdapterKind.CLAUDE_CODE: (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    ),
    AdapterKind.CODEX: (
        "OPENAI_API_KEY",
        "CODEX_HOME",
        "CODEX_API_KEY",
    ),
    AdapterKind.LOCAL_COMMAND: (),
}

#: The runtime's own precedence, stated so an operator can predict the outcome.
_PRECEDENCE: Final[dict[AdapterKind, str]] = {
    AdapterKind.CLAUDE_CODE: (
        "ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) takes precedence over a "
        "logged-in claude.ai subscription. Under --bare, OAuth and the keychain "
        "are never read at all and an API key or apiKeyHelper is required"
    ),
    AdapterKind.CODEX: (
        "codex uses its own stored login under CODEX_HOME (default ~/.codex). "
        "This package does not forward an Anthropic key to it and refuses a "
        "profile that declares one"
    ),
    AdapterKind.LOCAL_COMMAND: "no credential of any kind; it runs a fixed argv",
}


def effective_selection(
    profile: AgentProfile, *, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """What this profile will actually authenticate as, and why.

    Reports names and presence. Never a value, never a prefix of a value,
    never a length -- a length is a fact about a secret.
    """
    env = os.environ if environment is None else environment
    names = _CREDENTIAL_NAMES.get(profile.adapter, ())
    present = tuple(name for name in names if env.get(name))
    forwarded = tuple(name for name in names if name in profile.env_allowlist)
    # Present in the operator's shell but NOT forwarded: this is the good case
    # for SUBSCRIPTION_OAUTH and the surprising one for everybody else, so it
    # is named rather than left to inference.
    withheld = tuple(name for name in present if name not in forwarded)

    warnings: list[str] = []
    if profile.credential is CredentialMechanism.SUBSCRIPTION_OAUTH:
        effective = "the operator's logged-in session for this runtime"
        if forwarded:
            warnings.append(
                f"profile declares SUBSCRIPTION_OAUTH but forwards {', '.join(forwarded)}; "
                "the runtime would prefer the key and the declared mechanism "
                "would be a fiction. This combination is refused at launch"
            )
        if profile.isolated_runtime and profile.adapter is AdapterKind.CLAUDE_CODE:
            warnings.append(
                "isolated_runtime passes --bare, under which OAuth and the "
                "keychain are never read; SUBSCRIPTION_OAUTH cannot work"
            )
    elif profile.credential is CredentialMechanism.ANTHROPIC_API_KEY_ENV:
        effective = "ANTHROPIC_API_KEY from the supervisor's environment"
        if "ANTHROPIC_API_KEY" not in profile.env_allowlist:
            warnings.append(
                "profile declares ANTHROPIC_API_KEY_ENV but does not "
                "allow-list ANTHROPIC_API_KEY, so nothing is forwarded and the "
                "runtime will fall back to whatever else it finds"
            )
        elif not env.get("ANTHROPIC_API_KEY"):
            warnings.append(
                "ANTHROPIC_API_KEY is allow-listed but not set in this "
                "environment; the first dispatch will fail QUOTA_OR_CREDENTIAL"
            )
    elif profile.credential is CredentialMechanism.API_KEY_HELPER:
        effective = "an apiKeyHelper supplied through the runtime's own settings"
    else:
        effective = "none"

    return {
        "profile_id": profile.profile_id,
        "agent_id": profile.agent_id,
        "adapter": profile.adapter.value,
        "declared_mechanism": profile.credential.value,
        "effective_selection": effective,
        "runtime_precedence": _PRECEDENCE.get(profile.adapter, "unknown"),
        "credential_names_present_in_environment": list(present),
        "credential_names_forwarded_to_the_worker": list(forwarded),
        "credential_names_present_but_withheld": list(withheld),
        "always_forwarded": list(BASE_ENV_NAMES),
        "warnings": warnings,
        "values_read": False,
        "values_note": (
            "no credential value is read, returned, logged or persisted by this "
            "report -- names and presence only"
        ),
    }


def credential_report(
    loaded: LoadedProgram, *, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The whole program's credential picture, one row per distinct profile."""
    seen: dict[str, dict[str, Any]] = {}
    for task in loaded.program.tasks:
        profile = loaded.effective_profile(task.task_id)
        seen.setdefault(
            profile.profile_id, effective_selection(profile, environment=environment)
        )
    for profile in loaded.verifiers.values():
        seen.setdefault(
            profile.profile_id, effective_selection(profile, environment=environment)
        )
    rows = [seen[key] for key in sorted(seen)]
    return {
        "program_id": loaded.program.program_id,
        "profiles": rows,
        "warnings": [
            {"profile_id": row["profile_id"], "warning": warning}
            for row in rows
            for warning in row["warnings"]
        ],
        "policy": [
            "the child environment is BUILT from the profile's allow-list plus "
            f"{', '.join(BASE_ENV_NAMES)}; it is never inherited wholesale",
            "no account is switched automatically, ever",
            "an intentionally supplied credential is never discarded: a "
            "profile that allow-lists one gets it, and a profile whose "
            "declared mechanism contradicts its allow-list is refused rather "
            "than silently resolved",
            "no spending limit is raised by this package; max_estimated_cost_usd "
            "is forwarded to a runtime that has such a flag and is compared "
            "against that runtime's own client-side estimate",
        ],
        "merge_authorized": False,
    }
