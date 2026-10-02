"""Generic, deterministic DEVQ package builder (frontier F1, steps 4-5).

``dev_first_run`` builds exactly one hard-wired package (ATLAS-DEVQ-0001). This module builds the
same package structure for ANY task from an explicit, strictly validated ``PackageSpec`` (JSON).

Properties:
  * Deterministic: same spec content (key order irrelevant) -> byte-identical rendered package.
  * Identity is derived, never supplied: ``execution_id = f"{task_id}-E{execution_ordinal}"``.
  * Sealed instructions: a digest of the statement and acceptance commands is bound into the
    WorkItem's acceptance contract, so changing either changes ``work_seal``.
  * Fail-closed: every rule violation raises ``PackageSpecError`` with a stable ``reason`` code.
  * Acceptance commands must be plain, read-only ``pytest``/``ruff``/``mypy`` checks (the
    executor's tool allowlist matches on the first token; ``python -m``/env prefixes/shell
    syntax/write-capable flags would not run there or would widen what runs).
  * The forbidden-path HARD FLOOR is always required; allowed scope may not overlap it
    (case-insensitively, segment-wise).
  * Secrets are never inspected and never asserted present.

Nothing here dispatches, ingests, merges or reads any secret; it only produces a document.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import unicodedata
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
    EVIDENCE_ARTIFACT,
    REPORT_ARTIFACT,
    VERIFY_WORKFLOW,
    build_dispatch_payload,
)
from project_atlas.orchestration.autonomy.dev_first_run import render_package

__all__ = [
    "BUILDER_ID",
    "FORBIDDEN_FLOOR",
    "PackageSpec",
    "PackageSpecError",
    "build_package",
    "build_work",
    "instructions_sha256",
    "load_spec",
    "package_sha256",
    "render_package",
    "spec_sha256",
]

BUILDER_ID = "dev_package/1"
BASE_BRANCH = "main"
INSTRUCTIONS_PREFIX = "instructions_sha256="

# Paths no generated package may ever leave writable. Compared case-insensitively after
# stripping a trailing "/". The whole autonomy control-plane package is on the floor, so any
# spec whose scope touches it is ineligible for this builder (intended).
FORBIDDEN_FLOOR: tuple[str, ...] = (
    ".github/",
    "autonomy/",
    "infra/atlas-runner/controller",
    "src/project_atlas/orchestration/autonomy/trust.py",
    "src/project_atlas/orchestration/autonomy/",
    ".claude/",
    "pyproject.toml",
    "conftest.py",
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
MAX_ITEM_CHARS = 500
MAX_PROMPT_BYTES = 32000
MAX_ORDINAL = 9999
MAX_ATTEMPTS_CEILING = 10

REPAIR_SUFFIX = (
    " This is a REPAIR attempt: resolve every RESOLVE:<finding_id> listed "
    "in the acceptance contract on top of the previous result branch."
)

# Line prefixes that build_dispatch_payload uses for its own header/scope/acceptance lines (and
# the workflow wrapper uses around the prompt). A statement line may not imitate them.
RESERVED_STATEMENT_PREFIXES: tuple[str, ...] = (
    "atlas dev-loop task",
    "repository ",
    "only modify",
    "never modify",
    "acceptance",
    "-",
    "new tests must",
    "bounded task",
    "constraints:",
)

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AUTHORITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
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


def _str_tuple(name: str, v: object) -> tuple[str, ...]:
    if not isinstance(v, tuple) or not all(isinstance(x, str) for x in v):
        raise _fail("SPEC_TYPE", f"{name} must be a list of strings")
    if len(v) > MAX_LIST_ITEMS:
        raise _fail("LIST_TOO_LONG", f"{name} has more than {MAX_LIST_ITEMS} entries")
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
    if not p.isascii():
        raise _fail("PATH_INVALID", f"{name} entry {p!r} is not ASCII")
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


def _check_statement(statement: str) -> None:
    if not statement.strip():
        raise _fail("STATEMENT_EMPTY", "statement is empty")
    if len(statement.encode()) > MAX_STATEMENT_BYTES:
        raise _fail("STATEMENT_TOO_LONG", f"statement exceeds {MAX_STATEMENT_BYTES} UTF-8 bytes")
    if _bad_chars(statement, allow_newline=True):
        raise _fail("STATEMENT_INVALID", "statement contains control/format/line-separator chars")
    for line in statement.split("\n"):
        head = line.strip().casefold()
        if any(head.startswith(p) for p in RESERVED_STATEMENT_PREFIXES):
            raise _fail(
                "STATEMENT_RESERVED_PREFIX",
                f"statement line imitates a reserved prompt line: {line.strip()[:60]!r}",
            )


@dataclass(frozen=True)
class PackageSpec:
    """Explicit input for one package. ``execution_id`` is derived, never supplied."""

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
        forbidden = _str_tuple("forbidden_paths", self.forbidden_paths)
        for name in ("expected_outputs", "acceptance_contract"):
            if not _str_tuple(name, getattr(self, name)):
                raise _fail("LIST_EMPTY", f"{name} must not be empty")
        if any(c.startswith(INSTRUCTIONS_PREFIX) for c in self.acceptance_contract):
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

    @property
    def execution_id(self) -> str:
        return f"{self.task_id}-E{self.execution_ordinal}"


_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(PackageSpec))
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
    unknown = sorted(set(raw) - set(_FIELDS))
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
    return PackageSpec(**kw)


def _canonical_sha256(obj: object) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def spec_sha256(spec: PackageSpec) -> str:
    """sha256 over the canonical JSON of the spec (sorted keys, compact separators)."""
    return _canonical_sha256(asdict(spec))


def instructions_sha256(spec: PackageSpec) -> str:
    """sha256 over the canonical JSON of {statement, acceptance_commands} (sealed into work)."""
    return _canonical_sha256(
        {"statement": spec.statement, "acceptance_commands": list(spec.acceptance_commands)}
    )


def build_work(spec: PackageSpec) -> WorkItem:
    """Sealed WorkItem; the instructions digest is the last acceptance-contract entry."""
    contract = (*spec.acceptance_contract, INSTRUCTIONS_PREFIX + instructions_sha256(spec))
    try:
        return make_work(
            task_id=spec.task_id,
            execution_id=spec.execution_id,
            lineage_root=spec.lineage_root,
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


def _statement(spec: PackageSpec) -> str:
    return spec.statement + REPAIR_SUFFIX if spec.attempt > 1 else spec.statement


def build_package(spec: PackageSpec) -> dict[str, Any]:
    """Same key structure as ``dev_first_run.build_package`` plus ``provenance``."""
    w = build_work(spec)
    commands = spec.acceptance_commands
    payload = build_dispatch_payload(
        w, base_branch=BASE_BRANCH, task_statement=_statement(spec), acceptance_commands=commands
    )
    if len(payload.inputs["task_prompt"].encode()) > MAX_PROMPT_BYTES:
        raise _fail("PROMPT_TOO_LONG", f"assembled task_prompt exceeds {MAX_PROMPT_BYTES} bytes")
    return {
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
        "abort_conditions": [
            "main is not at the sealed base revision before dispatch",
            "run correlation ambiguous (correlation is serialised: one unbound dispatch at a time; "
            "no foreign/manual dispatch of atlas-agent-execute during the run)",
            "result branch moves after ingestion",
            "result touches forbidden or out-of-scope paths",
            "verifier verdict REJECTED/UNESTABLISHED repeatedly or attempt ceiling reached",
        ],
        "rollback": "delete the atlas/agent-* branch and close the draft evidence PR; nothing is "
        "merged and nothing touches main",
        "secrets": {
            "ANTHROPIC_API_KEY": "NOT_ASSERTED (builder never inspects secrets; presence is an "
            "owner statement)"
        },
        "grant_required": "ONE_WORKFLOW_DISPATCH_GRANT",
        "provenance": {"builder": BUILDER_ID, "spec_sha256": spec_sha256(spec)},
    }


def package_sha256(rendered: str) -> str:
    return hashlib.sha256(rendered.encode()).hexdigest()
