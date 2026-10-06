"""Agent profiles: the execution contract a worker runs under.

A profile answers "what may this agent do, where, for how long, with which
runtime, and what evidence must it leave". Shared defaults sit at the bottom,
a role profile refines them, and a task override refines that -- in one
direction only.

DECLARATIVE PERMISSION != ENFORCED BOUNDARY.
``docs/orchestration/program/PERMISSIONS.md`` states, per field, whether Atlas,
the runtime, or the operating system is the thing that actually stops a
violation. Fields Atlas merely *declares* are marked in this module's field
comments too, so nobody reads a permission list here and assumes a sandbox.

Credentials never appear in a profile, a program file, a checkpoint or a
report. A profile names a *mechanism* (``credential``) and an environment
variable allow-list (``env_allowlist``); values are read from the supervisor's
own environment at launch and are never persisted or echoed.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from project_atlas.orchestration.autonomy.models import AgentCapability, AgentRecord
from project_atlas.orchestration.program.models import (
    AcceptanceKind,
    AuthorityExpansionError,
    ProgramError,
)

_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REL_PATH_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]{0,255}$")
_ENV_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class AdapterKind(StrEnum):
    """Runtime adapters this package ships. Unknown values fail closed.

    A value exists here only when a real adapter has been written against the
    runtime's verified interface. There is deliberately no generic
    "any-CLI" member: a subprocess wrapper that launches anything would let a
    program claim support for a runtime nobody has checked, which is exactly
    the claim `SUPPORT-MATRIX.md` exists to prevent.
    """

    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    LOCAL_COMMAND = "local-command"


class CredentialMechanism(StrEnum):
    """How the runtime is expected to authenticate. Names only, never values.

    ``SUBSCRIPTION_OAUTH`` means the runtime uses the operator's own logged-in
    credentials. It is mutually exclusive with an inherited
    ``ANTHROPIC_API_KEY``: Claude Code prefers the API key when both are
    present, so the adapter *removes* that variable from the child environment
    under this mechanism rather than letting an inherited key silently change
    which account is billed.
    """

    SUBSCRIPTION_OAUTH = "SUBSCRIPTION_OAUTH"
    ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY_ENV"
    API_KEY_HELPER = "API_KEY_HELPER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


PermissionMode = Literal[
    "plan",
    "dontAsk",
    "manual",
    "acceptEdits",
    "auto",
    "bypassPermissions",
]

#: Least to most permissive. A task override may lower a profile's mode and
#: may never raise it. ``manual`` outranks ``dontAsk`` because a manual run
#: can still be approved by a permission host, while ``dontAsk`` denies
#: everything outside the allow rules and the read-only command set.
PERMISSION_RANK: Final[dict[str, int]] = {
    "plan": 0,
    "dontAsk": 1,
    "manual": 2,
    "acceptEdits": 3,
    "auto": 4,
    "bypassPermissions": 5,
}

#: Modes under which the runtime stops nothing. Allowed, but never silently:
#: `validate` reports them and the effective-profile digest records them.
UNENFORCED_MODES: Final[frozenset[str]] = frozenset({"bypassPermissions"})


class WorkspaceScope(BaseModel):
    """Where the worker runs and what else it may be pointed at.

    ENFORCEMENT: ``working_subdir`` is enforced by the OS (it is the child
    process's cwd). ``additional_dirs`` is *declarative* -- Claude Code only
    confines file tools to these directories under ``--restricted``; without
    it a worker holding Bash can read outside them. Set ``restricted`` when
    the confinement must be real.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    working_subdir: str = Field(default=".", min_length=1, max_length=256)
    additional_dirs: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    #: Passes ``--restricted`` to a runtime that supports it: removes the
    #: command-running tools unless named, and confines the file tools to the
    #: working directories. This is the flag that turns ``additional_dirs``
    #: from documentation into a boundary.
    restricted: bool = False

    @field_validator("working_subdir")
    @classmethod
    def _subdir(cls, value: str) -> str:
        if value == ".":
            return value
        if not _REL_PATH_RE.fullmatch(value) or ".." in value.split("/"):
            raise ValueError("working_subdir must be a safe relative path")
        return value

    @field_validator("additional_dirs")
    @classmethod
    def _dirs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REL_PATH_RE.fullmatch(item) or ".." in item.split("/"):
                raise ValueError("additional_dirs must be safe relative paths")
        return value


class ProfileLimits(BaseModel):
    """Per-launch bounds. Every one is enforced by this package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_seconds: int = Field(default=1800, ge=1, le=86_400)
    max_attempts: int = Field(default=3, ge=1, le=20)
    #: Forwarded to a runtime that supports a per-launch budget flag. The
    #: runtime enforces it against its own client-side estimate; it is not an
    #: account spending limit and this package never calls it one.
    max_estimated_cost_usd: float | None = Field(default=None, gt=0.0, le=10_000.0)


class AgentProfile(BaseModel):
    """One reusable execution contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    #: Registered agent identity. Two profiles with the same ``agent_id`` are
    #: the same principal, which is what makes "the implementer cannot be the
    #: verifier" checkable rather than cosmetic.
    agent_id: str = Field(min_length=1, max_length=128)
    adapter: AdapterKind
    #: Minimum runtime version, compared component-wise against the version
    #: the adapter reports. ``None`` skips the check and says so in the report.
    adapter_min_version: str | None = None
    capabilities: tuple[AgentCapability, ...] = Field(min_length=1, max_length=8)
    #: Paths the profile may ever mutate, as workspace-relative prefixes. A
    #: task's ``mutation_paths`` must lie inside these. ENFORCEMENT: Atlas
    #: refuses to dispatch outside them, and the lease records them; the
    #: runtime is not told to enforce them unless ``workspace.restricted``.
    allowed_mutation_prefixes: tuple[str, ...] = Field(
        default=("",), max_length=64
    )
    model: str | None = Field(default=None, max_length=128)
    permission_mode: PermissionMode = "dontAsk"
    #: ENFORCEMENT: the runtime. Empty means "do not pass the flag".
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    disallowed_tools: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    #: ENFORCEMENT: the runtime. ``None`` leaves the runtime's default set.
    #: ``()`` disables every built-in tool.
    tools: tuple[str, ...] | None = None
    workspace: WorkspaceScope = Field(default_factory=WorkspaceScope)
    #: Acceptance kinds a task using this profile MUST include. A program that
    #: omits one is rejected by `validate`, not discovered at run time.
    required_evidence: tuple[AcceptanceKind, ...] = Field(
        default_factory=tuple, max_length=8
    )
    limits: ProfileLimits = Field(default_factory=ProfileLimits)
    credential: CredentialMechanism = CredentialMechanism.SUBSCRIPTION_OAUTH
    #: Environment variable NAMES forwarded to the child. Values are read at
    #: launch and never persisted. ``PATH`` and ``HOME`` are always forwarded.
    env_allowlist: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    #: Skip the runtime's discovery of hooks, plugins, MCP servers and
    #: CLAUDE.md. Recommended for reproducibility; see PERMISSIONS.md for what
    #: it changes about credentials.
    isolated_runtime: bool = False
    #: A structured-output schema the runtime validates the worker's final
    #: answer against. Evidence, never authority: acceptance is still what the
    #: supervisor observes locally.
    result_schema: dict[str, Any] | None = None
    #: Adapter-specific construction options. Deliberately NOT overridable by a
    #: task: this is where "which program actually runs" lives for the
    #: local-command adapter, and a task that could rewrite it could execute
    #: anything under a profile approved for something else.
    adapter_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile_id", "agent_id")
    @classmethod
    def _ident(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("identifier must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        return value

    @field_validator("allowed_mutation_prefixes")
    @classmethod
    def _prefixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if item == "":
                continue
            if not _REL_PATH_RE.fullmatch(item) or ".." in item.split("/"):
                raise ValueError("mutation prefixes must be safe relative paths")
        return value

    @field_validator("env_allowlist")
    @classmethod
    def _env(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _ENV_NAME_RE.fullmatch(item):
                raise ValueError("env_allowlist entries must be UPPER_SNAKE names")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> AgentProfile:
        overlap = set(self.allowed_tools) & set(self.disallowed_tools)
        if overlap:
            raise ValueError(
                f"tools both allowed and disallowed: {', '.join(sorted(overlap))}"
            )
        if self.adapter is AdapterKind.LOCAL_COMMAND:
            if self.credential is not CredentialMechanism.NOT_APPLICABLE:
                raise ValueError(
                    "the local-command adapter takes no credential; set "
                    "credential to NOT_APPLICABLE"
                )
            argv = self.adapter_options.get("argv")
            if not isinstance(argv, (list, tuple)) or not argv:
                raise ValueError(
                    "the local-command adapter requires adapter_options.argv "
                    "to be a non-empty list"
                )
            if not all(isinstance(item, str) for item in argv):
                raise ValueError("adapter_options.argv must contain only strings")
        return self

    def command_argv(self) -> tuple[str, ...]:
        """The fixed argv for an adapter that runs one, validated above."""
        raw = self.adapter_options.get("argv", ())
        return tuple(str(item) for item in raw)

    def to_agent_record(self) -> AgentRecord:
        """Project onto the existing registered-agent type used by leasing."""
        return AgentRecord(
            agent_id=self.agent_id,
            capabilities=self.capabilities,
            available=True,
        )

    def permits_mutation(self, path: str) -> bool:
        """True when ``path`` lies under an allowed mutation prefix."""
        for prefix in self.allowed_mutation_prefixes:
            if prefix == "":
                return True
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return True
        return False


class ProfileSet(BaseModel):
    """Shared defaults plus the role profiles a program may reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    #: Field values merged underneath every profile. Only fields a profile
    #: does not state itself are taken from here, so a role profile always
    #: wins over the shared default -- and a default can never *widen* a role
    #: profile, because it is only ever consulted for absent fields.
    defaults: dict[str, Any] = Field(default_factory=dict)
    profiles: dict[str, AgentProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_match(self) -> ProfileSet:
        for key, profile in self.profiles.items():
            if key != profile.profile_id:
                raise ValueError(
                    f"profile key {key!r} does not match profile_id "
                    f"{profile.profile_id!r}"
                )
        return self

    def resolve(self, profile_ref: str) -> AgentProfile:
        profile = self.profiles.get(profile_ref)
        if profile is None:
            raise ProgramError(
                f"unknown profile {profile_ref}", code="UNKNOWN_PROFILE"
            )
        return profile


#: Override keys a task may state. Anything else is refused outright rather
#: than silently ignored, so a typo in a program file cannot look like a
#: narrower permission that was never applied.
OVERRIDABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "model",
        "permission_mode",
        "allowed_tools",
        "disallowed_tools",
        "tools",
        "capabilities",
        "allowed_mutation_prefixes",
        "required_evidence",
        "limits",
        "workspace",
        "result_schema",
    }
)


def _as_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise AuthorityExpansionError(
            f"override for {field} must be a list", code="OVERRIDE_MALFORMED"
        )
    for item in items:
        if not isinstance(item, str):
            raise AuthorityExpansionError(
                f"override for {field} must contain strings",
                code="OVERRIDE_MALFORMED",
            )
    return tuple(str(item) for item in items)


def resolve_effective_profile(
    profile_set: ProfileSet,
    *,
    profile_ref: str,
    override: dict[str, Any] | None = None,
) -> AgentProfile:
    """Apply shared defaults, then the role profile, then a task override.

    The override may only *narrow*. Every widening attempt raises
    ``AuthorityExpansionError`` with a specific code, so a program file that
    tries to buy itself a bigger tool set, a longer run, a more permissive
    permission mode or a wider mutation surface is rejected at validation
    time rather than at the moment it would have mattered.
    """
    base = profile_set.resolve(profile_ref)
    if not override:
        return base

    unknown = sorted(set(override) - OVERRIDABLE_FIELDS)
    if unknown:
        raise AuthorityExpansionError(
            f"task override names non-overridable field(s): {', '.join(unknown)}",
            code="OVERRIDE_FIELD_FORBIDDEN",
        )

    updates: dict[str, Any] = {}

    if "model" in override:
        model = override["model"]
        if model is not None and not isinstance(model, str):
            raise AuthorityExpansionError(
                "override for model must be a string or null",
                code="OVERRIDE_MALFORMED",
            )
        updates["model"] = model

    if "permission_mode" in override:
        mode = override["permission_mode"]
        if mode not in PERMISSION_RANK:
            raise AuthorityExpansionError(
                f"unknown permission mode {mode!r}", code="OVERRIDE_MALFORMED"
            )
        if PERMISSION_RANK[str(mode)] > PERMISSION_RANK[base.permission_mode]:
            raise AuthorityExpansionError(
                f"task override raises permission mode from "
                f"{base.permission_mode} to {mode}",
                code="PERMISSION_MODE_ESCALATION",
            )
        updates["permission_mode"] = mode

    if "allowed_tools" in override:
        wanted = _as_tuple(override["allowed_tools"], field="allowed_tools")
        extra = sorted(set(wanted) - set(base.allowed_tools))
        if extra:
            raise AuthorityExpansionError(
                f"task override adds tools not allowed by profile "
                f"{base.profile_id}: {', '.join(extra)}",
                code="TOOL_ALLOWLIST_EXPANSION",
            )
        updates["allowed_tools"] = wanted

    if "disallowed_tools" in override:
        wanted = _as_tuple(override["disallowed_tools"], field="disallowed_tools")
        missing = sorted(set(base.disallowed_tools) - set(wanted))
        if missing:
            raise AuthorityExpansionError(
                f"task override drops denied tool(s): {', '.join(missing)}",
                code="TOOL_DENYLIST_CONTRACTION",
            )
        updates["disallowed_tools"] = wanted

    if "tools" in override:
        raw = override["tools"]
        if raw is None:
            raise AuthorityExpansionError(
                "task override cannot reset tools to the runtime default",
                code="TOOL_SET_EXPANSION",
            )
        wanted = _as_tuple(raw, field="tools")
        if base.tools is not None:
            extra = sorted(set(wanted) - set(base.tools))
            if extra:
                raise AuthorityExpansionError(
                    f"task override adds built-in tool(s): {', '.join(extra)}",
                    code="TOOL_SET_EXPANSION",
                )
        updates["tools"] = wanted

    if "capabilities" in override:
        wanted_caps = _as_tuple(override["capabilities"], field="capabilities")
        try:
            parsed = tuple(AgentCapability(item) for item in wanted_caps)
        except ValueError as exc:
            raise AuthorityExpansionError(
                f"unknown capability in override: {exc}", code="OVERRIDE_MALFORMED"
            ) from exc
        extra_caps = sorted(set(parsed) - set(base.capabilities))
        if extra_caps:
            raise AuthorityExpansionError(
                "task override adds capabilities: "
                + ", ".join(cap.value for cap in extra_caps),
                code="CAPABILITY_EXPANSION",
            )
        if not parsed:
            raise AuthorityExpansionError(
                "task override cannot clear every capability",
                code="OVERRIDE_MALFORMED",
            )
        updates["capabilities"] = parsed

    if "allowed_mutation_prefixes" in override:
        wanted = _as_tuple(
            override["allowed_mutation_prefixes"], field="allowed_mutation_prefixes"
        )
        outside = sorted(item for item in wanted if not base.permits_mutation(item))
        if outside:
            raise AuthorityExpansionError(
                f"task override widens the mutation surface: {', '.join(outside)}",
                code="MUTATION_SURFACE_EXPANSION",
            )
        updates["allowed_mutation_prefixes"] = wanted

    if "required_evidence" in override:
        wanted = _as_tuple(override["required_evidence"], field="required_evidence")
        try:
            kinds = tuple(AcceptanceKind(item) for item in wanted)
        except ValueError as exc:
            raise AuthorityExpansionError(
                f"unknown acceptance kind in override: {exc}",
                code="OVERRIDE_MALFORMED",
            ) from exc
        dropped = sorted(set(base.required_evidence) - set(kinds))
        if dropped:
            raise AuthorityExpansionError(
                "task override drops required evidence: "
                + ", ".join(kind.value for kind in dropped),
                code="EVIDENCE_REQUIREMENT_WEAKENED",
            )
        updates["required_evidence"] = kinds

    if "limits" in override:
        raw_limits = override["limits"]
        if not isinstance(raw_limits, dict):
            raise AuthorityExpansionError(
                "override for limits must be an object", code="OVERRIDE_MALFORMED"
            )
        merged = base.limits.model_dump()
        merged.update(raw_limits)
        # Widening is checked on the RAW requested values, before validation.
        # A request for max_attempts=40 against a base of 4 is an attempt to
        # widen; that it also happens to exceed ProfileLimits' own ceiling is
        # incidental, and reporting it as a malformed field would hide what
        # the program file actually tried to do.
        for field_name in ("max_seconds", "max_attempts"):
            requested = raw_limits.get(field_name)
            if not isinstance(requested, (int, float)) or isinstance(requested, bool):
                continue
            current = getattr(base.limits, field_name)
            if requested > current:
                raise AuthorityExpansionError(
                    f"task override raises {field_name} from {current} to "
                    f"{requested}",
                    code="LIMIT_EXPANSION",
                )
        try:
            limits = ProfileLimits.model_validate(merged)
        except ValidationError as exc:
            raise AuthorityExpansionError(
                f"task override for limits is not a valid limit set: {exc}",
                code="OVERRIDE_MALFORMED",
            ) from exc
        base_cost = base.limits.max_estimated_cost_usd
        new_cost = limits.max_estimated_cost_usd
        if base_cost is not None and (new_cost is None or new_cost > base_cost):
            raise AuthorityExpansionError(
                "task override raises or removes max_estimated_cost_usd",
                code="LIMIT_EXPANSION",
            )
        updates["limits"] = limits

    if "workspace" in override:
        raw_ws = override["workspace"]
        if not isinstance(raw_ws, dict):
            raise AuthorityExpansionError(
                "override for workspace must be an object", code="OVERRIDE_MALFORMED"
            )
        merged_ws = base.workspace.model_dump()
        merged_ws.update(raw_ws)
        workspace = WorkspaceScope.model_validate(merged_ws)
        extra_dirs = sorted(
            set(workspace.additional_dirs) - set(base.workspace.additional_dirs)
        )
        if extra_dirs:
            raise AuthorityExpansionError(
                f"task override adds workspace dirs: {', '.join(extra_dirs)}",
                code="WORKSPACE_SCOPE_EXPANSION",
            )
        if base.workspace.restricted and not workspace.restricted:
            raise AuthorityExpansionError(
                "task override clears the profile's restricted flag",
                code="WORKSPACE_SCOPE_EXPANSION",
            )
        if workspace.working_subdir != base.workspace.working_subdir:
            root = base.workspace.working_subdir
            candidate = workspace.working_subdir
            inside = root == "." or candidate == root or candidate.startswith(
                root.rstrip("/") + "/"
            )
            if not inside:
                raise AuthorityExpansionError(
                    "task override moves the working directory outside the "
                    "profile's own subdirectory",
                    code="WORKSPACE_SCOPE_EXPANSION",
                )
        updates["workspace"] = workspace

    if "result_schema" in override:
        schema = override["result_schema"]
        if schema is not None and not isinstance(schema, dict):
            raise AuthorityExpansionError(
                "override for result_schema must be an object or null",
                code="OVERRIDE_MALFORMED",
            )
        updates["result_schema"] = schema

    return base.model_copy(update=updates)


def build_profile_set(
    *,
    defaults: dict[str, Any] | None,
    raw_profiles: dict[str, Any],
) -> ProfileSet:
    """Merge shared defaults underneath each role profile, then validate.

    Merging happens here, at load time, rather than at resolve time: a
    ``ProfileSet`` that has been constructed is already fully resolved, so no
    later reader has to remember that some field might still be inherited.
    Only keys a role profile does not state are taken from ``defaults``, which
    is what makes the direction one-way -- a default can fill a hole but can
    never overwrite, and therefore can never widen, a stated role permission.

    ``profile_id`` is never taken from defaults; it is the profile's key.
    """
    shared = dict(defaults or {})
    for reserved in ("profile_id",):
        shared.pop(reserved, None)

    merged: dict[str, AgentProfile] = {}
    for key, raw in raw_profiles.items():
        if not isinstance(raw, dict):
            raise ProgramError(
                f"profile {key} must be an object", code="PROFILE_MALFORMED"
            )
        body: dict[str, Any] = {**shared, **raw}
        body["profile_id"] = key
        merged[key] = AgentProfile.model_validate(body)
    return ProfileSet(defaults=shared, profiles=merged)
