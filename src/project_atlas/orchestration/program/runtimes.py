"""Which runtimes are supported, on this machine, with which capabilities.

`SUPPORTED != INSTALLED != AUTHENTICATED`. Three different facts, reported
separately, because a program that validates against a runtime nobody is
logged into fails at dispatch and not before.

A runtime appears here only when a real adapter exists for it, written against
that runtime's own verified interface. There is deliberately no generic
subprocess adapter: one would let any program claim support for a runtime
nobody has checked, and "we can launch a process" is not the same statement as
"we understand this runtime's output, permissions, and resume contract".

Capabilities are read from the adapters themselves, so this report cannot
drift from what the adapters actually declare.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from project_atlas.orchestration.program.adapters.base import (
    AdapterCapabilities,
    AdapterUnavailableError,
    RuntimeAdapter,
)
from project_atlas.orchestration.program.adapters.claude_code import ClaudeCodeAdapter
from project_atlas.orchestration.program.adapters.codex import CodexAdapter
from project_atlas.orchestration.program.adapters.local_command import (
    FIXTURE_LABEL,
    LocalCommandAdapter,
)
from project_atlas.orchestration.program.profiles import AdapterKind


class SupportTier(StrEnum):
    """How far a runtime has actually been taken.

    The three tiers exist because "we wrote an adapter" and "we ran it against
    the real thing" are different claims, and a matrix that prints one word for
    both invites the reader to assume the stronger one.
    """

    #: An adapter exists AND has been exercised end to end against the real
    #: runtime, with the evidence named.
    RUNTIME_TESTED = "IMPLEMENTED_AND_RUNTIME_TESTED"
    #: An adapter exists and passes its tests, but no real-runtime run backs it.
    FIXTURE_ONLY = "IMPLEMENTED_FIXTURE_ONLY"
    #: Inventoried, deliberately not adapted. See `blocker` and `demand`.
    UNAVAILABLE = "INVENTORIED_UNAVAILABLE"


@dataclass(frozen=True)
class RuntimeSupport:
    """One runtime's honest support picture on this machine."""

    adapter: AdapterKind
    executable: str | None
    installed: bool
    version: str | None
    capabilities: AdapterCapabilities | None
    #: Capabilities this runtime does NOT have, named rather than omitted.
    unsupported: tuple[str, ...]
    #: What the runtime enforces itself, as opposed to what Atlas declares.
    runtime_enforced: tuple[str, ...]
    notes: tuple[str, ...]
    #: FIXTURE adapters are labelled so a report can never read as real support.
    is_fixture: bool = False
    tier: SupportTier = SupportTier.FIXTURE_ONLY
    #: The committed evidence for a RUNTIME_TESTED claim. Empty otherwise, and
    #: the tier is what makes the emptiness meaningful rather than an omission.
    runtime_evidence: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter.value,
            "support_tier": self.tier.value,
            "runtime_evidence": list(self.runtime_evidence),
            "executable": self.executable,
            "installed": self.installed,
            "version": self.version,
            "is_fixture": self.is_fixture,
            "capabilities": (
                {
                    "supports_resume": self.capabilities.supports_resume,
                    "supports_session_probe": self.capabilities.supports_session_probe,
                    "accepts_assigned_session": (
                        self.capabilities.accepts_assigned_session
                    ),
                    "supports_cost_limit": self.capabilities.supports_cost_limit,
                    "reports_cost": self.capabilities.reports_cost,
                    "supports_result_schema": self.capabilities.supports_result_schema,
                }
                if self.capabilities is not None
                else None
            ),
            "unsupported": list(self.unsupported),
            "runtime_enforced": list(self.runtime_enforced),
            "notes": list(self.notes),
        }


#: Capabilities NEITHER supported runtime has. Stated once, here, because the
#: temptation to assume one of them is what produces a supervisor that adopts
#: somebody's live terminal session.
UNIVERSALLY_UNSUPPORTED: Final[tuple[str, ...]] = (
    "attach to a running interactive session",
    "adopt a process this supervisor did not start",
    "guarantee a worker stays inside its declared mutation paths",
)


def _executable_for(adapter: AdapterKind) -> str | None:
    if adapter is AdapterKind.CLAUDE_CODE:
        return "claude"
    if adapter is AdapterKind.CODEX:
        return "codex"
    return None


def _adapter_for(adapter: AdapterKind) -> RuntimeAdapter:
    if adapter is AdapterKind.CLAUDE_CODE:
        return ClaudeCodeAdapter()
    if adapter is AdapterKind.CODEX:
        return CodexAdapter()
    return LocalCommandAdapter(("/bin/true",))


def describe(adapter: AdapterKind) -> RuntimeSupport:
    """Report one runtime, without launching a model call."""
    executable = _executable_for(adapter)
    installed = executable is None or shutil.which(executable) is not None
    impl = _adapter_for(adapter)
    capabilities = impl.capabilities if installed else None

    if adapter is AdapterKind.CLAUDE_CODE:
        return RuntimeSupport(
            adapter=adapter,
            executable=executable,
            installed=installed,
            version=capabilities.version if capabilities else None,
            capabilities=capabilities,
            tier=SupportTier.RUNTIME_TESTED,
            runtime_evidence=(
                "docs/orchestration/program/evidence/REAL-RUNTIME-DEMO.md",
                "docs/orchestration/program/evidence/SYSTEMWIDE-ACCEPTANCE.md",
            ),
            unsupported=(
                *UNIVERSALLY_UNSUPPORTED,
                "filesystem confinement without --restricted",
                "a cost figure that is an actual charge rather than an estimate",
            ),
            runtime_enforced=(
                "--permission-mode",
                "--allowedTools / --disallowedTools / --tools",
                "--permission-prompts none",
                "--restricted (file tools confined to the working directories)",
                "--max-budget-usd (against the runtime's own estimate)",
            ),
            notes=(
                "session identity can be assigned before launch (--session-id), "
                "so an interrupted attempt stays addressable",
                "resume by session id works from any directory from 2.1.223",
                "--permission-prompts requires 2.1.259; below that an "
                "unattended run would wait for an approval nobody can give",
                "prefers ANTHROPIC_API_KEY over a logged-in subscription when "
                "both are present",
            ),
        )

    if adapter is AdapterKind.CODEX:
        return RuntimeSupport(
            adapter=adapter,
            executable=executable,
            installed=installed,
            version=capabilities.version if capabilities else None,
            capabilities=capabilities,
            tier=SupportTier.RUNTIME_TESTED,
            runtime_evidence=(
                "docs/orchestration/program/evidence/REAL-RUNTIME-DEMO-CODEX.md",
                "docs/orchestration/program/evidence/SYSTEMWIDE-ACCEPTANCE.md",
            ),
            unsupported=(
                *UNIVERSALLY_UNSUPPORTED,
                "assigning a session id before launch",
                "reporting cost in any currency",
                "a per-launch spend cap",
                "a single structured result object (events are JSONL)",
            ),
            runtime_enforced=(
                "--sandbox read-only | workspace-write | danger-full-access "
                "(a real filesystem boundary, verified by observation)",
                "--add-dir (additional writable roots)",
                "--cd (working root)",
            ),
            notes=(
                "mints its own thread id and announces it in the first "
                "thread.started event; the adapter streams that event file to "
                "disk so the identity survives a supervisor crash",
                "resume is `codex exec resume <thread-id>`",
                "reports token usage only, so this package reports cost as "
                "unknown rather than estimating one",
                "exits 0 on a task it could not perform: observed refusing a "
                "write under --sandbox read-only with a completed turn",
            ),
        )

    return RuntimeSupport(
        adapter=adapter,
        executable=None,
        installed=True,
        version=FIXTURE_LABEL,
        capabilities=capabilities,
        unsupported=(*UNIVERSALLY_UNSUPPORTED, "anything a model does"),
        runtime_enforced=("nothing; it runs a fixed argv",),
        notes=(
            "FIXTURE. Proves supervisor behaviour deterministically and "
            "without a model call. FIXTURE_RUN != REAL_RUNTIME_COMPATIBILITY",
        ),
        is_fixture=True,
        tier=SupportTier.FIXTURE_ONLY,
    )


def describe_all() -> tuple[RuntimeSupport, ...]:
    return tuple(describe(kind) for kind in AdapterKind)


@dataclass(frozen=True)
class InventoriedRuntime:
    """A runtime that exists on this machine but has no adapter here.

    Recorded rather than omitted. An absent row reads as "nobody thought about
    it"; a row saying `implemented: false` with the reason reads as what it is.

    ``verified_capabilities`` are facts read from the installed CLI's own
    ``--help``, which is authoritative for the build that is installed.
    ``unverified`` names what could NOT be established -- output shapes,
    terminal-state semantics, error taxonomies -- because establishing those
    requires actually running the thing.
    """

    name: str
    executable: str
    installed: bool
    version: str | None
    implemented: bool
    verified_capabilities: tuple[str, ...]
    unverified: tuple[str, ...]
    blocker: str | None
    demand: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "executable": self.executable,
            "installed": self.installed,
            "version": self.version,
            "adapter_implemented": self.implemented,
            "verified_capabilities": list(self.verified_capabilities),
            "unverified": list(self.unverified),
            "support_tier": SupportTier.UNAVAILABLE.value,
            "blocker": self.blocker,
            "practical_demand": self.demand,
        }


def _probe_version(executable: str, *args: str) -> str | None:
    """Ask an installed CLI its version. Never runs a model."""
    found = shutil.which(executable)
    if found is None:
        return None
    try:
        completed = subprocess.run(
            [found, *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (completed.stdout or completed.stderr or "").strip()
    match = re.search(r"\d[\w.\-]*", text)
    return match.group(0) if match else (text.splitlines()[0][:64] if text else None)


#: Runtimes present on a developer machine that this package does NOT adapt.
#:
#: Each entry is flag-level fact from the installed CLI's own help, plus an
#: explicit statement of what was not established and why. A generic subprocess
#: wrapper could "support" all of these tomorrow and understand none of them;
#: that is precisely the claim this table exists to refuse.
_NOT_IMPLEMENTED: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "Cursor Agent",
        "executable": "cursor-agent",
        "version_args": ("--version",),
        "verified_capabilities": (
            "-p/--print for non-interactive use",
            "--output-format text|json|stream-json",
            "--resume [chatId] and --continue",
            "create-chat returns a chat id before any run",
            "--mode plan|ask for read-only operation",
            "--force/--yolo, --auto-review, --sandbox enabled|disabled",
            "--workspace, --add-dir, -w/--worktree",
            "--trust is REQUIRED for a non-interactive run in an untrusted "
            "directory; without it the run exits 1 and says so",
        ),
        "unverified": (
            "the shape of --output-format json",
            "how a terminal state is signalled",
            "the error taxonomy, and how a refusal differs from a failure",
            "whether --resume preserves the working directory",
        ),
        "blocker": (
            "the account is at its usage limit: 'You've hit your usage limit ... "
            "Your usage limits will reset when your monthly cycle ends'. Raising "
            "a spend limit or switching to another account to get round it is "
            "not authorized, and would not be the right thing to do anyway"
        ),
        "demand": (
            "high: Atlas's existing orchestration.sdk lane is Cursor-based "
            "(backend.py, cli_execution_port.py). Those ports are pinned to that "
            "lane's own canonical PR and repository, so they do not satisfy this "
            "package's adapter contract, but the demand for a Cursor adapter here "
            "is real"
        ),
    },
    {
        "name": "GitHub Copilot CLI",
        "executable": "copilot",
        "version_args": ("--version",),
        "verified_capabilities": (
            "-p/--prompt <text> for non-interactive use",
            "--output-format json emits JSONL, one object per line",
            "--session-id <id> BOTH resumes a session AND sets the UUID for a "
            "new one, so an identity can be assigned before launch",
            "-r/--resume[=id] and --continue",
            "--allow-all-tools is required for non-interactive mode",
            "--allow-tool / --deny-tool for per-tool permissions",
            "--add-dir, --allow-all-paths, --log-dir, --no-color",
            "--acp starts an Agent Client Protocol server",
        ),
        "unverified": (
            "the JSONL event vocabulary",
            "how a terminal state is signalled",
            "whether the prompt can be delivered off argv",
            "the error taxonomy",
        ),
        "blocker": (
            "authentication cannot be validated: the GitHub token is present but "
            "the account is rate limited (HTTP 403, 'API rate limit exceeded for "
            "user ID ...'), which also breaks `gh auth status`. Waiting for the "
            "limit to reset is the fix; working round it is not"
        ),
        "demand": (
            "moderate: Copilot is in use on this host, but nothing in Atlas "
            "orchestrates it today"
        ),
    },
    {
        "name": "Gemini CLI",
        "executable": "gemini",
        "version_args": ("--version",),
        "verified_capabilities": ("installed and on PATH",),
        "unverified": ("everything beyond its presence",),
        "blocker": None,
        "demand": (
            "none observed: no Atlas code, document or work package references "
            "it. Adapting it would be building for a user nobody has"
        ),
    },
    {
        "name": "Aider",
        "executable": "aider",
        "version_args": ("--version",),
        "verified_capabilities": ("installed and on PATH",),
        "unverified": ("everything beyond its presence",),
        "blocker": None,
        "demand": "none observed",
    },
    {
        "name": "Amp",
        "executable": "amp",
        "version_args": ("--version",),
        "verified_capabilities": ("installed and on PATH",),
        "unverified": ("everything beyond its presence",),
        "blocker": None,
        "demand": "none observed",
    },
)


def inventory_unimplemented() -> tuple[InventoriedRuntime, ...]:
    """Runtimes on this machine that this package deliberately does not adapt."""
    rows: list[InventoriedRuntime] = []
    for entry in _NOT_IMPLEMENTED:
        executable = str(entry["executable"])
        installed = shutil.which(executable) is not None
        rows.append(
            InventoriedRuntime(
                name=str(entry["name"]),
                executable=executable,
                installed=installed,
                version=(
                    _probe_version(executable, *entry["version_args"])
                    if installed
                    else None
                ),
                implemented=False,
                verified_capabilities=tuple(entry["verified_capabilities"]),
                unverified=tuple(entry["unverified"]),
                blocker=entry["blocker"],
                demand=str(entry["demand"]),
            )
        )
    return tuple(rows)


def capability_matrix() -> dict[str, Any]:
    """The three-tier matrix, in one object.

    IMPLEMENTED, RUNTIME-TESTED and UNAVAILABLE are kept apart deliberately.
    "We wrote an adapter" and "we ran it against the real thing" are different
    claims, and a matrix that prints one word for both invites the reader to
    assume the stronger one.
    """
    implemented = describe_all()
    return {
        "generated_by": "project_atlas.orchestration.program.runtimes",
        "tiers": {
            SupportTier.RUNTIME_TESTED.value: (
                "an adapter exists AND has been exercised end to end against "
                "the real runtime; the evidence is named"
            ),
            SupportTier.FIXTURE_ONLY.value: (
                "an adapter exists and passes its tests; no real-runtime run "
                "backs it"
            ),
            SupportTier.UNAVAILABLE.value: (
                "inventoried, deliberately not adapted; the blocker is named"
            ),
        },
        "runtime_tested": [
            row.to_public_dict()
            for row in implemented
            if row.tier is SupportTier.RUNTIME_TESTED
        ],
        "implemented_fixture_only": [
            row.to_public_dict()
            for row in implemented
            if row.tier is SupportTier.FIXTURE_ONLY
        ],
        "unavailable": [row.to_public_dict() for row in inventory_unimplemented()],
        "no_generic_adapter": NO_GENERIC_ADAPTER,
        "universally_unsupported": list(UNIVERSALLY_UNSUPPORTED),
        "concurrent_acceptance": {
            "claim": (
                "Claude Code + Codex concurrent program acceptance: two "
                "runtimes progressing through separate approved task queues "
                "under one supervisor, two workers in flight at once, four "
                "tasks, one invocation, no operator prompt between them"
            ),
            "evidence": (
                "docs/orchestration/program/evidence/SYSTEMWIDE-ACCEPTANCE.md"
            ),
            "does_not_claim": [
                "any runtime other than claude-code 2.1.267 and codex-cli 0.153.4",
                "long or difficult tasks -- these were one-line file writes",
                "more than two concurrent workers",
                "billed spend; the cost figure is a client-side estimate where "
                "it exists at all, and Codex reports none",
            ],
        },
        "merge_authorized": False,
    }


#: Why there is no generic adapter, stated once so it can be quoted.
NO_GENERIC_ADAPTER: Final[str] = (
    "There is deliberately no generic subprocess adapter. Launching a process "
    "is not the same as understanding a runtime's output, its terminal states, "
    "its permission model or its resume contract -- and an adapter that does "
    "the first while claiming the rest is a support claim nobody has checked."
)


def preflight_report(adapter: AdapterKind) -> dict[str, Any]:
    """Describe a runtime and say why it could not run, if it could not.

    Authentication is not probed: doing so costs a model call on at least one
    of these runtimes, and a report that quietly spends money is not a report.
    Whether credentials work is established by the first dispatch, and the
    failure class it produces (`QUOTA_OR_CREDENTIAL`) says so plainly.
    """
    support = describe(adapter)
    payload = support.to_public_dict()
    payload["authentication_checked"] = False
    payload["authentication_note"] = (
        "not probed: a probe costs a model call on a real runtime. A "
        "credential or quota problem surfaces at first dispatch as the "
        "QUOTA_OR_CREDENTIAL failure class, which is never retried in a loop"
    )
    if not support.installed:
        payload["blocked"] = f"{support.executable!r} is not on PATH"
    return payload


def unavailable_reason(exc: AdapterUnavailableError) -> dict[str, str]:
    return {"code": getattr(exc, "code", "ADAPTER_UNAVAILABLE"), "detail": str(exc)}
