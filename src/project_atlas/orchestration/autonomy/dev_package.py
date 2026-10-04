"""Generic, deterministic DEVQ package builder (frontier F1, steps 4-5).

``dev_first_run`` builds exactly one hard-wired package (ATLAS-DEVQ-0001). This module builds the
same package structure for ANY task from an explicit, strictly validated ``PackageSpec`` (JSON).

Properties:
  * Deterministic: same spec content (key order irrelevant) -> byte-identical rendered package.
  * Identity is derived, never supplied: ``execution_id = f"{task_id}-E{execution_ordinal}"``.
  * Sealed instructions: a digest of the statement and acceptance commands is bound into the
    WorkItem's acceptance contract, so changing either changes ``work_seal``.
  * Repair packages (``attempt_kind == "repair"``) reproduce the canonical WorkItem that
    ``dev_contracts.materialize_repair`` produced, seal for seal: identity follows its rule
    (``task_id = f"{lineage_root}-R{attempt - 1}"``,
    ``execution_id = f"{parent_execution_id}-R{attempt - 1}"``), ``parent_task_id`` is carried,
    and the acceptance contract is taken verbatim (the parent's sealed instructions digest must
    match this spec's statement and commands, so a repair can never change what runs). A repair
    never checks out ``main``: it names the previous result branch explicitly, and
    ``verify_checkout_ref`` (pure; the caller supplies the resolved sha) refuses unless that
    branch resolves to exactly the sealed ``base_revision``.
  * Every package (implementation and repair) carries ``workflow_inputs.base_revision``, the
    sealed base revision: a branch can move after the pre-dispatch check, so the execute
    workflow asserts its checked-out HEAD equals it and fails before the agent runs otherwise.
    ``workflow_inputs`` is exactly ``build_sealed_dispatch_payload``'s output, the same payload
    the live adapter dispatches, so package and live dispatch share one payload identity.
  * Fail-closed: every rule violation raises ``PackageSpecError`` with a stable ``reason`` code.
  * Acceptance commands must be plain, read-only ``pytest``/``ruff``/``mypy`` checks (the
    executor's tool allowlist matches on the first token; ``python -m``/env prefixes/shell
    syntax/write-capable flags would not run there or would widen what runs).
  * The forbidden-path HARD FLOOR is always required; allowed scope may not overlap it
    (case-insensitively, segment-wise). Inside the autonomy control-plane package the floor is
    per module: only modules in ``OWNER_SCOPABLE_AUTONOMY_MODULES`` may be scoped, and new
    modules are forbidden by default. Tool-config basenames and ``.git`` are never scopable.
  * Secrets are never inspected and never asserted present.

Nothing here dispatches, ingests, merges or reads any secret; it only produces a document.

``bind_package_to_work`` (ATLAS-DEVQ-0005) is the pure half of binding a rendered package to a
dispatch: it proves that a rendered document is the one with the expected ``package_sha256``,
that its identity, scope, contract and dispatch payload are those of one sealed work item, and
it returns the payload REBUILT from that work item. Binding is not a grant: it neither issues,
consumes nor verifies an owner dispatch grant, ``grant_required`` stays as rendered, and a
successful binding never permits a dispatch and never overrides a classifier or platform denial.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

from project_atlas.orchestration.autonomy.dev_contracts import (
    ContractError,
    WorkItem,
    make_work,
    norm_path,
)
from project_atlas.orchestration.autonomy.dev_fabric_adapter import (
    AGENT_BRANCH,
    BASE_REVISION_INPUT,
    EVIDENCE_ARTIFACT,
    REPORT_ARTIFACT,
    VERIFY_WORKFLOW,
    DispatchPayload,
    build_sealed_dispatch_payload,
)
from project_atlas.orchestration.autonomy.dev_first_run import render_package

__all__ = [
    "AUTONOMY_FLOOR_MODULES",
    "BUILDER_ID",
    "FORBIDDEN_FLOOR",
    "OWNER_SCOPABLE_AUTONOMY_MODULES",
    "PackageBinding",
    "PackageSpec",
    "PackageSpecError",
    "bind_package_to_work",
    "build_package",
    "build_work",
    "effective_statement",
    "instructions_sha256",
    "load_spec",
    "package_sha256",
    "render_package",
    "repair_spec_from_work",
    "sealed_instructions_sha256",
    "spec_sha256",
    "verify_checkout_ref",
]

# Builder-version identity recorded in ``provenance.builder``. It changes whenever the rendered
# package for a given spec changes, so a package always names the builder that can reproduce it.
#   dev_package/1  every package rendered up to main f17f582846493a5e6edde07b59bc0169c7a7390f.
#                  Under that id the rendered shape changed more than once without a bump
#                  (repair support, the repair-only ``base_revision`` input, then the canonical
#                  sealed payload for implementation packages); the id alone therefore does not
#                  identify one shape. Those packages are historical evidence and stay as they
#                  are; this builder no longer renders or accepts them.
#   dev_package/2  the canonical sealed dispatch payload for every package kind. Apart from
#                  this id, a /2 repair package is identical to the last /1 repair shape.
BUILDER_ID = "dev_package/2"
BASE_BRANCH = "main"
# What every package states about dispatch authority. Rendering or binding a package never
# satisfies it.
GRANT_REQUIRED = "ONE_WORKFLOW_DISPATCH_GRANT"
# ``BASE_REVISION_INPUT`` (imported from ``dev_fabric_adapter``, re-exported here) names the
# atlas-agent-execute.yml input the workflow asserts its checked-out HEAD against. The canonical
# payload builder (``build_sealed_dispatch_payload``) emits it for every package.
INSTRUCTIONS_PREFIX = "instructions_sha256="

AUTONOMY_PACKAGE = "src/project_atlas/orchestration/autonomy/"
# Autonomy control-plane modules the owner has explicitly classified as scopable by a generated
# package. Everything else in AUTONOMY_PACKAGE is on the floor (per module, below), and an
# allowed path inside AUTONOMY_PACKAGE must be exactly one of these opt-in modules -- so a NEW
# module is forbidden by default until someone consciously classifies it (a test enumerates the
# directory and fails on any unclassified module).
OWNER_SCOPABLE_AUTONOMY_MODULES: tuple[str, ...] = ("dev_github_port.py",)
AUTONOMY_FLOOR_MODULES: tuple[str, ...] = (
    "__init__.py",
    "adversarial.py",
    "authentic_estate.py",
    "cli.py",
    "continuation.py",
    "continuation_broker.py",
    "dag.py",
    "dev_contracts.py",
    "dev_crosswalk.py",
    "dev_fabric_adapter.py",
    "dev_first_run.py",
    "dev_package.py",
    "dev_planner.py",
    "dev_queue.py",
    "dev_spool_transport.py",
    "dev_transport.py",
    "discovery.py",
    "evidence.py",
    "exact_main_closure.py",
    "governor.py",
    "iv_routing.py",
    "lease_projection.py",
    "lease_recovery.py",
    "leases.py",
    "local_dispatch_port.py",
    "loop.py",
    "models.py",
    "overlap.py",
    "owner_gates.py",
    "rehydration.py",
    "remediation.py",
    "return_gate.py",
    "trust.py",
)

# Paths no generated package may ever leave writable. Compared case-insensitively after
# stripping a trailing "/".
FORBIDDEN_FLOOR: tuple[str, ...] = (
    ".git/",
    ".github/",
    ".claude/",
    "autonomy/",
    "infra/atlas-runner/",
    "pyproject.toml",
    "conftest.py",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    *(AUTONOMY_PACKAGE + m for m in AUTONOMY_FLOOR_MODULES),
)
# Tool-configuration / import-hook basenames no allowed path may name at ANY depth (they change
# how the acceptance commands themselves behave); likewise nothing under a ``.git`` segment.
RESERVED_BASENAMES = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        ".mypy.ini",
        "pyproject.toml",
    }
)
ALLOWED_COMMANDS = frozenset({"pytest", "ruff", "mypy"})

# Size caps. GitHub caps a workflow_dispatch payload at 65,535 characters; ``task_prompt`` is
# assembled by ``build_dispatch_payload`` from the statement, scope, contract and commands, so
# each part is capped and the assembled prompt is checked against a conservative overall cap
# measured in UTF-8 BYTES (never fewer than characters).
MAX_STATEMENT_BYTES = 8000
MAX_COMMANDS = 16
MAX_COMMAND_CHARS = 400
MAX_LIST_ITEMS = 64
MAX_FORBIDDEN_ITEMS = 128  # the hard floor alone is ~50 entries
MAX_ITEM_CHARS = 500
MAX_PROMPT_BYTES = 32000
MAX_ORDINAL = 9999
MAX_ATTEMPTS_CEILING = 10

# What an attempt IS, stated explicitly (never inferred from ``attempt``): an "implementation"
# attempt (any attempt number, e.g. a re-run after lost result bytes) never claims to be a
# repair; a "repair" attempt must follow a previous attempt and name the findings it resolves.
ATTEMPT_KINDS = frozenset({"implementation", "repair"})
RESOLVE_PREFIX = "RESOLVE:"

REPAIR_SUFFIX = (
    " This is a REPAIR attempt: resolve every RESOLVE:<finding_id> listed "
    "in the acceptance contract on top of the previous result branch."
)

# Line prefixes that build_dispatch_payload uses for its own header/scope/acceptance lines (and
# the workflow wrapper uses around the prompt), plus list/quote markers. No statement line and
# no acceptance_contract / expected_outputs entry may imitate them. Compared after NFKC,
# whitespace collapsing and casefolding (``_norm_head``).
RESERVED_PREFIXES: tuple[str, ...] = (
    "atlas dev-loop task",
    "repository ",
    "only modify",
    "never modify",
    "acceptance",
    "run:",
    "new tests must",
    "bounded task",
    "constraints:",
    "-",
    "*",
    ">",
    "\u2022",  # bullet
    "\u2023",  # triangular bullet
    "\u25e6",  # white bullet
    "\u2043",  # hyphen bullet
    "\u2219",  # bullet operator
)

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AUTHORITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SEAL = re.compile(r"^[0-9a-f]{64}$")
MAX_BRANCH_CHARS = 255
# AGENT_BRANCH with bounded digit groups (run_id, run_attempt).
_RESULT_BRANCH = re.compile(r"^atlas/agent-[0-9]{1,20}-[0-9]{1,6}$")
_PATH_CHARS = re.compile(r"^[A-Za-z0-9._/-]+$")
# Same shape rules as the "Validate base_branch shape" step of atlas-agent-execute.yml.
_BRANCH_CHARS = re.compile(r"^[A-Za-z0-9._/-]+$")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Shell metacharacters that would chain, substitute, redirect, glob, expand, comment or escape.
# Balanced quotes are allowed (checked by shlex) so ``pytest -k 'a or b'`` stays expressible;
# with every chaining/substitution character rejected they cannot start a second command.
_METACHARS = frozenset(";&|`$<>\\#*?~{}()!\n\r\t\x00")
_LINE_SEPARATORS = frozenset("\u2028\u2029\u0085")

# Write-capable / config-redirecting / interactive / network flags (pytest, ruff, mypy). Long
# flags are also denied in any abbreviated form (argparse prefix matching) and as --flag=value.
_DENIED_LONG: tuple[str, ...] = (
    "--basetemp",
    "--rootdir",
    "--confcutdir",
    "--override-ini",
    "--config-file",
    "--config",
    "--fix",
    "--unsafe-fixes",
    "--add-noqa",
    "--watch",
    "--install-types",
    "--python-executable",
    "--output-file",
    "--junitxml",
    "--junit-xml",
    "--junit-prefix",
    "--log-file",
    "--debug",
    "--pastebin",
    "--pdb",
    "--pdbcls",
    "--trace",
    "--cache-dir",
    "--collect-only",
    "--exit-zero",
    "--report-log",
    "--html",
    "--cov-config",
    "--sqlite-cache",
)
# Single-dash flag letters that are denied anywhere in a short-flag cluster (-p plugin,
# -c config/inifile, -o ini override). Conservative: e.g. ``-kfoo`` is refused too.
_DENIED_SHORT = frozenset("pco")


class PackageSpecError(ValueError):
    """A spec violates a fail-closed rule. ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _fail(reason: str, detail: str) -> PackageSpecError:
    return PackageSpecError(reason, detail)


def _bad_chars(s: str, *, allow_newline: bool) -> bool:
    """True if ``s`` holds a control/format char (Cc/Cf) or a Unicode line separator."""
    for c in s:
        if c == "\n" and allow_newline:
            continue
        if c in _LINE_SEPARATORS or unicodedata.category(c) in ("Cc", "Cf"):
            return True
    return False


def _str(name: str, v: object) -> str:
    if not isinstance(v, str):
        raise _fail("SPEC_TYPE", f"{name} must be a string")
    return v


def _int(name: str, v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise _fail("SPEC_TYPE", f"{name} must be an integer (bool is not an integer)")
    return v


def _norm_head(s: str) -> str:
    """Normalisation for reserved-prefix checks: NFKC, collapsed whitespace, casefold."""
    return " ".join(unicodedata.normalize("NFKC", s).split()).casefold()


def _reserved_prefix(s: str) -> bool:
    head = _norm_head(s)
    return any(head.startswith(p) for p in RESERVED_PREFIXES)


def _str_tuple(name: str, v: object, max_items: int = MAX_LIST_ITEMS) -> tuple[str, ...]:
    if not isinstance(v, tuple) or not all(isinstance(x, str) for x in v):
        raise _fail("SPEC_TYPE", f"{name} must be a list of strings")
    if len(v) > max_items:
        raise _fail("LIST_TOO_LONG", f"{name} has more than {max_items} entries")
    for x in v:
        if not x.strip():
            raise _fail("LIST_ITEM_EMPTY", f"{name} contains an empty entry")
        if len(x) > MAX_ITEM_CHARS or _bad_chars(x, allow_newline=False):
            raise _fail("LIST_ITEM_INVALID", f"{name} entry too long or contains control chars")
    if len(set(v)) != len(v):
        raise _fail("LIST_DUPLICATE", f"{name} contains duplicate entries")
    return v


def _canon_path(name: str, p: str) -> str:
    """Canonical comparison key (lower-cased) for one scope entry; fail closed otherwise."""
    if not _PATH_CHARS.fullmatch(p):
        raise _fail("PATH_INVALID", f"{name} entry {p!r} must match {_PATH_CHARS.pattern}")
    if p.startswith("/") or ".." in p.split("/"):
        raise _fail("PATH_INVALID", f"{name} entry {p!r} is absolute or escapes the repo")
    stripped = p.rstrip("/")
    for seg in stripped.split("/"):
        if seg != seg.strip() or seg.endswith("."):
            raise _fail("PATH_INVALID", f"{name} entry {p!r} has a padded or dot-ended segment")
    n = norm_path(stripped)
    if n is None:
        raise _fail("PATH_INVALID", f"{name} entry {p!r} is malformed (no globs/absolute/escapes)")
    return n.lower()


def _overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _check_arg(cmd: str, tok: str) -> None:
    if tok.startswith("--"):
        name, _, value = tok.partition("=")
        if name == "--":
            raise _fail("COMMAND_FLAG_DENIED", f"bare '--' not allowed: {cmd!r}")
        if (
            name.startswith("--fix")
            or name.endswith("-report")
            or any(d.startswith(name) for d in _DENIED_LONG)
        ):
            raise _fail("COMMAND_FLAG_DENIED", f"flag {name!r} not allowed: {cmd!r}")
        if value:
            _check_path_arg(cmd, value)
        return
    if tok.startswith("-") and len(tok) > 1:
        if any(c in _DENIED_SHORT for c in tok[1:]):
            raise _fail("COMMAND_FLAG_DENIED", f"short flag {tok!r} not allowed: {cmd!r}")
        return
    _check_path_arg(cmd, tok)


def _check_path_arg(cmd: str, arg: str) -> None:
    if arg.startswith("/") or ".." in arg.split("/"):
        raise _fail("COMMAND_PATH_INVALID", f"absolute or escaping argument {arg!r}: {cmd!r}")


def _check_command(cmd: str) -> None:
    if not cmd.strip():
        raise _fail("COMMAND_EMPTY", "acceptance command is empty")
    if len(cmd) > MAX_COMMAND_CHARS:
        raise _fail("COMMAND_TOO_LONG", f"acceptance command exceeds {MAX_COMMAND_CHARS} chars")
    if not cmd.isascii():
        raise _fail("COMMAND_NOT_ASCII", f"acceptance command must be ASCII: {cmd!r}")
    bad = sorted({c for c in cmd if c in _METACHARS or not c.isprintable()})
    if bad:
        raise _fail("COMMAND_METACHAR", f"shell metacharacter(s) {bad!r} in {cmd!r}")
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        raise _fail("COMMAND_UNPARSEABLE", f"{cmd!r}: {exc}") from exc
    if not tokens:
        raise _fail("COMMAND_EMPTY", "acceptance command is empty")
    first = tokens[0]
    if _ENV_ASSIGN.match(first):
        raise _fail("COMMAND_ENV_PREFIX", f"env-assignment prefix not allowed in {cmd!r}")
    if first.startswith("python") and "-m" in tokens[1:2]:
        raise _fail("COMMAND_PYTHON_M", f"'python -m' is not allowlisted: {cmd!r}")
    if first not in ALLOWED_COMMANDS:
        raise _fail(
            "COMMAND_NOT_ALLOWED",
            f"first token {first!r} not in {sorted(ALLOWED_COMMANDS)}: {cmd!r}",
        )
    if cmd != cmd.strip() or (cmd != first and not cmd.startswith(first + " ")):
        raise _fail("COMMAND_NOT_ALLOWED", f"command must start with a bare {first!r}: {cmd!r}")
    if first == "ruff":
        sub = tokens[1] if len(tokens) > 1 else ""
        if sub == "format" and "--check" not in tokens[2:]:
            raise _fail("COMMAND_RUFF_MODE", f"'ruff format' requires --check: {cmd!r}")
        if sub not in ("check", "format"):
            raise _fail("COMMAND_RUFF_MODE", f"ruff must be 'check' or 'format --check': {cmd!r}")
        args = tokens[2:]
    else:
        args = tokens[1:]
    for tok in args:
        _check_arg(cmd, tok)


def _check_base_branch(branch: str) -> None:
    """Workflow shape rules: charset, no ``..``, no leading ``/``, no ``.git`` suffix."""
    if (
        len(branch) > MAX_BRANCH_CHARS
        or not _BRANCH_CHARS.fullmatch(branch)
        or ".." in branch
        or branch.startswith("/")
        or branch.endswith(".git")
    ):
        raise _fail("BASE_BRANCH_INVALID", f"base_branch {branch!r} is not a safe branch name")


def _check_statement(statement: str) -> None:
    if not statement.strip():
        raise _fail("STATEMENT_EMPTY", "statement is empty")
    if len(statement.encode()) > MAX_STATEMENT_BYTES:
        raise _fail("STATEMENT_TOO_LONG", f"statement exceeds {MAX_STATEMENT_BYTES} UTF-8 bytes")
    if _bad_chars(statement, allow_newline=True):
        raise _fail("STATEMENT_INVALID", "statement contains control/format/line-separator chars")
    for line in statement.split("\n"):
        if _reserved_prefix(line):
            raise _fail(
                "STATEMENT_RESERVED_PREFIX",
                f"statement line imitates a reserved prompt line: {line.strip()[:60]!r}",
            )


@dataclass(frozen=True)
class PackageSpec:
    """Explicit input for one package. ``execution_id`` is derived, never supplied.

    ``parent_task_id``, ``parent_execution_id`` and ``base_branch`` are repair-only: all three
    are required for ``attempt_kind == "repair"`` and refused for ``"implementation"``.
    """

    task_id: str
    execution_ordinal: int
    lineage_root: str
    repository: str
    base_revision: str
    authority_ref: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    acceptance_contract: tuple[str, ...]
    statement: str
    acceptance_commands: tuple[str, ...]
    attempt: int
    max_attempts: int
    attempt_kind: str
    parent_task_id: str | None = None
    parent_execution_id: str | None = None
    base_branch: str | None = None
    expected_work_seal: str | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "lineage_root"):
            if not _TASK_ID.fullmatch(_str(name, getattr(self, name))):
                raise _fail("TASK_ID_INVALID", f"{name} must match {_TASK_ID.pattern}")
        ordinal = _int("execution_ordinal", self.execution_ordinal)
        if not 1 <= ordinal <= MAX_ORDINAL:
            raise _fail("ORDINAL_INVALID", f"execution_ordinal must be in 1..{MAX_ORDINAL}")
        repo = _str("repository", self.repository)
        if not _REPO.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
            raise _fail("REPOSITORY_INVALID", "repository must be <owner>/<name>")
        if not _SHA.fullmatch(_str("base_revision", self.base_revision)):
            raise _fail("BASE_REVISION_INVALID", "base_revision must be 40 lowercase hex chars")
        if not _AUTHORITY.fullmatch(_str("authority_ref", self.authority_ref)):
            raise _fail("AUTHORITY_INVALID", f"authority_ref must match {_AUTHORITY.pattern}")

        _check_statement(_str("statement", self.statement))

        allowed = _str_tuple("allowed_paths", self.allowed_paths)
        forbidden = _str_tuple("forbidden_paths", self.forbidden_paths, MAX_FORBIDDEN_ITEMS)
        for name in ("expected_outputs", "acceptance_contract"):
            entries = _str_tuple(name, getattr(self, name))
            if not entries:
                raise _fail("LIST_EMPTY", f"{name} must not be empty")
            for e in entries:
                if _reserved_prefix(e):
                    raise _fail(
                        "LIST_ITEM_RESERVED_PREFIX",
                        f"{name} entry imitates a reserved prompt line: {e[:60]!r}",
                    )
        reserved = _norm_head(INSTRUCTIONS_PREFIX.rstrip("="))
        inherited = [
            i for i, c in enumerate(self.acceptance_contract) if _norm_head(c).startswith(reserved)
        ]
        # Only a repair may carry such an entry: the one it inherited from its sealed parent
        # (checked byte-exactly against this spec's own instructions below).
        # (An invalid attempt_kind falls through to ATTEMPT_KIND_INVALID below.)
        if inherited and self.attempt_kind == "implementation":
            raise _fail("CONTRACT_RESERVED", f"{INSTRUCTIONS_PREFIX!r} entries are builder-only")
        if not allowed:
            raise _fail("ALLOWED_PATHS_EMPTY", "allowed_paths must not be empty")
        allowed_n = [_canon_path("allowed_paths", p) for p in allowed]
        forbidden_n = [_canon_path("forbidden_paths", p) for p in forbidden]
        missing = [f for f in FORBIDDEN_FLOOR if f.rstrip("/").lower() not in forbidden_n]
        if missing:
            raise _fail("FORBIDDEN_FLOOR_MISSING", f"forbidden_paths lacks floor entries {missing}")
        for a in allowed_n:
            for f in forbidden_n:
                if _overlaps(a, f):
                    raise _fail("SCOPE_OVERLAP", f"allowed {a!r} overlaps forbidden {f!r}")
        package = AUTONOMY_PACKAGE.rstrip("/").lower()
        scopable = {f"{package}/{m.lower()}" for m in OWNER_SCOPABLE_AUTONOMY_MODULES}
        for a in allowed_n:
            segs = a.split("/")
            if ".git" in segs or segs[-1] in RESERVED_BASENAMES:
                raise _fail("PATH_RESERVED_BASENAME", f"allowed {a!r} names a reserved file")
            if _overlaps(a, package) and a not in scopable:
                raise _fail(
                    "AUTONOMY_SCOPE_RESTRICTED",
                    f"allowed {a!r} is in the autonomy package but not an opt-in module",
                )

        commands = self.acceptance_commands
        if not isinstance(commands, tuple) or not all(isinstance(c, str) for c in commands):
            raise _fail("SPEC_TYPE", "acceptance_commands must be a list of strings")
        if not commands:
            raise _fail("COMMANDS_EMPTY", "acceptance_commands must not be empty")
        if len(commands) > MAX_COMMANDS:
            raise _fail("COMMANDS_TOO_MANY", f"more than {MAX_COMMANDS} acceptance commands")
        for c in commands:
            _check_command(c)  # command-specific reason codes before the generic list rules
        _str_tuple("acceptance_commands", commands)

        attempt = _int("attempt", self.attempt)
        ceiling = _int("max_attempts", self.max_attempts)
        if not 1 <= ceiling <= MAX_ATTEMPTS_CEILING or not 1 <= attempt <= ceiling:
            raise _fail(
                "ATTEMPT_INVALID",
                f"require 1 <= attempt <= max_attempts <= {MAX_ATTEMPTS_CEILING}",
            )
        kind = _str("attempt_kind", self.attempt_kind)
        if kind not in ATTEMPT_KINDS:
            raise _fail(
                "ATTEMPT_KIND_INVALID", f"attempt_kind must be one of {sorted(ATTEMPT_KINDS)}"
            )
        if kind == "repair":
            if attempt < 2:
                raise _fail("REPAIR_ATTEMPT_INVALID", "a repair attempt requires attempt > 1")
            if not any(c.startswith(RESOLVE_PREFIX) for c in self.acceptance_contract):
                raise _fail(
                    "REPAIR_RESOLVE_MISSING",
                    f"a repair attempt needs at least one {RESOLVE_PREFIX!r} contract entry",
                )
            self._check_repair(attempt, inherited)
        else:
            for name in _REPAIR_ONLY_FIELDS:
                if getattr(self, name) is not None:
                    raise _fail(
                        "IMPLEMENTATION_REPAIR_FIELD",
                        f"{name} is only valid for attempt_kind 'repair' (implementation "
                        f"packages always check out {BASE_BRANCH!r})",
                    )

    def _check_repair(self, attempt: int, inherited: list[int]) -> None:
        """A repair spec must describe exactly the WorkItem ``materialize_repair`` produced."""
        for name in ("parent_task_id", "parent_execution_id"):
            v = getattr(self, name)
            if v is None:
                raise _fail("REPAIR_PARENT_MISSING", f"a repair attempt requires {name}")
            if not _TASK_ID.fullmatch(_str(name, v)):
                raise _fail("REPAIR_PARENT_INVALID", f"{name} must match {_TASK_ID.pattern}")
        if self.task_id != f"{self.lineage_root}-R{attempt - 1}":
            raise _fail(
                "REPAIR_IDENTITY_MISMATCH",
                "a repair task_id must be '<lineage_root>-R<attempt - 1>' (materialize_repair)",
            )
        if self.base_branch is None:
            raise _fail(
                "REPAIR_BASE_BRANCH_MISSING",
                "a repair attempt requires an explicit base_branch (the previous result branch); "
                f"it never defaults to {BASE_BRANCH!r}",
            )
        branch = _str("base_branch", self.base_branch)
        if branch == BASE_BRANCH:
            raise _fail(
                "REPAIR_BASE_BRANCH_IS_MAIN",
                f"a repair attempt never checks out {BASE_BRANCH!r}; name the previous result "
                "branch",
            )
        _check_base_branch(branch)
        if not AGENT_BRANCH.fullmatch(branch) or not _RESULT_BRANCH.fullmatch(branch):
            raise _fail(
                "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH",
                f"repair base_branch must match {_RESULT_BRANCH.pattern}",
            )
        # The contract is the parent's sealed contract plus RESOLVE entries, verbatim. The
        # parent's instructions digest must be the one this spec's statement and commands
        # produce, so a repair runs exactly the sealed parent's instructions (plus the fixed
        # REPAIR_SUFFIX) and nothing else.
        if len(inherited) != 1:
            raise _fail(
                "REPAIR_INSTRUCTIONS_MISSING",
                f"a repair contract must carry exactly one inherited {INSTRUCTIONS_PREFIX!r} entry",
            )
        at = inherited[0]
        if self.acceptance_contract[at] != INSTRUCTIONS_PREFIX + sealed_instructions_sha256(self):
            raise _fail(
                "REPAIR_INSTRUCTIONS_MISMATCH",
                "inherited instructions digest does not match this spec's statement and "
                "acceptance_commands (a repair may not change the sealed instructions)",
            )
        tail = self.acceptance_contract[at + 1 :]
        if not tail or not all(c.startswith(RESOLVE_PREFIX) for c in tail):
            raise _fail(
                "REPAIR_CONTRACT_SHAPE",
                f"only {RESOLVE_PREFIX!r} entries may follow the inherited instructions entry, "
                "and at least one must",
            )
        # Mandatory binding to the canonical repair WorkItem: the spec must name the seal that
        # ``materialize_repair`` produced; ``build_work`` refuses unless it reproduces it.
        if self.expected_work_seal is None:
            raise _fail(
                "REPAIR_WORK_SEAL_MISSING",
                "a repair attempt requires expected_work_seal (the materialized repair's seal)",
            )
        if not _SEAL.fullmatch(_str("expected_work_seal", self.expected_work_seal)):
            raise _fail(
                "REPAIR_WORK_SEAL_INVALID", "expected_work_seal must be 64 lowercase hex chars"
            )

    @property
    def execution_id(self) -> str:
        if self.attempt_kind == "repair":
            return f"{self.parent_execution_id}-R{self.attempt - 1}"
        return f"{self.task_id}-E{self.execution_ordinal}"


_REPAIR_ONLY_FIELDS: tuple[str, ...] = (
    "parent_task_id",
    "parent_execution_id",
    "base_branch",
    "expected_work_seal",
)
_ALL_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(PackageSpec))
# Required JSON keys. The repair-only keys are optional in the JSON (absent == null).
_FIELDS: tuple[str, ...] = tuple(n for n in _ALL_FIELDS if n not in _REPAIR_ONLY_FIELDS)
_LIST_FIELDS = frozenset(
    {
        "allowed_paths",
        "forbidden_paths",
        "expected_outputs",
        "acceptance_contract",
        "acceptance_commands",
    }
)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise _fail("SPEC_DUPLICATE_KEY", f"duplicate JSON key {k!r}")
        out[k] = v
    return out


def _reject_constant(token: str) -> Any:
    raise _fail("SPEC_JSON_INVALID", f"non-standard JSON constant {token}")


def load_spec(text: str) -> PackageSpec:
    """Strict JSON -> ``PackageSpec``: exact key set, strict types, no duplicate keys."""
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise _fail("SPEC_JSON_INVALID", str(exc)) from exc
    if not isinstance(raw, dict):
        raise _fail("SPEC_NOT_OBJECT", "spec must be a JSON object")
    unknown = sorted(set(raw) - set(_ALL_FIELDS))
    if unknown:
        raise _fail("SPEC_UNKNOWN_KEY", f"unknown keys {unknown}")
    missing = sorted(set(_FIELDS) - set(raw))
    if missing:
        raise _fail("SPEC_MISSING_KEY", f"missing keys {missing}")
    kw: dict[str, Any] = {}
    for k in _FIELDS:
        v = raw[k]
        if k in _LIST_FIELDS:
            if not isinstance(v, list):
                raise _fail("SPEC_TYPE", f"{k} must be a list of strings")
            v = tuple(v)
        kw[k] = v
    for k in _REPAIR_ONLY_FIELDS:
        kw[k] = raw.get(k)
    return PackageSpec(**kw)


def _canonical_sha256(obj: object) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def spec_sha256(spec: PackageSpec) -> str:
    """sha256 over the canonical JSON of the spec (sorted keys, compact separators).

    Unset repair-only fields are omitted, so an implementation spec hashes exactly as before.
    """
    return _canonical_sha256(
        {k: v for k, v in asdict(spec).items() if not (k in _REPAIR_ONLY_FIELDS and v is None)}
    )


def effective_statement(spec: PackageSpec) -> str:
    """The statement the executor receives: REPAIR_SUFFIX only for attempt_kind == "repair"."""
    return spec.statement + REPAIR_SUFFIX if spec.attempt_kind == "repair" else spec.statement


def instructions_sha256(spec: PackageSpec) -> str:
    """sha256 over canonical JSON of {attempt_kind, effective statement, acceptance_commands}.

    For an implementation spec this is sealed into the work item, so each of them changes
    ``work_seal``. A repair inherits its parent's seal entry: see ``sealed_instructions_sha256``.
    """
    return _canonical_sha256(
        {
            "attempt_kind": spec.attempt_kind,
            "statement": effective_statement(spec),
            "acceptance_commands": list(spec.acceptance_commands),
        }
    )


def sealed_instructions_sha256(spec: PackageSpec) -> str:
    """The instructions digest sealed in the WorkItem's acceptance contract.

    Always the implementation form (unsuffixed statement). A repair WorkItem inherits its
    lineage's implementation contract unchanged, so the same digest stays sealed and a repair
    spec is refused unless its statement and commands reproduce it.
    """
    return _canonical_sha256(
        {
            "attempt_kind": "implementation",
            "statement": spec.statement,
            "acceptance_commands": list(spec.acceptance_commands),
        }
    )


def build_work(spec: PackageSpec) -> WorkItem:
    """Sealed WorkItem.

    Implementation: the instructions digest is appended as the last acceptance-contract entry.
    Repair: the contract is used verbatim (it already carries the inherited digest, validated
    by the spec) and the result must be seal-identical to ``materialize_repair``'s WorkItem:
    any drift in identity, parent, authority, scope, base, ceiling or contract is refused with
    ``REPAIR_WORK_SEAL_MISMATCH`` rather than rendered under a different seal.
    """
    if spec.attempt_kind == "repair":
        contract = spec.acceptance_contract
    else:
        contract = (*spec.acceptance_contract, INSTRUCTIONS_PREFIX + instructions_sha256(spec))
    try:
        work: WorkItem = make_work(
            task_id=spec.task_id,
            execution_id=spec.execution_id,
            lineage_root=spec.lineage_root,
            parent_task_id=spec.parent_task_id,
            repository=spec.repository,
            base_revision=spec.base_revision,
            authority_ref=spec.authority_ref,
            allowed_paths=spec.allowed_paths,
            forbidden_paths=spec.forbidden_paths,
            expected_outputs=spec.expected_outputs,
            acceptance_contract=contract,
            attempt=spec.attempt,
            max_attempts=spec.max_attempts,
        )
    except ContractError as exc:
        raise _fail("WORK_INVALID", str(exc)) from exc
    if spec.attempt_kind == "repair" and work.seal != spec.expected_work_seal:
        raise _fail(
            "REPAIR_WORK_SEAL_MISMATCH",
            "spec does not reproduce the materialized repair WorkItem "
            f"(built seal {work.seal}, expected {spec.expected_work_seal})",
        )
    return work


def repair_spec_from_work(
    work: WorkItem,
    *,
    statement: str,
    acceptance_commands: tuple[str, ...],
    base_branch: str,
    parent_execution_id: str,
    execution_ordinal: int = 1,
) -> PackageSpec:
    """Repair spec derived from a sealed repair WorkItem (no hand transcription). Pure.

    The WorkItem's seal is verified first and becomes ``expected_work_seal``; every other
    rule is enforced by ``PackageSpec`` / ``build_work`` as for a hand-written spec.
    """
    try:
        work.verify_seal()
    except ContractError as exc:
        raise _fail("WORK_INVALID", str(exc)) from exc
    return PackageSpec(
        task_id=work.task_id,
        execution_ordinal=execution_ordinal,
        lineage_root=work.lineage_root,
        repository=work.repository,
        base_revision=work.base_revision,
        authority_ref=work.authority_ref,
        allowed_paths=work.allowed_paths,
        forbidden_paths=work.forbidden_paths,
        expected_outputs=work.expected_outputs,
        acceptance_contract=work.acceptance_contract,
        statement=statement,
        acceptance_commands=acceptance_commands,
        attempt=work.attempt,
        max_attempts=work.max_attempts,
        attempt_kind="repair",
        parent_task_id=work.parent_task_id,
        parent_execution_id=parent_execution_id,
        base_branch=base_branch,
        expected_work_seal=work.seal,
    )


def build_package(spec: PackageSpec) -> dict[str, Any]:
    """Same key structure as ``dev_first_run.build_package`` plus ``attempt_kind`` and
    ``provenance`` (both top-level additions; every shared key keeps its shape).

    ``workflow_inputs`` is exactly ``build_sealed_dispatch_payload``'s output (the payload the
    live adapter dispatches) and always carries ``base_revision``, the sealed base revision the
    execute workflow asserts its checked-out HEAD against.

    A repair package additionally carries ``lineage_root``, ``parent_task_id``,
    ``parent_execution_id`` and a ``checkout`` block, and its ``workflow_inputs.base_branch``
    is the spec's explicit previous-result branch. Repair package bytes are unchanged by the
    move of ``base_revision`` into the canonical builder; implementation package bytes gained
    the input and the matching abort condition.
    """
    w = build_work(spec)
    commands = spec.acceptance_commands
    repair = spec.attempt_kind == "repair"
    base_branch = spec.base_branch if repair and spec.base_branch is not None else BASE_BRANCH
    payload = build_sealed_dispatch_payload(
        w,
        base_branch=base_branch,
        task_statement=effective_statement(spec),
        acceptance_commands=commands,
    )
    if len(payload.inputs["task_prompt"].encode()) > MAX_PROMPT_BYTES:
        raise _fail("PROMPT_TOO_LONG", f"assembled task_prompt exceeds {MAX_PROMPT_BYTES} bytes")
    abort_conditions = [
        f"{base_branch} is not at the sealed base revision before dispatch",
        "run correlation ambiguous (correlation is serialised: one unbound dispatch at a time; "
        "no foreign/manual dispatch of atlas-agent-execute during the run)",
        "result branch moves after ingestion",
        "result touches forbidden or out-of-scope paths",
        "verifier verdict REJECTED/UNESTABLISHED repeatedly or attempt ceiling reached",
    ]
    abort_conditions.insert(
        1,
        "the execute workflow's checked-out HEAD is not the sealed base revision (asserted "
        f"in the workflow from workflow_inputs.{BASE_REVISION_INPUT}; the run fails before "
        "the agent starts)",
    )
    pkg: dict[str, Any] = {
        "package_version": 1,
        "task_id": w.task_id,
        "execution_id": w.execution_id,
        "work_seal": w.seal,
        "repository": w.repository,
        "base_revision": w.base_revision,
        "allowed_paths": list(w.allowed_paths),
        "forbidden_paths": list(w.forbidden_paths),
        "authority_reference": w.authority_ref,
        "workflow": payload.workflow,
        "workflow_ref": payload.ref,
        "workflow_inputs": payload.inputs,
        "workflow_inputs_sha256": payload.sha256(),
        "expected_agent_branch_pattern": AGENT_BRANCH.pattern,
        "acceptance": {
            "contract": list(w.acceptance_contract),
            "commands": list(commands),
            "note": "infra/atlas-runner tests are corroborating evidence only",
        },
        "result_discovery_contract": {
            "run": "single workflow_dispatch run of atlas-agent-execute.yml on main created after "
            "the write-ahead DISPATCH record; ambiguity => refuse",
            "branch": "atlas/agent-<run_id>-<run_attempt>; head sha = result revision; tree via "
            "git commit; merge-base with the sealed base must equal the base; changed paths must "
            "lie within allowed_paths and outside forbidden_paths",
            "evidence_artifact": EVIDENCE_ARTIFACT,
            "ingestion": "ingest_report -> Crosswalk-bound ResultRecord",
        },
        "verification_profile": {
            "profile": "github_hosted",
            "workflow": VERIFY_WORKFLOW,
            "report_artifact": REPORT_ARTIFACT,
            "policy": "PASS iff verifier verdict VERIFIED for the source run AND every REQUIRED "
            "check (control-plane + the 3 quality jobs) is present, completed and success on the "
            "exact result head, and no other check failed (draft evidence PR opened by the "
            "adapter); UNESTABLISHED or missing/in-progress checks => no verdict",
            "required_checks": [
                "control-plane",
                "quality (ubuntu-latest, 3.12, full)",
                "quality (ubuntu-latest, 3.13, compat)",
                "quality (windows-latest, 3.12, windows)",
            ],
        },
        "failure_ceiling": {
            "max_attempts": w.max_attempts,
            "repair_base": "previous result branch",
        },
        "abort_conditions": abort_conditions,
        "rollback": "delete the atlas/agent-* branch and close the draft evidence PR; nothing is "
        "merged and nothing touches main",
        "secrets": {
            "ANTHROPIC_API_KEY": "NOT_ASSERTED (builder never inspects secrets; presence is an "
            "owner statement)"
        },
        "grant_required": GRANT_REQUIRED,
        "attempt_kind": spec.attempt_kind,
        "provenance": {"builder": BUILDER_ID, "spec_sha256": spec_sha256(spec)},
    }
    if repair:
        pkg["lineage_root"] = w.lineage_root
        pkg["parent_task_id"] = w.parent_task_id
        pkg["parent_execution_id"] = spec.parent_execution_id
        pkg["expected_work_seal"] = spec.expected_work_seal
        pkg["checkout"] = {
            "base_branch": base_branch,
            "required_revision": w.base_revision,
            "rule": "before dispatch, resolve base_branch (e.g. git ls-remote) and pass the sha "
            "to dev_package.verify_checkout_ref; any mismatch => refuse. Dispatch "
            "workflow_inputs exactly as recorded: the execute workflow asserts its checked-out "
            "HEAD equals "
            f"workflow_inputs.{BASE_REVISION_INPUT} and fails closed before the agent runs",
        }
    return pkg


def verify_checkout_ref(package: Mapping[str, Any], resolved_sha: object) -> None:
    """Fail closed unless the package's checkout branch resolves to its sealed base revision.

    Pure: no network, subprocess, dispatch or secret access. The caller resolves
    ``package["workflow_inputs"]["base_branch"]`` elsewhere and supplies the sha. A repair
    package must name a previous result branch (never ``main``) and an implementation package
    must name ``main``; the recorded workflow inputs must still hash to
    ``workflow_inputs_sha256``. Every package must also carry
    ``workflow_inputs.base_revision`` equal to the sealed base revision (the value the execute
    workflow asserts against its checked-out HEAD); a package without it is refused. A
    package whose ``provenance.builder`` is not the current ``BUILDER_ID`` (any package rendered
    by an earlier builder version) is refused with ``PACKAGE_BUILDER_UNSUPPORTED``: it must be
    re-rendered from its spec. Raises ``PackageSpecError`` with a stable reason otherwise.

    This checks the package's INTERNAL consistency only. It does not authenticate the package:
    a forged but self-consistent document passes. The trust anchor is the reviewed
    ``package_sha256`` of the rendered package; verify that first, then call this.
    """
    branch, required = _check_package_consistency(package)
    if not isinstance(resolved_sha, str) or not _SHA.fullmatch(resolved_sha):
        raise _fail(
            "CHECKOUT_SHA_INVALID", "resolved sha must be exactly 40 lowercase hex characters"
        )
    if resolved_sha != required:
        raise _fail(
            "CHECKOUT_REF_MISMATCH",
            f"{branch} resolves to {resolved_sha}, not the sealed base revision {required}",
        )


def _check_package_consistency(package: Mapping[str, Any]) -> tuple[str, str]:
    """Internal-consistency checks that need no resolved sha; returns (branch, base revision).

    Shared by ``verify_checkout_ref`` and ``bind_package_to_work``: well-formed checkout fields,
    current builder id, recorded inputs digest, base-branch shape, the sealed ``base_revision``
    input, and the repair/implementation branch rules. Reason codes and raise order are those
    ``verify_checkout_ref`` has always had. A forged but self-consistent document passes.
    """
    inputs = package.get("workflow_inputs")
    required = package.get("base_revision")
    kind = package.get("attempt_kind")
    workflow, ref = package.get("workflow"), package.get("workflow_ref")
    if (
        not isinstance(inputs, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in inputs.items())
        or not isinstance(workflow, str)
        or not isinstance(ref, str)
        or not isinstance(required, str)
        or not _SHA.fullmatch(required)
        or not isinstance(kind, str)
        or kind not in ATTEMPT_KINDS
        or "base_branch" not in inputs
    ):
        raise _fail("CHECKOUT_PACKAGE_INVALID", "package lacks well-formed checkout fields")
    provenance = package.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("builder") != BUILDER_ID:
        raise _fail(
            "PACKAGE_BUILDER_UNSUPPORTED",
            f"package was not rendered by {BUILDER_ID}; re-render it from its spec",
        )
    recorded = DispatchPayload(workflow=workflow, ref=ref, inputs=inputs).sha256()
    if package.get("workflow_inputs_sha256") != recorded:
        raise _fail("CHECKOUT_PACKAGE_INVALID", "workflow_inputs do not match their sha256")
    branch: str = inputs["base_branch"]
    _check_base_branch(branch)
    if inputs.get(BASE_REVISION_INPUT) != required:
        raise _fail(
            "CHECKOUT_PACKAGE_INVALID",
            f"workflow_inputs.{BASE_REVISION_INPUT} is missing or not the sealed base revision",
        )
    if kind == "repair":
        if branch == BASE_BRANCH:
            raise _fail(
                "REPAIR_BASE_BRANCH_IS_MAIN", f"a repair package never checks out {BASE_BRANCH!r}"
            )
        if not AGENT_BRANCH.fullmatch(branch) or not _RESULT_BRANCH.fullmatch(branch):
            raise _fail(
                "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH",
                f"repair base_branch must match {_RESULT_BRANCH.pattern}",
            )
        checkout = package.get("checkout")
        if (
            not isinstance(checkout, dict)
            or checkout.get("base_branch") != branch
            or checkout.get("required_revision") != required
            or inputs.get(BASE_REVISION_INPUT) != required
            or not package.get("parent_task_id")
            or package.get("expected_work_seal") != package.get("work_seal")
        ):
            raise _fail("CHECKOUT_PACKAGE_INVALID", "repair checkout block is inconsistent")
    elif branch != BASE_BRANCH:
        raise _fail(
            "CHECKOUT_PACKAGE_INVALID", f"an implementation package checks out {BASE_BRANCH!r}"
        )
    return branch, required


def package_sha256(rendered: str) -> str:
    return hashlib.sha256(rendered.encode()).hexdigest()


# -- binding a rendered package to its sealed work item (ATLAS-DEVQ-0005) -------------------


@dataclass(frozen=True)
class PackageBinding:
    """What ``bind_package_to_work`` established. A statement of identity, NOT a grant.

    ``package_sha256`` is the caller's expected hash the rendered document was checked against;
    ``payload`` was rebuilt from the sealed work item (never copied from the package) and was
    required to equal the package's recorded workflow, ref and inputs exactly. Holding a
    ``PackageBinding`` permits nothing: it neither issues, consumes nor verifies an owner
    dispatch grant.
    """

    package_sha256: str
    work_seal: str
    attempt_kind: str
    base_branch: str
    payload: DispatchPayload


# Exactly the top-level keys ``build_package`` renders (a test pins both sets against it). A
# bound package may carry no other key and may not lack one: an unknown key could tell a reader
# something the sealed work item does not say.
_PACKAGE_KEYS = frozenset(
    {
        "package_version",
        "task_id",
        "execution_id",
        "work_seal",
        "repository",
        "base_revision",
        "allowed_paths",
        "forbidden_paths",
        "authority_reference",
        "workflow",
        "workflow_ref",
        "workflow_inputs",
        "workflow_inputs_sha256",
        "expected_agent_branch_pattern",
        "acceptance",
        "result_discovery_contract",
        "verification_profile",
        "failure_ceiling",
        "abort_conditions",
        "rollback",
        "secrets",
        "grant_required",
        "attempt_kind",
        "provenance",
    }
)
_REPAIR_PACKAGE_KEYS = frozenset(
    {"lineage_root", "parent_task_id", "parent_execution_id", "expected_work_seal", "checkout"}
)

# A statement placeholder that differs from the first character of the prompt's fixed tail
# (a newline), used only to locate where the statement sits in the assembled prompt.
_STATEMENT_PROBE = "\x00"


def _recover_statement(
    work: WorkItem, *, base_branch: str, commands: tuple[str, ...], prompt: str
) -> str | None:
    """The one statement for which the canonical builder yields ``prompt``, or None.

    The assembled prompt is ``<head><statement><tail>`` where head and tail are fixed strings
    determined by the work item and the commands alone. Both are obtained from the canonical
    builder itself (built once with an empty statement and once with a one-character probe;
    the first differing index is the end of the head), so no prompt layout is duplicated here.
    For a given work item and command tuple the split is unique: at most one statement
    reproduces a prompt, and it is returned only if the prompt has exactly that head and tail.
    """
    empty = build_sealed_dispatch_payload(
        work, base_branch=base_branch, task_statement="", acceptance_commands=commands
    ).inputs["task_prompt"]
    probed = build_sealed_dispatch_payload(
        work,
        base_branch=base_branch,
        task_statement=_STATEMENT_PROBE,
        acceptance_commands=commands,
    ).inputs["task_prompt"]
    at = next((i for i, (a, b) in enumerate(zip(empty, probed, strict=False)) if a != b), None)
    if at is None or probed[at] != _STATEMENT_PROBE or probed[:at] + probed[at + 1 :] != empty:
        return None
    head, tail = empty[:at], empty[at:]
    if (
        len(prompt) < len(head) + len(tail)
        or not prompt.startswith(head)
        or not prompt.endswith(tail)
    ):
        return None
    return prompt[len(head) : len(prompt) - len(tail)]


def bind_package_to_work(
    rendered: str, *, expected_package_sha256: str, work: WorkItem
) -> PackageBinding:
    """Bind a rendered package to one sealed work item, or refuse. Pure.

    No network, ledger, port, subprocess or secret access. Proves, in this order, each failure
    raising ``PackageSpecError`` with a stable reason:

      1. ``BINDING_PACKAGE_SHA_INVALID``  the expected hash is not 64 lowercase hex characters;
      2. ``BINDING_PACKAGE_SHA_MISMATCH`` ``rendered`` is not the document with that hash;
      3. ``BINDING_PACKAGE_UNPARSEABLE``  it is not a strict JSON object;
      4. the package's internal consistency (the reason codes of ``verify_checkout_ref`` that
         need no resolved sha, e.g. ``PACKAGE_BUILDER_UNSUPPORTED``, ``CHECKOUT_PACKAGE_INVALID``);
      5. ``BINDING_WORK_INVALID``         the work item's seal does not verify;
      6. ``BINDING_WORK_SEAL_MISMATCH``   the package names another work seal;
      7. ``BINDING_IDENTITY_MISMATCH``    task, execution, repository or base revision differ;
      8. ``BINDING_KIND_MISMATCH``        the package is a repair iff the work has a parent;
         then ``BINDING_DESCRIPTION_MISMATCH`` if the scope, authority reference, acceptance
         contract, attempt ceiling, ``grant_required`` or (repair) lineage the package states
         differ from the work item;
      9. ``BINDING_INSTRUCTIONS_MISMATCH`` the statement recovered from the recorded prompt,
         with the recorded acceptance commands, does not reproduce the instructions digest
         sealed in the work item's acceptance contract;
      10. ``BINDING_PAYLOAD_MISMATCH``    the payload rebuilt from the sealed work item is not
         exactly the package's workflow, ref and inputs (and their recorded digest).

    The returned payload is the REBUILT one. The expected hash is supplied by the caller: this
    function cannot know whether anyone reviewed that document.

    The package must have exactly the top-level keys the builder renders for its kind (no
    extra, none missing). Not covered: the VALUES of fields that are not derived from the work
    item (for example the verification profile, the result discovery contract,
    ``provenance.spec_sha256``, a repair's ``parent_execution_id``) and keys nested inside
    them are not compared; only the expected hash covers them. For a repair, any well-formed
    result-branch name passes here: whether that branch is the one the ledger knows for the
    sealed base revision, and whether it resolves to that revision, is checked by the caller
    (``FabricAdapter.dispatch_package``, ``verify_checkout_ref``).

    Binding is not a grant. It neither issues, consumes nor verifies an owner dispatch grant;
    ``grant_required`` stays exactly as rendered; a successful binding never permits a dispatch
    and never overrides a classifier or platform denial.
    """
    if not isinstance(expected_package_sha256, str) or not _SEAL.fullmatch(expected_package_sha256):
        raise _fail(
            "BINDING_PACKAGE_SHA_INVALID",
            "expected package sha256 must be exactly 64 lowercase hex characters",
        )
    if not isinstance(rendered, str):
        raise _fail("BINDING_PACKAGE_SHA_MISMATCH", "rendered package must be text")
    try:
        actual = package_sha256(rendered)
    except UnicodeError:
        actual = ""
    if actual != expected_package_sha256:
        raise _fail(
            "BINDING_PACKAGE_SHA_MISMATCH",
            "rendered package does not hash to the expected package sha256",
        )
    try:
        package = json.loads(
            rendered, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
        )
    except (PackageSpecError, ValueError, RecursionError) as exc:
        raise _fail("BINDING_PACKAGE_UNPARSEABLE", str(exc)) from exc
    if not isinstance(package, dict):
        raise _fail("BINDING_PACKAGE_UNPARSEABLE", "package must be a JSON object")
    branch, required = _check_package_consistency(package)
    if not isinstance(work, WorkItem):
        raise _fail("BINDING_WORK_INVALID", "work must be a sealed WorkItem")
    try:
        work.verify_seal()
    except ContractError as exc:
        raise _fail("BINDING_WORK_INVALID", str(exc)) from exc
    if package.get("work_seal") != work.seal:
        raise _fail(
            "BINDING_WORK_SEAL_MISMATCH", "package was rendered for a different sealed work item"
        )
    for key, want in (
        ("task_id", work.task_id),
        ("execution_id", work.execution_id),
        ("repository", work.repository),
        ("base_revision", work.base_revision),
    ):
        if package.get(key) != want:
            raise _fail("BINDING_IDENTITY_MISMATCH", f"package {key} differs from the work item")
    kind: str = package["attempt_kind"]
    if (kind == "repair") != (work.parent_task_id is not None):
        raise _fail(
            "BINDING_KIND_MISMATCH",
            "a repair package requires a work item with a parent, and only such a work item",
        )
    expected_keys = _PACKAGE_KEYS | (_REPAIR_PACKAGE_KEYS if kind == "repair" else frozenset())
    if set(package) != expected_keys:
        odd = sorted(set(package) ^ expected_keys)
        raise _fail(
            "BINDING_DESCRIPTION_MISMATCH",
            f"package top-level keys differ from what the builder renders: {odd}",
        )
    # What a reader of the package is told about the work must be what the seal says: scope,
    # authority reference, contract, attempt ceiling and (repair) lineage are all derived from
    # the work item by ``build_package``, so they are compared, not trusted.
    acceptance = package.get("acceptance")
    ceiling = package.get("failure_ceiling")
    described: list[tuple[str, object, object]] = [
        ("allowed_paths", package.get("allowed_paths"), list(work.allowed_paths)),
        ("forbidden_paths", package.get("forbidden_paths"), list(work.forbidden_paths)),
        ("authority_reference", package.get("authority_reference"), work.authority_ref),
        (
            "acceptance.contract",
            acceptance.get("contract") if isinstance(acceptance, dict) else None,
            list(work.acceptance_contract),
        ),
        (
            "failure_ceiling.max_attempts",
            ceiling.get("max_attempts") if isinstance(ceiling, dict) else None,
            work.max_attempts,
        ),
        ("grant_required", package.get("grant_required"), GRANT_REQUIRED),
    ]
    if kind == "repair":
        described += [
            ("lineage_root", package.get("lineage_root"), work.lineage_root),
            ("parent_task_id", package.get("parent_task_id"), work.parent_task_id),
        ]
    for field_name, stated, sealed_value in described:
        if type(stated) is not type(sealed_value) or stated != sealed_value:
            raise _fail(
                "BINDING_DESCRIPTION_MISMATCH", f"package {field_name} differs from the work item"
            )
    raw_commands = acceptance.get("commands") if isinstance(acceptance, dict) else None
    if not isinstance(raw_commands, list) or not all(isinstance(c, str) for c in raw_commands):
        raise _fail(
            "BINDING_INSTRUCTIONS_MISMATCH", "package acceptance.commands is not a string list"
        )
    commands = tuple(raw_commands)
    sealed = [c for c in work.acceptance_contract if c.startswith(INSTRUCTIONS_PREFIX)]
    if len(sealed) != 1:
        raise _fail(
            "BINDING_INSTRUCTIONS_MISMATCH",
            f"the work item must seal exactly one {INSTRUCTIONS_PREFIX!r} contract entry",
        )
    prompt: str = package["workflow_inputs"].get("task_prompt", "")
    try:
        statement = _recover_statement(work, base_branch=branch, commands=commands, prompt=prompt)
    except ContractError as exc:
        raise _fail("BINDING_PAYLOAD_MISMATCH", str(exc)) from exc
    if statement is None:
        raise _fail(
            "BINDING_INSTRUCTIONS_MISMATCH",
            "the recorded task_prompt is not the canonical prompt for this work item and "
            "these acceptance commands",
        )
    # The sealed digest is always the implementation form over the UNSUFFIXED statement
    # (``sealed_instructions_sha256``); a repair prompt carries exactly one REPAIR_SUFFIX.
    if kind == "repair":
        if not statement.endswith(REPAIR_SUFFIX):
            raise _fail(
                "BINDING_INSTRUCTIONS_MISMATCH",
                "a repair prompt must end its statement with the fixed repair suffix",
            )
        sealed_statement = statement[: -len(REPAIR_SUFFIX)]
    else:
        sealed_statement = statement
    digest = _canonical_sha256(
        {
            "attempt_kind": "implementation",
            "statement": sealed_statement,
            "acceptance_commands": list(commands),
        }
    )
    if sealed[0] != INSTRUCTIONS_PREFIX + digest:
        raise _fail(
            "BINDING_INSTRUCTIONS_MISMATCH",
            "the package's statement and acceptance commands do not reproduce the instructions "
            "digest sealed in the work item",
        )
    try:
        payload = build_sealed_dispatch_payload(
            work, base_branch=branch, task_statement=statement, acceptance_commands=commands
        )
    except ContractError as exc:
        raise _fail("BINDING_PAYLOAD_MISMATCH", str(exc)) from exc
    if (
        (payload.workflow, payload.ref, payload.inputs)
        != (package["workflow"], package["workflow_ref"], package["workflow_inputs"])
        or payload.sha256() != package.get("workflow_inputs_sha256")
        or required != work.base_revision
    ):
        raise _fail(
            "BINDING_PAYLOAD_MISMATCH",
            "the payload rebuilt from the sealed work item is not the package's recorded "
            "workflow, ref and inputs",
        )
    return PackageBinding(
        package_sha256=expected_package_sha256,
        work_seal=work.seal,
        attempt_kind=kind,
        base_branch=branch,
        payload=payload,
    )
