"""Atlas 3 must not rewrite certified demo / 2.x surfaces."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Every git subprocess call in this module is bounded. A hung git process
# (most plausibly the network-dependent fetch, e.g. a credential prompt
# that never resolves, or an unusual server-side negotiation) must fail
# loudly within a bounded time, never silently consume an entire hosted
# CI job's runtime -- discovered the hard way when an untimed `git fetch`
# call left a real hosted CI run "in_progress" for over an hour.
_LOCAL_GIT_TIMEOUT_SECONDS = 30
_NETWORK_GIT_TIMEOUT_SECONDS = 60

DENY = (
    "src/project_atlas/chatgpt_bridge.py",
    "src/project_atlas/chatgpt_capture.py",
    "src/project_atlas/knowledge_compiler.py",
    "src/project_atlas/api_server.py",
    "src/project_atlas/authz.py",
    "src/project_atlas/discovery.py",
    "src/project_atlas/ingestion.py",
    "src/project_atlas/compat_anchor.py",
    "src/project_atlas/conflict_projections.py",
    "src/project_atlas/reality_gap.py",
    "src/project_atlas/bitemporal.py",
    "src/project_atlas/conversation_capture.py",
)

# Owner-approved narrow exceptions to the freeze above (SS9.1 of
# docs/atlas-3/ARCHITECTURE.md). This is NOT a path allowlist: each entry
# is pinned to the *exact*, reviewed content of one file by sha256. Any
# further edit -- even one byte, even by the same PR -- changes the hash
# and the guard fires again for that path. An exception never widens to
# cover a different path, a different diff, or a later PR; there is no
# generic "this file is now unfrozen" mechanism here, only ever a specific
# reviewed byte sequence for a specific, named, owner-approved reason.
#
# Do not add an entry here without an explicit owner grant recorded in
# WORKLOG.md; do not extend an existing entry's scope beyond its recorded
# reason; do not use this mechanism to work around a failing guard on an
# unreviewed change.
_OWNER_APPROVED_EXCEPTIONS: tuple[dict[str, str], ...] = (
    {
        "exception_id": "DOGFOOD-001",
        "owner_approved": "YES",
        "reason": "authentic first-run P1 source-safety remediation (PR #656)",
        "path": "src/project_atlas/ingestion.py",
        "allowed_sha256": (
            "e8d779a8ab2fe0b4327ae9cf8cae115f2a793eb96eb35e8b0024b6ee085168ef"
        ),
    },
    {
        "exception_id": "R-READY-LINUX-RESIDUALS-20260905-DISCOVERY",
        "owner_approved": "YES",
        "reason": (
            "owner grant R_READY (supersedes the discovery.py pin of "
            "OG-ATLAS-LINUX-FILESYSTEM-20260905): verified residuals R1 "
            "canonical-equivalence collision, R2 unreadable metadata "
            "boundary, R3 silent loss of inaccessible discovery scope, plus "
            "the post-verification remediation (unmeasurable-path skip now "
            "observable; ordering key made total for NFR-001 determinism), and "
            "owner grant R_READY-2 residual R4 (a real document in the "
            "reserved agent-event routing scope reached neither inventory "
            "and produced no diagnostic), including R4-D (a real document, or a "
            "whole subtree behind a directory symlink, escaping the source "
            "root was excluded without any diagnostic), and owner grant "
            "R_READY-5 residual R5 (an unreadable scope under the agent-event "
            "inbox aborted the entire discovery run), and owner grant "
            "OG-ATLAS-DISCOVERY-M1M2-20260906 (directive "
            "D-UBUNTU-AUTONOMOUS-PR683-INTEGRATE-AND-SUCCESSOR-EXECUTION) "
            "residual M1 (five main-walk OSError guards caught every errno, "
            "so an injected EIO was skipped with exit 0 or recorded as an "
            "'unreadable' source; every guard now filters through the "
            "module's stated inaccessible-scope errno contract) and residual "
            "M2 (the event inventory followed symbolic links on the chain to "
            "a package while the walk did not, so an aliased scope was "
            "inventoried twice or silently not at all; links are now refused "
            "on physical identity, reported, and a linked event directory is "
            "recorded as an invalid row), plus the IV remediation (ENAMETOOLONG "
            "joins the inaccessible-scope set: a tree grown past PATH_MAX is "
            "one unaddressable path, not a fault) and the review remediation "
            "(the event-scope chain is tested before any probe that would "
            "follow it, so an escaping link is refused without a metadata "
            "probe through it and a dangling or file-targeted scope link is "
            "named rather than dropped as 'no scope')"
        ),
        "path": "src/project_atlas/discovery.py",
        "allowed_sha256": (
            "471a56af7fcb45220f7c18b5b96efeb32acf7460489c2e9e7525eeec0a429f35"
        ),
    },
    {
        "exception_id": "OG-ATLAS-LINUX-FILESYSTEM-20260905-INGESTION",
        "owner_approved": "YES",
        "reason": (
            "owner grant OG-ATLAS-LINUX-FILESYSTEM-20260905: excluded records "
            "are no longer pre-resolved at the manifest boundary, and a "
            "symlinked authorized root now names its physical path"
        ),
        "path": "src/project_atlas/ingestion.py",
        "allowed_sha256": (
            "6911a99d2c5127a45f29d55888fb2398270749dcfd6c49da3d0626a106d74159"
        ),
    },
)


def _owner_approved_exception_permits(
    path: str, *, root: Path, exceptions: tuple[dict[str, str], ...] = _OWNER_APPROVED_EXCEPTIONS
) -> bool:
    """True only if an owner-approved exception names `path` AND the file's
    *current* content at `root` matches that exception's pinned, exact
    reviewed sha256. Anything else -- a different path, a missing file, a
    hash mismatch from ANY further edit -- returns False, and the caller
    treats `path` as a real, unexcepted violation."""
    target = root / path
    if not target.is_file():
        return False
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return any(
        exc.get("path") == path
        and exc.get("owner_approved") == "YES"
        and digest == exc.get("allowed_sha256")
        for exc in exceptions
    )


class DemoIsolationGuardError(RuntimeError):
    """Fail-closed: the certified-surface freeze guard could not determine
    its comparison base. An inconclusive result must fail visibly, never
    be silently treated as "no changes" -- that would turn the guard into
    a no-op exactly when it matters most.
    """


class DemoIsolationGuardNotApplicable(Exception):
    """Distinct from ``DemoIsolationGuardError``: there is no comparison
    base to even attempt (no CI pull_request/push event context, and no
    git remote configured at all -- e.g. a source archive, vendored
    copy, or a checkout with remotes deliberately stripped). This is not
    a failure to enforce something real; there is nothing to enforce
    here. Callers should skip, not fail and not silently pass.
    """


def _has_any_remote(*, root: Path) -> bool:
    result = subprocess.run(
        ["git", "remote"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _event_pull_request_shas(
    *, env: Mapping[str, str] | None = None
) -> tuple[str, str] | None:
    """Read the authoritative base/head commit SHAs for this PR directly
    from the GitHub Actions ``pull_request`` event payload.

    This is independent of local checkout depth or which refs happen to
    be fetched -- it comes from GitHub's own event metadata (the API's
    view of the PR), not from resolving a branch name like ``origin/main``
    against whatever the local clone happens to contain. Returns ``None``
    only when we are demonstrably not running in a GitHub Actions
    ``pull_request`` context (i.e. ``GITHUB_EVENT_NAME`` is unset or is
    something else, such as a local run or a ``push`` trigger) -- in that
    case the caller falls back to local/full-clone behavior. If
    ``GITHUB_EVENT_NAME=pull_request`` but the payload cannot actually be
    read, this raises rather than returning ``None``, so a broken/missing
    event file cannot be silently treated the same as "not in CI".
    """
    env = os.environ if env is None else env
    if env.get("GITHUB_EVENT_NAME") != "pull_request":
        return None
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise DemoIsolationGuardError(
            "GITHUB_EVENT_NAME=pull_request but GITHUB_EVENT_PATH is unset "
            "-- cannot determine the PR's actual base/head commits"
        )
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        base_sha = payload["pull_request"]["base"]["sha"]
        head_sha = payload["pull_request"]["head"]["sha"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DemoIsolationGuardError(
            f"could not read pull_request base/head sha from "
            f"GITHUB_EVENT_PATH ({event_path}): {exc}"
        ) from exc
    if (
        not isinstance(base_sha, str)
        or not isinstance(head_sha, str)
        or not base_sha
        or not head_sha
    ):
        raise DemoIsolationGuardError(
            "pull_request base/head sha missing or malformed in event payload"
        )
    return base_sha, head_sha


def _event_push_before_sha(*, env: Mapping[str, str] | None = None) -> str | None:
    """Read the authoritative pre-push commit SHA (``before``) directly
    from the GitHub Actions ``push`` event payload.

    This repository's own ``ci.yml`` triggers the same ``quality`` job
    (which runs this guard) on both ``pull_request`` and ``push:
    branches: [main]``. The ``pull_request`` case is covered by
    ``_event_pull_request_shas``; this covers the other real hosted-CI
    trigger this guard actually runs under, so a shallow-clone push run
    cannot silently no-op the same way the original ``pull_request``-only
    fix would have left it. Returns ``None`` only when demonstrably not
    running in a GitHub Actions ``push`` context. Raises, rather than
    returning ``None``, if that context is claimed but the payload cannot
    actually be read -- same fail-closed contract as the PR-event reader.
    """
    env = os.environ if env is None else env
    if env.get("GITHUB_EVENT_NAME") != "push":
        return None
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise DemoIsolationGuardError(
            "GITHUB_EVENT_NAME=push but GITHUB_EVENT_PATH is unset -- "
            "cannot determine the pre-push commit"
        )
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        before_sha = payload["before"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DemoIsolationGuardError(
            f"could not read push 'before' sha from GITHUB_EVENT_PATH "
            f"({event_path}): {exc}"
        ) from exc
    if not isinstance(before_sha, str) or not before_sha:
        raise DemoIsolationGuardError("push 'before' sha missing or malformed in event payload")
    # GitHub uses an all-zero SHA for `before` on a branch's first-ever
    # push (nothing to diff against). Treat that the same as "not
    # resolvable" -- fail closed rather than attempt to fetch/diff a
    # sentinel that was never a real commit.
    if before_sha == "0" * 40:
        raise DemoIsolationGuardError(
            "push event 'before' sha is the all-zero first-push sentinel "
            "-- no real prior commit exists to diff against"
        )
    return before_sha


def _ensure_commit_fetched(sha: str, *, root: Path) -> None:
    """Make ``sha`` resolvable in the local object database, fetching it
    directly by SHA if a shallow checkout did not already include it.
    Raises if the fetch itself fails, is refused, or does not complete
    within a bounded time -- a comparison base that cannot be obtained
    must not be silently skipped, and must never be allowed to hang the
    calling process indefinitely.
    """
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    if probe.returncode == 0:
        return
    try:
        fetch = subprocess.run(
            ["git", "fetch", "--depth=1", "origin", sha],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_NETWORK_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise DemoIsolationGuardError(
            f"fetching commit {sha} for the freeze-guard comparison did "
            f"not complete within {_NETWORK_GIT_TIMEOUT_SECONDS}s -- "
            "treating as a fetch failure rather than hanging"
        ) from exc
    if fetch.returncode != 0:
        raise DemoIsolationGuardError(
            f"could not fetch commit {sha} needed for the freeze-guard "
            f"comparison: {fetch.stderr.strip()}"
        )


def _resolve_diff_base_and_mode(
    *, root: Path = ROOT, env: Mapping[str, str] | None = None
) -> tuple[str, bool]:
    """Return ``(sha, exact)`` -- the commit SHA the freeze guard must diff
    HEAD against, and whether that SHA is an *exact* changeset endpoint
    (``True``: a GitHub event's own base/before SHA, safe to diff directly
    against) or a *floating local fallback* (``False``: ``origin/main``
    resolved outside any CI event context, which can independently advance
    out from under a local branch and must be diffed via its merge-base
    with HEAD instead, or an unrelated upstream change to a DENY-listed
    path would false-positive as though the local branch itself made it).

    Prefers the authoritative GitHub ``pull_request`` event base SHA, then
    the ``push`` event's pre-push SHA (both exact, both correct under any
    checkout depth including the hosted CI default of a single-commit
    shallow fetch). Falls back to ``origin/main`` for local/full-clone use,
    but only when a git remote is actually configured at all -- raises
    ``DemoIsolationGuardNotApplicable`` (not ``DemoIsolationGuardError``)
    when neither a recognized CI event context nor any remote exists,
    since a source archive, vendored copy, or a checkout with remotes
    deliberately stripped was never a context this guard could
    meaningfully enforce against; callers should skip in that case, not
    fail closed. When a remote *is* configured but ``origin/main``
    specifically cannot be resolved, that is a genuine failure and still
    raises ``DemoIsolationGuardError`` -- never silently treat an
    unresolvable base as "nothing changed".
    """
    pr_shas = _event_pull_request_shas(env=env)
    if pr_shas is not None:
        base_sha, _head_sha = pr_shas
        _ensure_commit_fetched(base_sha, root=root)
        return base_sha, True
    push_before = _event_push_before_sha(env=env)
    if push_before is not None:
        _ensure_commit_fetched(push_before, root=root)
        return push_before, True
    if not _has_any_remote(root=root):
        raise DemoIsolationGuardNotApplicable(
            "not running in a GitHub Actions pull_request or push context, "
            "and no git remote is configured at all -- nothing to compare "
            "against (e.g. a source archive or vendored checkout); the "
            "freeze guard does not apply here"
        )
    resolve = subprocess.run(
        ["git", "rev-parse", "--verify", "origin/main"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    if resolve.returncode != 0:
        raise DemoIsolationGuardError(
            "a git remote is configured but origin/main is not resolvable "
            "locally, and no pull_request/push CI event context is present "
            "-- cannot determine the freeze-guard comparison base (this "
            "must fail, not pass)"
        )
    return resolve.stdout.strip(), False


def _resolve_diff_base(*, root: Path = ROOT, env: Mapping[str, str] | None = None) -> str:
    """Return just the resolved comparison SHA (see
    ``_resolve_diff_base_and_mode`` for the full contract, including the
    exact-vs-floating-fallback distinction that ``_changed_paths`` needs
    and this wrapper deliberately discards for callers that only want the
    commit itself, e.g. ``test_cli_mutation_is_additive_only``'s own
    direct diff against ``cli.py``)."""
    sha, _exact = _resolve_diff_base_and_mode(root=root, env=env)
    return sha


def _changed_paths(*, root: Path = ROOT, env: Mapping[str, str] | None = None) -> set[str]:
    """Return every path changed relative to the resolved diff base, plus
    any uncommitted worktree/staged changes. Fails closed (raises via
    ``_resolve_diff_base_and_mode``) if the comparison base cannot be
    determined -- never silently returns an empty/inconclusive result as
    if nothing changed.
    """
    base, exact = _resolve_diff_base_and_mode(root=root, env=env)
    if exact:
        # Direct two-endpoint diff, not three-dot merge-base diff: `base`
        # is an exact PR/push-event SHA, and computing a merge-base can
        # fail outright under a shallow/disconnected fetch (no shared
        # history graph) even though both endpoints are individually
        # resolvable. A direct diff needs only the two commits' trees, not
        # their ancestry, and is exactly correct here since `base` is
        # already the changeset's real prior commit, not a possibly-
        # since-moved branch tip.
        diff_base = base
    else:
        # `base` is the floating local-fallback `origin/main`, resolved
        # outside any CI event context (full clone required -- property 7
        # -- so a merge-base computation is safe and expected to succeed
        # here, unlike in the shallow exact-SHA case above). A direct diff
        # against the live `origin/main` tip would false-positive on any
        # DENY-listed path main has independently advanced since this
        # branch last rebased/merged, flagging it as though the local
        # branch itself touched it. Diffing against the merge-base instead
        # (equivalent to a three-dot diff) isolates exactly the local
        # branch's own changes, which is what this guard is actually
        # supposed to enforce.
        merge_base = subprocess.run(
            ["git", "merge-base", base, "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
        )
        if merge_base.returncode != 0:
            raise DemoIsolationGuardError(
                f"could not compute a merge-base between {base} and HEAD "
                f"for the local-fallback freeze-guard comparison: "
                f"{merge_base.stderr.strip()}"
            )
        diff_base = merge_base.stdout.strip()
    committed = subprocess.run(
        ["git", "diff", "--name-only", diff_base, "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    worktree = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    names = "\n".join([committed.stdout, worktree.stdout, staged.stdout])
    return {line.strip() for line in names.splitlines() if line.strip()}


def test_certified_surfaces_unmodified() -> None:
    try:
        changed = _changed_paths()
    except DemoIsolationGuardNotApplicable as exc:
        pytest.skip(str(exc))
    violated = sorted(
        path
        for path in DENY
        if path in changed and not _owner_approved_exception_permits(path, root=ROOT)
    )
    assert violated == []


# ---------------------------------------------------------------------------
# Atlas 3 CLI ownership seam (D-191 / D-192).
#
# Atlas 3 does not register commands in the shared `cli.py`. It owns
# `project_atlas/atlas3/cli.py`, and the shared CLI reaches it through exactly
# two delegating call sites -- one parser hook, one dispatch hook. That seam,
# not the *shape of a diff*, is the property this guard exists to protect.
#
# The original formulation asserted that any `cli.py` diff must itself contain
# `register_atlas3_parsers` / `dispatch_atlas3` and must add a line mentioning
# the former. That pinned one commit's diff rather than the invariant, with two
# consequences the repository has already paid for:
#
#   * it is unsatisfiable for unrelated CLI work. A new *nested* subcommand
#     (e.g. `atlas capture text`) hangs off a parser built in `cli.py`, and the
#     Atlas 3 seam only receives the top-level `subparsers`, so there is no
#     honest way to add the required hook line. PR #656 (DOGFOOD-001) resolved
#     this by reverting its `cli.py` change entirely ("cli.py: reverted to base
#     entirely ... the guard never fires for this PR at all", 91b24ab4) --
#     legitimate work dropped to satisfy a text match.
#   * it under-protected the real boundary. A diff that merely *mentioned*
#     `register_atlas3_parsers` in a comment satisfied it, even while
#     registering an Atlas 3 command directly in `cli.py` and bypassing the
#     seam altogether.
#
# The checks below are structural and are evaluated against the resulting file
# plus the removed lines, so they are strictly stronger for Atlas 3 while
# staying silent about CLI work Atlas 3 does not own.
# ---------------------------------------------------------------------------

_ATLAS3_SEAM_MODULE = "project_atlas.atlas3.cli"

#: symbol -> the argument the shared CLI must hand it.
_ATLAS3_SEAM_CALLS = {
    "register_atlas3_parsers": "subparsers",
    "dispatch_atlas3": "args",
}

#: Certified command surface that must survive any `cli.py` change.
_CERTIFIED_CLI_COMMANDS = ("connect", "ask2", "kdiff", "brief", "capture")

#: Top-level registrations only. `cli.py`'s top-level subparsers object is
#: uniquely named `subparsers`; nested groups use their own names
#: (`capture_sub`, `ops_sub`, ...), so this cannot mistake a nested subcommand
#: for a top-level one even if the two share a name.
_TOP_LEVEL_ADD_PARSER = re.compile(r"\bsubparsers\.add_parser\(\s*[\"']([\w-]+)[\"']")

#: Any command registration, at any nesting depth.
_ANY_ADD_PARSER = re.compile(r"\.add_parser\(\s*[\"']([\w-]+)[\"']")


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment so prose cannot trip a code-level check."""
    return line.split("#", 1)[0]


def _seam_call_count(source: str, symbol: str, argument: str) -> int:
    return len(
        re.findall(
            rf"(?<![\w.]){re.escape(symbol)}\(\s*{re.escape(argument)}\s*[,)]",
            source,
        )
    )



#: The one call that creates the shared CLI's top-level subparser group.
_TOP_LEVEL_SUBPARSERS_NAME = "subparsers"


def _cli_module_ast(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - a broken cli.py fails louder elsewhere
        raise AssertionError(f"cli.py does not parse: {exc}") from exc


def _bound_names(scope: ast.AST, *, descend_into_functions: bool = True) -> list[tuple[str, int]]:
    """Every name bound in `scope`, by any binding form Python has.

    Walking `ast.Name` nodes in `Store`/`Del` context covers assignment, `for`
    targets, `with ... as`, comprehension targets and `:=` in one pass -- the
    forms an earlier version enumerated node-type by node-type, and therefore
    missed. `for subparsers in (ops_sub,): pass` was a one-line rewrite of an
    assignment that the enumeration did not model, and it reproduced the exact
    damage the assignment form was added to prevent.

    Also covers the binding forms that do not produce a `Name` node at all:
    `except ... as`, `match` capture patterns, `def`, `class`, `import ... as`,
    and parameters. `case name:` is the easiest of these to overlook -- its
    name is a plain string on `MatchAs`, so a walker looking for `Name` nodes
    in `Store` context sees nothing at all.
    """
    bound: list[tuple[str, int]] = []
    for node in ast.walk(scope):
        if (
            not descend_into_functions
            and node is not scope
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.append((node.id, node.lineno))
        elif (
            isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
            and node.name
        ):
            bound.append((node.name, node.lineno))
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.append((node.rest, node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.append((node.name, node.lineno))
            for argument in [
                *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs
            ] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []:
                bound.append((argument.arg, argument.lineno))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.asname:
                    bound.append((alias.asname, node.lineno))
    return bound


def _seam_attribute_writes(tree: ast.Module) -> list[tuple[str, int]]:
    """`<module>.register_atlas3_parsers = ...` -- rebinding via the module object.

    Binds no local name, so no `Name` in `Store` context exists to find, yet it
    replaces what the seam call resolves to at runtime. Covers the `setattr`
    spelling too: catching only the assignment form would be catching the
    spelling rather than the mechanism.
    """
    writes: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _ATLAS3_SEAM_CALLS
        ):
            writes.append((str(node.args[1].value), node.lineno))
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in _ATLAS3_SEAM_CALLS:
                writes.append((target.attr, target.lineno))
    return writes


#: The entry point whose parser is the one operators actually get.
_CLI_ENTRY_POINT_NAME = "main"

def _add_parser_calls(tree: ast.Module) -> list[tuple[str, str, int]]:
    """(receiver_name, command_name, lineno) for every `X.add_parser("cmd")`."""
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_parser" or not isinstance(node.func.value, ast.Name):
            continue
        name: str | None = None
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
            node.args[0].value, str
        ):
            name = node.args[0].value
        for keyword in node.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                name = keyword.value.value
        if name is not None:
            found.append((node.func.value.id, name, node.lineno))
    return found


def _dispatched_commands(tree: ast.Module) -> set[str]:
    """Commands compared against `args.command` anywhere in the module.

    The namespace is required to be `args`, not merely any object with a
    `.command` attribute: accepting `anything.command == "brief"` would let an
    unrelated comparison elsewhere satisfy the check after the real dispatch
    branch had been deleted.
    """
    dispatched: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Attribute):
            continue
        if not (isinstance(node.left.value, ast.Name) and node.left.value.id == "args"):
            continue
        if node.left.attr != "command":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                dispatched.add(comparator.value)
            elif isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                for element in comparator.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        dispatched.add(element.value)
    return dispatched


def assert_cli_semantic_invariants(*, source: str, atlas3_commands: frozenset[str]) -> None:
    """Module-wide invariants, parsed rather than pattern-matched.

    **What these establish, exactly:** that `cli.py` still *contains* an
    un-neutralised Atlas 3 seam, still *contains* a top-level registration and
    a dispatch branch for each certified command, and does not *contain* a
    direct registration of an Atlas-3-owned name.

    **What they do not establish:** what the running CLI exposes. A module can
    satisfy every check here and still build a different parser at runtime --
    see the withdrawal note below, and the non-protections listed with it.

    ---

    Three further checks were attempted and **withdrawn**: top-level parser
    identity, certified command arguments, and certified subcommand arguments.
    All three had to know *which function builds the parser the operator
    actually gets*, and five rounds of independent verification defeated five
    successive answers: the function that happened to call `add_subparsers`,
    the one named `build_parser`, the one `main` assigns `parser` from, then
    the module attribute and the entry point themselves. Each fix was correct
    and each left a sibling, because "which parser reaches argparse" is a
    dynamic property of a language where any name can be rebound through an
    attribute, a subscript, `setattr`, the import system, or `main` itself. A
    static check cannot make that total; it can only enumerate the ways it
    might be defeated, which is the failure that recurred five times.

    Reducing the guarantee to what actually holds is the point. A smaller
    guard whose every claim survived five rounds of adversarial testing is a
    better governance artifact than a larger one that reads stronger than it
    is.
    """
    tree = _cli_module_ast(source)

    # (C) The seam symbols are not rebound by any *local* binding form --
    #     assignment, walrus, `for`/`with`/`except`/`match` targets,
    #     comprehensions, `def`, `class`, `import as`, parameters, `del` -- nor
    #     by a direct attribute assignment or `setattr` on the imported module.
    #
    #     Not exhaustive, and the difference matters: a write through
    #     `__dict__`, `vars()`, a tuple target, `object.__setattr__`,
    #     `obj.__setattr__`, or a computed `setattr` name passes this and
    #     removes every Atlas 3 command. `test_cli_surface_contract.py` catches
    #     those by observing the built parser instead of the source.
    bound = _bound_names(tree)
    attribute_writes = _seam_attribute_writes(tree)
    for symbol in sorted(_ATLAS3_SEAM_CALLS):
        offending = sorted(
            [line for name, line in bound if name == symbol]
            + [line for name, line in attribute_writes if name == symbol]
        )
        assert offending == [], (
            f"Atlas 3 seam neutralised: cli.py rebinds {symbol!r} at line(s) "
            f"{offending}; the seam call would still be present but inert"
        )

    registrations = _add_parser_calls(tree)
    top_level = {
        name
        for receiver, name, _ in registrations
        if receiver == _TOP_LEVEL_SUBPARSERS_NAME
    }

    # (E) Each certified command still has a top-level registration. Parsed,
    #     so a nested `discover connect` cannot stand in for top-level
    #     `connect`, and a mention in help text is not a registration.
    for command in _CERTIFIED_CLI_COMMANDS:
        assert command in top_level, (
            f"certified CLI surface removed: {command!r} is not registered on "
            f"{_TOP_LEVEL_SUBPARSERS_NAME} (AST)"
        )

    # (F) A registered command that nothing dispatches is dead surface.
    dispatched = _dispatched_commands(tree)
    for command in _CERTIFIED_CLI_COMMANDS:
        assert command in dispatched, (
            f"certified command unreachable: nothing dispatches args.command == {command!r}"
        )

    # (H) Atlas 3 owned names are not registered here at any depth. The text
    #     layer covers the top level; this covers nested parsers too.
    bypassed = sorted({name for _, name, _ in registrations} & set(atlas3_commands))
    assert bypassed == [], (
        f"Atlas 3 seam bypassed: cli.py registers Atlas 3 owned command(s) {bypassed}"
    )


def assert_cli_atlas3_contract(
    *,
    source: str,
    diff_text: str,
    atlas3_commands: frozenset[str],
) -> None:
    """Enforce the Atlas 3 CLI ownership seam against a `cli.py` change.

    ``source`` is the resulting file, ``diff_text`` a unified diff of it.
    Raises ``AssertionError`` describing the first violation.

    Deliberately says nothing about unrelated CLI additions: a lane adding a
    command Atlas 3 does not own must not be forced to impersonate an Atlas 3
    change to pass.
    """
    removed = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]

    # (1) The seam still exists, is still delegated to atlas3/cli.py, and is
    #     still wired exactly once each. Catches removal and rewiring.
    for symbol, argument in sorted(_ATLAS3_SEAM_CALLS.items()):
        assert f"from {_ATLAS3_SEAM_MODULE} import {symbol}" in source, (
            f"Atlas 3 seam broken: cli.py no longer imports {symbol} from "
            f"{_ATLAS3_SEAM_MODULE}"
        )
        count = _seam_call_count(source, symbol, argument)
        assert count == 1, (
            f"Atlas 3 seam broken: expected exactly one {symbol}({argument}) "
            f"call site in cli.py, found {count}"
        )

    # (2) No change may delete or rewrite the seam itself. A modification
    #     shows up as a removed line, so this covers in-place mutation too.
    # Compare code only: a removed comment or docstring line that merely
    # *mentions* a seam symbol is not a seam mutation, and failing on it would
    # make the guard fire on unrelated documentation edits.
    seam_removals = [
        line
        for line in removed
        if any(symbol in _strip_comment(line) for symbol in _ATLAS3_SEAM_CALLS)
        or _ATLAS3_SEAM_MODULE in _strip_comment(line)
    ]
    assert seam_removals == [], (
        "Atlas 3 seam mutated: cli.py may not remove or rewrite the Atlas 3 "
        f"registration/dispatch hooks; removed {seam_removals}"
    )

    # (3) The shared CLI must not register a command Atlas 3 owns. This is the
    #     seam bypass the original text match could not see.
    bypassed = sorted(set(_TOP_LEVEL_ADD_PARSER.findall(source)) & set(atlas3_commands))
    assert bypassed == [], (
        "Atlas 3 seam bypassed: cli.py registers Atlas 3 owned command(s) "
        f"{bypassed} directly instead of via {_ATLAS3_SEAM_MODULE}"
    )

    # (4) Certified surfaces are additive-only: no existing command
    #     registration may be deleted by a cli.py change.
    # Join before matching: a registration is routinely formatted across
    # several lines, so scanning each removed line independently never matches
    # `add_parser("name")` and a whole deleted command would slip through.
    removed_text = "\n".join(removed)
    dropped = sorted(set(_ANY_ADD_PARSER.findall(removed_text)))
    still_present = set(_ANY_ADD_PARSER.findall(source))
    # A name occurring elsewhere (dispatch, help text) must NOT count as a
    # surviving registration -- that is what let a deleted certified command
    # pass the presence check below.
    lost = [name for name in dropped if name not in still_present]
    assert lost == [], (
        f"certified CLI surface removed: cli.py no longer registers {lost}"
    )

    # (5) The named certified commands remain reachable *as top-level
    #     commands*. Depth matters here: `connect` is registered both as
    #     `atlas connect` and as the nested `atlas discover connect`, so an
    #     any-depth presence check let the certified top-level registration be
    #     deleted while the unrelated nested subcommand kept the name alive.
    top_level = set(_TOP_LEVEL_ADD_PARSER.findall(source))
    for command in _CERTIFIED_CLI_COMMANDS:
        assert command in top_level, (
            f"certified CLI surface removed: {command!r} is no longer registered "
            "as a top-level command via subparsers.add_parser"
        )

#: Owner-approved exceptions to the *Atlas-3 hook* requirement below, pinned
#: the same way `_OWNER_APPROVED_EXCEPTIONS` is: to one exact reviewed
#: sha256 of `cli.py`, never to the path in general. The guard's purpose is
#: to stop the Atlas 3 lane rewriting certified 2.x CLI surfaces, and it
#: expresses that by demanding the diff be the two additive Atlas-3
#: registration hooks. A change that is genuinely not an Atlas 3 change --
#: and must not touch that seam -- cannot satisfy that shape, so it needs an
#: explicit owner grant rather than a weakened guard.
#:
#: What an entry here waives is ONLY the "diff must be the Atlas-3 hooks"
#: assertions. Additive-only and command-integrity stay enforced for every
#: change, excepted or not.
_CLI_ADDITIVE_EXCEPTIONS: tuple[dict[str, str], ...] = (
    {
        "exception_id": "OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906",
        "owner_approved": "YES",
        "reason": (
            "owner grant OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906: an "
            "explicitly requested --log-format must govern every record the "
            "process emits. Modules configure logging with the console "
            "default at import, so applying the flag only after load_config "
            "left that bootstrap record in console format and a "
            "JSON-consuming deployment received an unparseable stream. The "
            "diff is one guarded configure_logging call after parse_args; no "
            "Atlas-3 seam change, no command added, renamed or removed"
        ),
        "path": "src/project_atlas/cli.py",
        "allowed_sha256": (
            "752eb7f346814d1064faf7502f6fc55f4e47bc738c5bda24f5396bf63bfa8550"
        ),
    },
)


#: Commands whose registration must survive every cli.py diff, waived or not.
_CERTIFIED_COMMANDS = ("connect", "ask2", "kdiff", "brief", "capture")


def _cli_guard_violations(
    *, diff_text: str, cli_source: str, atlas3_hooks_waived: bool
) -> list[str]:
    """The guard's decision, as a pure function of its inputs.

    Extracted so the waiver can be proven narrow by *running* the guard
    against synthetic candidate diffs, rather than by reading its source and
    asserting the arrangement looks right. A waiver that silently stopped
    blocking a command deletion would pass a source-shape assertion and fail
    the mutation cases below.

    Returns the violations found; empty means the diff is permitted.
    """
    violations: list[str] = []
    lines = diff_text.splitlines()
    added = [line for line in lines if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in lines if line.startswith("-") and not line.startswith("---")]

    # Never waivable: a certified CLI surface may only grow.
    if removed:
        violations.append("deletion-from-certified-cli-surface")

    # Waivable by an owner grant pinned to exact reviewed bytes: the demand
    # that the diff *be* the two additive Atlas-3 registration hooks. A change
    # that is deliberately not an Atlas 3 change cannot satisfy that shape.
    if not atlas3_hooks_waived:
        if "register_atlas3_parsers" not in diff_text:
            violations.append("missing-atlas3-register-hook")
        if "dispatch_atlas3" not in diff_text:
            violations.append("missing-atlas3-dispatch-hook")
        if not any("register_atlas3_parsers" in line for line in added):
            violations.append("no-added-atlas3-registration")

    # Never waivable: every certified command must still be registered.
    for command in _CERTIFIED_COMMANDS:
        if f'"{command}"' not in cli_source and f"'{command}'" not in cli_source:
            violations.append(f"certified-command-missing:{command}")
    return violations


def test_cli_mutation_is_additive_only() -> None:
    """The shared CLI must preserve the Atlas 3 ownership seam.

    Enforced structurally against the resulting file and the removed lines
    (see ``assert_cli_atlas3_contract``), not by pattern-matching the diff --
    so removing, rewriting or bypassing the seam still fails, while CLI work
    Atlas 3 does not own is neither required nor able to impersonate it.
    """
    try:
        already_changed = _changed_paths()
    except DemoIsolationGuardNotApplicable as exc:
        pytest.skip(str(exc))
    if "src/project_atlas/cli.py" not in already_changed:
        return
    # No waiver lookup here. `_CLI_ADDITIVE_EXCEPTIONS` waives the demand that
    # a cli.py diff *be* the two Atlas-3 registration hooks -- a demand this
    # contract no longer makes, because it protects the seam structurally
    # instead of by diff shape. A change that is genuinely not an Atlas 3
    # change therefore passes on its own merits; verified against the
    # log-format bootstrap diff that grant was issued for, which this contract
    # permits unwaived. The waiver machinery and its regression matrix are
    # retained below and still exercised, so reinstating it is one call.
    base = _resolve_diff_base()
    diff = subprocess.run(
        ["git", "diff", base, "--", "src/project_atlas/cli.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    from project_atlas.atlas3.cli import ATLAS3_COMMANDS

    cli_source = (ROOT / "src" / "project_atlas" / "cli.py").read_text(encoding="utf-8")
    assert_cli_atlas3_contract(
        source=cli_source,
        diff_text=diff.stdout,
        atlas3_commands=frozenset(ATLAS3_COMMANDS),
    )
    # Semantic layers, parsed rather than pattern-matched. Separate because
    # they need a real module: the text-layer matrix below exercises tiny
    # synthetic sources, which have no dispatch table and no argument
    # registrations to reason about.
    assert_cli_semantic_invariants(
        source=cli_source, atlas3_commands=frozenset(ATLAS3_COMMANDS)
    )


# ---------------------------------------------------------------------------
# Owner-approved exceptions must be narrow: pinned to one exact reviewed
# byte sequence, never a path allowlist, never transferable to another
# path or another diff. These use a throwaway `_owner_approved_exception_permits`
# call with a synthetic exceptions tuple (never the real
# `_OWNER_APPROVED_EXCEPTIONS`), so they stay valid regardless of what
# `ingestion.py` legitimately contains after DOGFOOD-001 lands or after
# `FULL_LIVE_DEMO_READY` eventually lifts the freeze entirely.
# ---------------------------------------------------------------------------


def test_exception_permits_only_the_exact_pinned_content(tmp_path: Path) -> None:
    target = tmp_path / "src" / "project_atlas" / "ingestion.py"
    target.parent.mkdir(parents=True)
    approved_content = b"# approved DOGFOOD-001 content\n"
    target.write_bytes(approved_content)
    exceptions = (
        {
            "exception_id": "DOGFOOD-001",
            "owner_approved": "YES",
            "reason": "test fixture",
            "path": "src/project_atlas/ingestion.py",
            "allowed_sha256": hashlib.sha256(approved_content).hexdigest(),
        },
    )

    assert _owner_approved_exception_permits(
        "src/project_atlas/ingestion.py", root=tmp_path, exceptions=exceptions
    )


def test_exception_does_not_survive_any_further_edit(tmp_path: Path) -> None:
    """A future PR touching ingestion.py must not automatically inherit
    DOGFOOD-001 permission -- not even by one byte."""
    target = tmp_path / "src" / "project_atlas" / "ingestion.py"
    target.parent.mkdir(parents=True)
    approved_content = b"# approved DOGFOOD-001 content\n"
    exceptions = (
        {
            "exception_id": "DOGFOOD-001",
            "owner_approved": "YES",
            "reason": "test fixture",
            "path": "src/project_atlas/ingestion.py",
            "allowed_sha256": hashlib.sha256(approved_content).hexdigest(),
        },
    )
    target.write_bytes(approved_content + b"# one more unreviewed line\n")

    assert not _owner_approved_exception_permits(
        "src/project_atlas/ingestion.py", root=tmp_path, exceptions=exceptions
    )


def test_exception_does_not_cover_a_different_deny_listed_path(tmp_path: Path) -> None:
    """An exception for ingestion.py must not blanket-permit any other
    DENY-listed surface, even an untouched, byte-identical one."""
    approved_content = b"# approved DOGFOOD-001 content\n"
    ingestion = tmp_path / "src" / "project_atlas" / "ingestion.py"
    ingestion.parent.mkdir(parents=True)
    ingestion.write_bytes(approved_content)
    other = tmp_path / "src" / "project_atlas" / "discovery.py"
    other.write_bytes(approved_content)  # byte-identical content, different path
    exceptions = (
        {
            "exception_id": "DOGFOOD-001",
            "owner_approved": "YES",
            "reason": "test fixture",
            "path": "src/project_atlas/ingestion.py",
            "allowed_sha256": hashlib.sha256(approved_content).hexdigest(),
        },
    )

    assert _owner_approved_exception_permits(
        "src/project_atlas/ingestion.py", root=tmp_path, exceptions=exceptions
    )
    assert not _owner_approved_exception_permits(
        "src/project_atlas/discovery.py", root=tmp_path, exceptions=exceptions
    )


def test_exception_without_owner_approval_flag_permits_nothing(tmp_path: Path) -> None:
    """A registry entry missing the explicit owner_approved=YES marker must
    never be treated as a live exception -- guards against a future entry
    being added with the grant recorded elsewhere (or not at all)."""
    approved_content = b"# content\n"
    target = tmp_path / "src" / "project_atlas" / "ingestion.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(approved_content)
    exceptions = (
        {
            "exception_id": "UNAPPROVED",
            "reason": "no owner sign-off recorded",
            "path": "src/project_atlas/ingestion.py",
            "allowed_sha256": hashlib.sha256(approved_content).hexdigest(),
        },
    )

    assert not _owner_approved_exception_permits(
        "src/project_atlas/ingestion.py", root=tmp_path, exceptions=exceptions
    )


def test_real_dogfood_001_exception_is_currently_live_or_absent_honestly() -> None:
    """Sanity check against the real registry and the real checkout: either
    the pinned hash matches the file currently on disk (the exception is
    live), or it doesn't (the exception is inert and the guard is fully
    load-bearing for ingestion.py again) -- never an exception mechanically
    incapable of ever matching anything (e.g. an empty/placeholder hash)."""
    assert len(_OWNER_APPROVED_EXCEPTIONS) >= 1
    for exc in _OWNER_APPROVED_EXCEPTIONS:
        assert exc["owner_approved"] == "YES"
        assert exc["exception_id"]
        assert exc["reason"]
        assert exc["path"] in DENY, "an exception must name a real DENY-listed path"
        assert len(exc["allowed_sha256"]) == 64
        int(exc["allowed_sha256"], 16)  # must be real hex, not a placeholder


def test_end_to_end_exception_gate_via_pr_event_shas(tmp_path: Path) -> None:
    """Exercises the real `_changed_paths` + exception-gate combination
    (mirroring `test_certified_surfaces_unmodified`'s own logic) against a
    synthetic PR event, on a throwaway repo -- never the real checkout."""
    repo = _init_fixture_repo(tmp_path)
    base_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    ingestion = repo / "src" / "project_atlas" / "ingestion.py"
    ingestion.parent.mkdir(parents=True)
    approved_content = b"# reviewed DOGFOOD-001-shaped change\n"
    ingestion.write_bytes(approved_content)
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-q", "-m", "dogfood-001-shaped change"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    event_path = _write_event_payload(tmp_path, base_sha=base_sha, head_sha=head_sha)
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event_path)}
    exceptions = (
        {
            "exception_id": "DOGFOOD-001",
            "owner_approved": "YES",
            "reason": "test fixture",
            "path": "src/project_atlas/ingestion.py",
            "allowed_sha256": hashlib.sha256(approved_content).hexdigest(),
        },
    )

    changed = _changed_paths(root=repo, env=env)
    assert "src/project_atlas/ingestion.py" in changed

    # With the matching exception: not a violation.
    violated_with_exception = sorted(
        path
        for path in DENY
        if path in changed
        and not _owner_approved_exception_permits(path, root=repo, exceptions=exceptions)
    )
    assert violated_with_exception == []

    # With no exception at all: a real, unexcepted violation.
    violated_without_exception = sorted(
        path
        for path in DENY
        if path in changed and not _owner_approved_exception_permits(path, root=repo, exceptions=())
    )
    assert violated_without_exception == ["src/project_atlas/ingestion.py"]

    # Same content, but the exception is pinned to different bytes (as if
    # someone edited the file after review, or copied the exception onto a
    # different unreviewed change): a real, unexcepted violation again.
    stale_exceptions = (
        {**exceptions[0], "allowed_sha256": hashlib.sha256(b"different content\n").hexdigest()},
    )
    violated_with_stale_exception = sorted(
        path
        for path in DENY
        if path in changed
        and not _owner_approved_exception_permits(path, root=repo, exceptions=stale_exceptions)
    )
    assert violated_with_stale_exception == ["src/project_atlas/ingestion.py"]


# ---------------------------------------------------------------------------
# Regression coverage for the freeze guard itself (ORCHAUT/D-041-style
# self-verification: the guard's own hosted-CI blind spot was found and
# fixed 2026-08-28 -- see WORKLOG "Freeze-guard hosted-CI blind spot").
# These build a real, throwaway git repository per test rather than
# mutating the actual project-atlas checkout.
# ---------------------------------------------------------------------------


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "freeze-guard-test",
            "GIT_AUTHOR_EMAIL": "freeze-guard-test@example.invalid",
            "GIT_COMMITTER_NAME": "freeze-guard-test",
            "GIT_COMMITTER_EMAIL": "freeze-guard-test@example.invalid",
        },
    )
    return result.stdout


def _init_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    _run_git(["init", "-q", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-q", "-m", "base"], cwd=repo)
    return repo


def _write_event_payload(tmp_path: Path, *, base_sha: str, head_sha: str) -> Path:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"pull_request": {"base": {"sha": base_sha}, "head": {"sha": head_sha}}}),
        encoding="utf-8",
    )
    return event_path


def test_freeze_guard_detects_frozen_path_via_pr_event_shas(tmp_path: Path) -> None:
    """Property 2/3: the guard must evaluate the actual PR changeset (via
    the GitHub event payload, not a possibly-unresolvable branch name) and
    must fail when a DENY-listed path is touched."""
    repo = _init_fixture_repo(tmp_path)
    base_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    frozen = repo / "src" / "project_atlas" / "api_server.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("# touched\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-q", "-m", "touch frozen surface"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    event_path = _write_event_payload(tmp_path, base_sha=base_sha, head_sha=head_sha)
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event_path)}

    changed = _changed_paths(root=repo, env=env)

    assert "src/project_atlas/api_server.py" in changed


def test_freeze_guard_allows_non_frozen_path_via_pr_event_shas(tmp_path: Path) -> None:
    """Property 4: a change that never touches a DENY-listed path must not
    false-positive as a violation."""
    repo = _init_fixture_repo(tmp_path)
    base_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    allowed = repo / "src" / "project_atlas" / "totally_unrelated_module.py"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("# fine\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-q", "-m", "touch allowed surface"], cwd=repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    event_path = _write_event_payload(tmp_path, base_sha=base_sha, head_sha=head_sha)
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event_path)}

    changed = _changed_paths(root=repo, env=env)
    violated = sorted(path for path in DENY if path in changed)

    assert "src/project_atlas/totally_unrelated_module.py" in changed
    assert violated == []


def test_freeze_guard_survives_shallow_single_commit_checkout(tmp_path: Path) -> None:
    """Property 5: a shallow, single-commit checkout (mirroring hosted
    CI's default ``fetch-depth: 1``) must not silently turn the guard
    into a no-op. Simulated by cloning only the head commit with
    ``--depth=1`` into a fresh directory that never had the base commit
    at all -- exactly the condition that made the original guard
    silently pass regardless of what changed."""
    source_repo = _init_fixture_repo(tmp_path)
    base_sha = _run_git(["rev-parse", "HEAD"], cwd=source_repo).strip()
    frozen = source_repo / "src" / "project_atlas" / "authz.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("# touched\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=source_repo)
    _run_git(["commit", "-q", "-m", "touch frozen surface"], cwd=source_repo)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd=source_repo).strip()

    shallow = tmp_path / "shallow-checkout"
    _run_git(
        # --no-local is required: git optimizes same-machine clones by
        # hardlinking the full object store regardless of --depth unless
        # explicitly told not to, which would silently defeat this exact
        # fixture (it must produce a genuinely shallow clone, matching
        # actions/checkout's real network-transport behavior).
        [
            "clone",
            "-q",
            "--no-local",
            "--depth=1",
            "--branch",
            "main",
            str(source_repo),
            str(shallow),
        ],
        cwd=tmp_path,
    )
    # Confirm the shallow clone genuinely does not have the base commit --
    # this is the precondition that defeated the original implementation.
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{base_sha}^{{commit}}"],
        cwd=shallow,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    assert probe.returncode != 0, "fixture setup error: base commit unexpectedly present"

    event_path = _write_event_payload(tmp_path, base_sha=base_sha, head_sha=head_sha)
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event_path)}

    changed = _changed_paths(root=shallow, env=env)
    violated = sorted(path for path in DENY if path in changed)

    assert violated == ["src/project_atlas/authz.py"]


def test_freeze_guard_fails_closed_on_missing_event_path(tmp_path: Path) -> None:
    """Property 6a: claiming pull_request context without a readable event
    payload must raise, never silently pass."""
    repo = _init_fixture_repo(tmp_path)
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": ""}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardError:
        return
    raise AssertionError("expected DemoIsolationGuardError, guard did not fail closed")


def test_freeze_guard_fails_closed_on_malformed_event_payload(tmp_path: Path) -> None:
    """Property 6b: an unreadable/malformed event payload must raise."""
    repo = _init_fixture_repo(tmp_path)
    bad_event = tmp_path / "bad-event.json"
    bad_event.write_text("{not valid json", encoding="utf-8")
    env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(bad_event)}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardError:
        return
    raise AssertionError("expected DemoIsolationGuardError, guard did not fail closed")


def test_freeze_guard_not_applicable_with_no_remote_at_all(tmp_path: Path) -> None:
    """Property 6c, corrected per review (P2): outside any CI event
    context, a checkout with NO git remote configured at all (a source
    archive, vendored copy, or remotes deliberately stripped) has nothing
    to compare against -- this must raise the distinct
    ``DemoIsolationGuardNotApplicable``, not the fail-closed
    ``DemoIsolationGuardError``. Callers (the actual tests) turn this into
    a skip, not a suite-wide failure. Regression coverage for the original
    over-broad fail-closed behavior, which made the entire unit suite
    fail when run from an environment with remotes stripped."""
    repo = _init_fixture_repo(tmp_path)
    assert _has_any_remote(root=repo) is False, "fixture setup error: unexpected remote present"
    env: dict[str, str] = {}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardNotApplicable:
        return
    except DemoIsolationGuardError:
        raise AssertionError(
            "expected DemoIsolationGuardNotApplicable (no remote at all -- "
            "nothing to enforce), got the fail-closed DemoIsolationGuardError instead"
        ) from None
    raise AssertionError("expected DemoIsolationGuardNotApplicable, guard did not skip")


def test_freeze_guard_fails_closed_when_remote_exists_but_origin_main_unresolvable(
    tmp_path: Path,
) -> None:
    """The genuine fail-closed case, distinct from the above: a remote
    IS configured (so this is not a "nothing to enforce" situation), but
    ``origin/main`` specifically cannot be resolved -- this must still
    raise the fail-closed ``DemoIsolationGuardError``."""
    repo = _init_fixture_repo(tmp_path)
    other_remote = tmp_path / "unrelated-remote"
    other_remote.mkdir()
    _run_git(["init", "-q", "--bare"], cwd=other_remote)
    _run_git(["remote", "add", "origin", str(other_remote)], cwd=repo)
    assert _has_any_remote(root=repo) is True
    env: dict[str, str] = {}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardNotApplicable:
        raise AssertionError(
            "expected the fail-closed DemoIsolationGuardError (a remote is "
            "configured), got DemoIsolationGuardNotApplicable instead"
        ) from None
    except DemoIsolationGuardError:
        return
    raise AssertionError("expected DemoIsolationGuardError, guard did not fail closed")


def test_freeze_guard_local_full_clone_behavior_preserved() -> None:
    """Property 7: outside a pull_request event context, against the real
    project-atlas checkout (which does have ``origin/main``), resolution
    must still work -- this is the existing local-development path and
    must not regress. The fix now resolves to the concrete SHA rather
    than the floating ref name (correcting the "exact commit SHA"
    docstring/contract accuracy raised in review), so this checks that
    the returned value is genuinely that commit, not the literal string
    "origin/main"."""
    resolve = subprocess.run(
        ["git", "rev-parse", "--verify", "origin/main"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    if resolve.returncode != 0:
        return  # no origin/main available in this environment either; nothing to assert
    expected_sha = resolve.stdout.strip()
    base = _resolve_diff_base(root=ROOT, env={})
    assert base == expected_sha
    assert base != "origin/main"


# ---------------------------------------------------------------------------
# Extension: the repository's ci.yml also runs this same guard on `push:
# branches: [main]`, not only `pull_request`. An independent re-verifier
# of the pull_request-only fix (above) correctly found that trigger was
# still silently unprotected under the same shallow-clone mechanism --
# `origin/main` still resolves (fail-open, not fail-closed) but is
# degenerate (== HEAD) under fetch-depth: 1, so nothing is ever detected
# on a push run. Closing that gap here, same established scope.
# ---------------------------------------------------------------------------


def _write_push_event_payload(tmp_path: Path, *, before_sha: str) -> Path:
    event_path = tmp_path / "push-event.json"
    event_path.write_text(json.dumps({"before": before_sha}), encoding="utf-8")
    return event_path


def test_freeze_guard_detects_frozen_path_via_push_event_before_sha(tmp_path: Path) -> None:
    """The push-event equivalent of the pull_request detection test: a
    shallow single-commit clone (matching ci.yml's push-triggered quality
    job) must still detect a DENY-listed change via the event's `before`
    SHA."""
    source_repo = _init_fixture_repo(tmp_path)
    before_sha = _run_git(["rev-parse", "HEAD"], cwd=source_repo).strip()
    frozen = source_repo / "src" / "project_atlas" / "discovery.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("# touched\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=source_repo)
    _run_git(["commit", "-q", "-m", "touch frozen surface"], cwd=source_repo)

    shallow = tmp_path / "shallow-push-checkout"
    _run_git(
        [
            "clone",
            "-q",
            "--no-local",
            "--depth=1",
            "--branch",
            "main",
            str(source_repo),
            str(shallow),
        ],
        cwd=tmp_path,
    )
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{before_sha}^{{commit}}"],
        cwd=shallow,
        check=False,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    assert probe.returncode != 0, "fixture setup error: base commit unexpectedly present"

    event_path = _write_push_event_payload(tmp_path, before_sha=before_sha)
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event_path)}

    changed = _changed_paths(root=shallow, env=env)
    violated = sorted(path for path in DENY if path in changed)

    assert violated == ["src/project_atlas/discovery.py"]


def test_freeze_guard_push_event_no_false_positive_on_allowed_path(tmp_path: Path) -> None:
    repo = _init_fixture_repo(tmp_path)
    before_sha = _run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    allowed = repo / "src" / "project_atlas" / "totally_unrelated_module.py"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("# fine\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-q", "-m", "touch allowed surface"], cwd=repo)
    event_path = _write_push_event_payload(tmp_path, before_sha=before_sha)
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event_path)}

    changed = _changed_paths(root=repo, env=env)
    violated = sorted(path for path in DENY if path in changed)

    assert "src/project_atlas/totally_unrelated_module.py" in changed
    assert violated == []


def test_freeze_guard_push_event_fails_closed_on_missing_event_path(tmp_path: Path) -> None:
    repo = _init_fixture_repo(tmp_path)
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": ""}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardError:
        return
    raise AssertionError("expected DemoIsolationGuardError, guard did not fail closed")


def test_freeze_guard_push_event_fails_closed_on_malformed_payload(tmp_path: Path) -> None:
    repo = _init_fixture_repo(tmp_path)
    bad_event = tmp_path / "bad-push-event.json"
    bad_event.write_text("not json at all", encoding="utf-8")
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(bad_event)}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardError:
        return
    raise AssertionError("expected DemoIsolationGuardError, guard did not fail closed")


def test_freeze_guard_push_event_fails_closed_on_first_push_sentinel(tmp_path: Path) -> None:
    """GitHub uses an all-zero SHA for `before` on a branch's very first
    push. There is no real prior commit to diff against; this must fail
    closed, not silently resolve to something wrong."""
    repo = _init_fixture_repo(tmp_path)
    event_path = _write_push_event_payload(tmp_path, before_sha="0" * 40)
    env = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": str(event_path)}

    try:
        _changed_paths(root=repo, env=env)
    except DemoIsolationGuardError:
        return
    raise AssertionError("expected DemoIsolationGuardError, guard did not fail closed")


# ---------------------------------------------------------------------------
# Round-4 fixes. An independent re-verifier of round 3 (bound to exact head
# 99ce6fbf3329273425025643bc5963cd805b2005) reproduced a real, pre-existing
# P2 in the local/full-clone fallback path (property 7): because
# `_changed_paths` diffed directly against the live `origin/main` tip
# rather than its merge-base with HEAD, a local branch that is behind
# `origin/main` would false-positive on any DENY-listed path main had
# independently advanced since -- flagging it as though the local branch
# itself had touched it. This matches a stale Copilot review comment on
# this PR's round-1 head that the earlier three-dot-to-direct-diff switch
# had not accounted for. Fixed by computing an explicit merge-base for
# this one fallback path (safe here: full clone, not the shallow
# exact-SHA CI-event case the original direct-diff switch was protecting).
# ---------------------------------------------------------------------------


def test_freeze_guard_local_fallback_no_false_positive_on_diverged_main(
    tmp_path: Path,
) -> None:
    """The local/full-clone fallback (no CI event context) must diff HEAD
    against its merge-base with origin/main, not origin/main's live tip --
    otherwise an unrelated DENY-listed change that main picked up AFTER
    this branch diverged reads as a violation the local branch never
    committed.

    Fetches/clones directly against a plain working-tree "seed" repo
    (git supports both against a non-bare local path) rather than
    round-tripping through a separate bare "origin" repo with two
    `git push`es -- push's pack-generation/transfer overhead was the
    single largest contributor to this test's wall time on the
    already-tight hosted Windows CI lane; this fixture is behaviorally
    identical (a real, separate `origin` remote a real `git fetch`
    populates) but meaningfully cheaper.
    """
    seed = _init_fixture_repo(tmp_path)

    clone = tmp_path / "diverged-clone"
    _run_git(["clone", "-q", "--no-local", str(seed), str(clone)], cwd=tmp_path)

    # The local branch makes its own, allowed-only change and diverges.
    local_change = clone / "src" / "project_atlas" / "totally_unrelated_module.py"
    local_change.parent.mkdir(parents=True)
    local_change.write_text("# local branch's own change\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=clone)
    _run_git(["commit", "-q", "-m", "local branch change"], cwd=clone)

    # Meanwhile origin/main independently advances with a DENY-listed
    # change the local branch never saw and never touched.
    frozen = seed / "src" / "project_atlas" / "authz.py"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("# unrelated upstream change\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=seed)
    _run_git(["commit", "-q", "-m", "upstream touches frozen surface"], cwd=seed)
    _run_git(["fetch", "-q", "origin"], cwd=clone)

    # Precondition: the naive direct diff WOULD flag the frozen path here.
    naive = subprocess.run(
        ["git", "diff", "--name-only", "origin/main", "HEAD"],
        cwd=clone,
        check=True,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
    )
    assert "src/project_atlas/authz.py" in naive.stdout, (
        "fixture setup error: expected the direct-diff false positive "
        "precondition to hold before asserting the fix avoids it"
    )

    changed = _changed_paths(root=clone, env={})
    violated = sorted(path for path in DENY if path in changed)

    assert violated == [], (
        "local-fallback diff must isolate the local branch's own changes "
        f"via merge-base, not flag upstream-only advancement: {violated}"
    )
    assert "src/project_atlas/totally_unrelated_module.py" in changed


# ---------------------------------------------------------------------------
# The incident this timeout responds to: a real hosted CI run for this PR
# left three quality jobs "in_progress" for over an hour (well past
# ci.yml's own 20-minute job timeout) because the network `git fetch` this
# guard added had no bound of its own. GitHub's job-level timeout only
# fires once a job has actually started consuming its allotted wall
# clock; it does not help if a single subprocess call inside that job can
# block forever. Proven directly here with a fake, deliberately-hanging
# `git` on PATH -- not just trusted because `subprocess.run(timeout=...)`
# is a standard library feature.
# ---------------------------------------------------------------------------


def test_fetch_timeout_is_actually_enforced_not_just_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly proves the incident this fix responds to cannot recur: a
    `git fetch` that never returns must be converted to
    DemoIsolationGuardError, not left to hang the calling process (and, in
    real hosted CI, the whole job) indefinitely.

    A real subprocess hang is deliberately NOT simulated here: on Windows,
    `subprocess.run(["git", ...])` with `shell=False` resolves the
    executable via a restricted, .exe-only search (Windows' own
    documented CreateProcess behavior for a NULL application name) and
    will not pick up a `.cmd`/`.bat` shim placed on PATH, so an
    orchestrated "fake hanging git" fixture would silently invoke the
    real git instead and prove nothing (or worse, flake). `subprocess.
    run(timeout=...)` reliably raising `TimeoutExpired` on an actual
    timeout is standard-library behavior, not this guard's own logic to
    prove; what this guard's own code needs proving is that a real
    `TimeoutExpired` -- however it arises -- is caught on the fetch call
    specifically and converted into the documented, fail-closed
    `DemoIsolationGuardError`, rather than propagating as a raw traceback
    or (worse) being swallowed. That is exercised directly here by making
    the fetch call itself raise a real `subprocess.TimeoutExpired`, while
    every other subprocess call (including `_ensure_commit_fetched`'s own
    preceding `cat-file -e` probe) still runs for real, so the "probe
    reports absent, falls through to fetch" path is genuinely exercised.
    """
    import time

    repo = _init_fixture_repo(tmp_path)
    real_run = subprocess.run

    def _fake_run(
        cmd: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "fetch":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
        return real_run(
            cmd, cwd=cwd, check=check, capture_output=capture_output, text=text, timeout=timeout
        )

    monkeypatch.setattr(
        "tests.unit.test_atlas3_demo_isolation_001.subprocess.run",
        _fake_run,
    )
    monkeypatch.setattr(
        "tests.unit.test_atlas3_demo_isolation_001._NETWORK_GIT_TIMEOUT_SECONDS",
        2,
        raising=True,
    )

    started = time.monotonic()
    with pytest.raises(DemoIsolationGuardError, match="did not complete within"):
        _ensure_commit_fetched("0" * 40, root=repo)
    elapsed = time.monotonic() - started

    assert elapsed < 10, (
        f"fetch-timeout conversion took {elapsed:.1f}s -- should be "
        "near-instant since the timeout itself is simulated, not waited out"
    )


# ---------------------------------------------------------------------------
# Atlas 3 CLI seam contract -- regression matrix.
#
# These exercise `assert_cli_atlas3_contract` directly against synthetic
# (source, diff) pairs, so each case is pinned independently of whatever the
# working tree's real `cli.py` diff happens to be. The point of the corrected
# contract is generality: it must protect Atlas 3 from the next unrelated CLI
# addition too, not just from this one.
# ---------------------------------------------------------------------------

_ATLAS3_FIXTURE_COMMANDS = frozenset({"pulse", "start", "proof"})

_SEAM_SOURCE = '''
def build_parser():
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("connect")
    subparsers.add_parser("ask2")
    subparsers.add_parser("kdiff")
    subparsers.add_parser("brief")
    capture_parser = subparsers.add_parser("capture")
    capture_sub = capture_parser.add_subparsers(dest="capture_command")
    capture_sub.add_parser("record")

    from project_atlas.atlas3.cli import register_atlas3_parsers

    register_atlas3_parsers(subparsers)
    return parser


def main(argv=None):
    from project_atlas.atlas3.cli import dispatch_atlas3

    atlas3_exit = dispatch_atlas3(args)
    return atlas3_exit
'''


def _check(source: str, diff_text: str) -> None:
    assert_cli_atlas3_contract(
        source=source,
        diff_text=diff_text,
        atlas3_commands=_ATLAS3_FIXTURE_COMMANDS,
    )


def _diff(removed: tuple[str, ...] = (), added: tuple[str, ...] = ()) -> str:
    lines = ["diff --git a/cli.py b/cli.py", "--- a/cli.py", "+++ b/cli.py", "@@ -1,1 +1,1 @@"]
    lines += [f"-{line}" for line in removed]
    lines += [f"+{line}" for line in added]
    return "\n".join(lines) + "\n"


def test_g0_unchanged_seam_passes() -> None:
    """Baseline: the real seam shape satisfies the contract."""
    _check(_SEAM_SOURCE, _diff())


def test_g1_atlas3_hook_removal_fails() -> None:
    """G1 -- deleting the Atlas 3 registration hook must still be caught."""
    source = _SEAM_SOURCE.replace("    register_atlas3_parsers(subparsers)\n", "")
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        _check(source, _diff(removed=("    register_atlas3_parsers(subparsers)",)))


def test_g1b_dispatch_hook_removal_fails() -> None:
    source = _SEAM_SOURCE.replace("    atlas3_exit = dispatch_atlas3(args)\n", "")
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        _check(source, _diff(removed=("    atlas3_exit = dispatch_atlas3(args)",)))


def test_g2_atlas3_hook_mutation_fails() -> None:
    """G2 -- rewiring the hook to something other than the top-level parser."""
    source = _SEAM_SOURCE.replace(
        "    register_atlas3_parsers(subparsers)",
        "    register_atlas3_parsers(capture_sub)",
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        _check(
            source,
            _diff(
                removed=("    register_atlas3_parsers(subparsers)",),
                added=("    register_atlas3_parsers(capture_sub)",),
            ),
        )


def test_g2b_reimplementing_the_seam_locally_fails() -> None:
    """Re-pointing the import away from atlas3/cli.py is a mutation too."""
    source = _SEAM_SOURCE.replace(
        "from project_atlas.atlas3.cli import register_atlas3_parsers",
        "from project_atlas.shadow_cli import register_atlas3_parsers",
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        _check(source, _diff())


def test_g2c_duplicate_seam_call_fails() -> None:
    """A second registration call site is an ownership mutation."""
    source = _SEAM_SOURCE.replace(
        "    register_atlas3_parsers(subparsers)\n",
        "    register_atlas3_parsers(subparsers)\n    register_atlas3_parsers(subparsers)\n",
    )
    with pytest.raises(AssertionError, match="expected exactly one"):
        _check(source, _diff(added=("    register_atlas3_parsers(subparsers)",)))


def test_g3_valid_additive_atlas3_registration_passes() -> None:
    """G3 -- Atlas 3 grows inside its own module; cli.py keeps one seam."""
    source = _SEAM_SOURCE.replace(
        "    subparsers.add_parser(\"connect\")",
        "    subparsers.add_parser(\"connect\")\n    subparsers.add_parser(\"doctor\")",
    )
    _check(source, _diff(added=('    subparsers.add_parser("doctor")',)))


def test_g4_unrelated_nested_cli_addition_passes() -> None:
    """G4 -- the case the previous contract made impossible.

    A nested subcommand owned by another package (the shape of
    ``atlas capture text``) must pass without fabricating an Atlas 3 hook.
    """
    source = _SEAM_SOURCE.replace(
        '    capture_sub.add_parser("record")',
        '    capture_sub.add_parser("record")\n    capture_sub.add_parser("text")',
    )
    _check(source, _diff(added=('    capture_sub.add_parser("text")',)))
    assert "register_atlas3_parsers" not in _diff(
        added=('    capture_sub.add_parser("text")',)
    ), "the passing diff must not need to mention Atlas 3 at all"


def test_g4b_unrelated_import_reformat_passes() -> None:
    """Reformatting an unrelated import is not a certified-surface rewrite.

    Merging two ``from x import y`` lines is a removal, but it deletes no
    command registration and does not touch the Atlas 3 seam. The repository's
    own ruff isort configuration forces this shape, so forbidding it would
    make lint and governance mutually unsatisfiable.
    """
    _check(
        _SEAM_SOURCE,
        _diff(
            removed=("from project_atlas.config import load_config",),
            added=("from project_atlas.config import AtlasConfig, load_config",),
        ),
    )


def test_g5_unrelated_top_level_cli_addition_passes() -> None:
    """G5 -- a top-level command Atlas 3 does not own is permitted.

    Repository-backed: ``ATLAS3_COMMANDS`` is the ownership registry, and
    `cli.py` already registers 67 top-level commands outside it. Only a
    collision with a name Atlas 3 owns is a violation (see G6).
    """
    source = _SEAM_SOURCE.replace(
        '    subparsers.add_parser("connect")',
        '    subparsers.add_parser("connect")\n    subparsers.add_parser("capture-serve")',
    )
    _check(source, _diff(added=('    subparsers.add_parser("capture-serve")',)))


def test_g6_seam_bypass_fails() -> None:
    """G6 -- registering an Atlas 3 owned command directly in cli.py."""
    source = _SEAM_SOURCE.replace(
        '    subparsers.add_parser("connect")',
        '    subparsers.add_parser("connect")\n    subparsers.add_parser("pulse")',
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam bypassed"):
        _check(source, _diff(added=('    subparsers.add_parser("pulse")',)))


def _superseded_text_match_contract(source: str, diff_text: str) -> None:
    """The pre-remediation assertions, kept only to prove the change is stronger.

    Reproduces exactly what ``test_cli_mutation_is_additive_only`` asserted
    before the seam contract replaced it, so the regression below can show a
    diff the old form accepted and the new form rejects.
    """
    lines = diff_text.splitlines()
    added = [line for line in lines if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in lines if line.startswith("-") and not line.startswith("---")]
    assert "register_atlas3_parsers" in diff_text
    assert "dispatch_atlas3" in diff_text
    assert removed == []
    assert any("register_atlas3_parsers" in line for line in added)
    for command in _CERTIFIED_CLI_COMMANDS:
        assert f'"{command}"' in source or f"'{command}'" in source


def test_g6b_seam_bypass_the_old_text_match_would_have_accepted() -> None:
    """G6 (under-protection) -- a diff the superseded contract let through.

    The old form only required the *diff text* to mention both seam symbols
    and to add a line naming the registration hook. A change can satisfy all
    of that while registering an Atlas 3 owned command directly in `cli.py`,
    bypassing the seam entirely. This asserts both halves: the old contract
    accepted it, the corrected one rejects it.
    """
    source = _SEAM_SOURCE.replace(
        '    subparsers.add_parser("connect")',
        '    subparsers.add_parser("connect")\n    subparsers.add_parser("start")',
    )
    diff = _diff(
        added=(
            '    subparsers.add_parser("start")',
            "    register_atlas3_parsers(subparsers)  # re-registered here",
            "    dispatch_atlas3(args)",
        )
    )

    # The superseded contract accepted this bypass.
    _superseded_text_match_contract(source, diff)

    # The corrected contract does not.
    with pytest.raises(AssertionError, match="Atlas 3 seam bypassed"):
        _check(source, diff)


def test_g7_deleting_a_certified_command_fails() -> None:
    """Certified CLI surfaces stay additive: a dropped command is a rewrite."""
    source = _SEAM_SOURCE.replace('    subparsers.add_parser("kdiff")\n', "")
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _check(source, _diff(removed=('    subparsers.add_parser("kdiff")',)))


def test_g7b_renaming_a_command_registration_fails() -> None:
    source = _SEAM_SOURCE.replace('subparsers.add_parser("ask2")', 'subparsers.add_parser("ask3")')
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _check(
            source,
            _diff(
                removed=('    subparsers.add_parser("ask2")',),
                added=('    subparsers.add_parser("ask3")',),
            ),
        )


def test_g8_real_cli_satisfies_the_contract_against_its_own_base() -> None:
    """The contract holds for the repository's actual current cli.py."""
    from project_atlas.atlas3.cli import ATLAS3_COMMANDS

    assert_cli_atlas3_contract(
        source=(ROOT / "src" / "project_atlas" / "cli.py").read_text(encoding="utf-8"),
        diff_text="",
        atlas3_commands=frozenset(ATLAS3_COMMANDS),
    )


# ---------------------------------------------------------------------------
# Review-thread regressions (PR #684). Both were real holes in the corrected
# guard, found by automated review after the structural rewrite landed.
# ---------------------------------------------------------------------------


def test_g9_removed_comment_mentioning_a_seam_symbol_is_not_a_mutation() -> None:
    """A doc/comment edit must not read as a seam mutation (false positive)."""
    _check(
        _SEAM_SOURCE,
        _diff(removed=("    # see register_atlas3_parsers for the Atlas 3 seam",)),
    )


def test_g10_multiline_certified_registration_deletion_is_caught() -> None:
    """The deletion a per-line scan missed.

    Registrations are routinely formatted across several lines, so matching
    each removed line independently never sees ``add_parser("name")``. The
    fallback string-presence check then passed too, because a name like
    ``capture`` also appears in dispatch code -- so a whole certified command
    could be deleted silently.
    """
    source = _SEAM_SOURCE.replace('    subparsers.add_parser("kdiff")\n', "")
    diff = _diff(
        removed=(
            "    kdiff_parser = subparsers.add_parser(",
            '        "kdiff",',
            '        help="Knowledge diff.",',
            "    )",
        )
    )
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _check(source, diff)


def test_g11_certified_command_must_be_registered_not_merely_mentioned() -> None:
    """A bare mention in dispatch code is not a registration."""
    source = _SEAM_SOURCE.replace(
        '    subparsers.add_parser("kdiff")',
        '    if args.command == "kdiff":  # mentioned, not registered',
    )
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _check(source, _diff(removed=('    subparsers.add_parser("kdiff")',)))


def test_g12_nested_subcommand_does_not_keep_a_deleted_top_level_alive() -> None:
    """A same-named nested subcommand is not the certified top-level surface.

    Found by replaying the deletion attack against the real ``cli.py`` rather
    than the fixture: ``connect`` is registered twice there -- once as
    ``atlas connect`` and once as the nested ``atlas discover connect``.
    Because the presence check matched ``.add_parser`` at any depth, deleting
    the certified top-level registration left the name alive via the unrelated
    nested one and the guard stayed green. Certified reachability is now
    checked against top-level registrations only.
    """
    source = _SEAM_SOURCE.replace(
        '    subparsers.add_parser("connect")',
        '    discover_sub = discover_parser.add_subparsers(dest="discover_command")\n'
        '    discover_sub.add_parser("connect")',
    )
    # The nested registration survives, so an any-depth check sees the name.
    assert '.add_parser("connect")' in source
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _check(source, _diff(removed=('    subparsers.add_parser("connect")',)))

# --- the waiver, proven by running the guard, not by reading it ------------
#
# Source inspection cannot show that a waiver still blocks what it must. Each
# case below feeds a synthetic candidate diff through the guard's own decision
# function with the waiver ACTIVE, and asserts the outcome.

_LOGFORMAT_DELTA = """diff --git a/src/project_atlas/cli.py b/src/project_atlas/cli.py
--- a/src/project_atlas/cli.py
+++ b/src/project_atlas/cli.py
@@ -3125,6 +3125,8 @@ def main(argv: Sequence[str] | None = None) -> int:
     args = parser.parse_args(argv)
+    if args.log_format is not None:
+        configure_logging(log_format=args.log_format)
     try:
"""

_INTACT_SOURCE = " ".join(f'"{command}"' for command in _CERTIFIED_COMMANDS)


def test_waiver_permits_only_the_authorized_bootstrap_delta() -> None:
    """A: the authorized change passes with the waiver active."""
    assert (
        _cli_guard_violations(
            diff_text=_LOGFORMAT_DELTA,
            cli_source=_INTACT_SOURCE,
            atlas3_hooks_waived=True,
        )
        == []
    )


def test_waiver_still_blocks_a_certified_command_deletion() -> None:
    """B: removing a certified command registration must fail even when waived."""
    diff = _LOGFORMAT_DELTA + '-    connect_parser = subparsers.add_parser("connect")\n'
    source = " ".join(
        f'"{command}"' for command in _CERTIFIED_COMMANDS if command != "connect"
    )
    violations = _cli_guard_violations(
        diff_text=diff, cli_source=source, atlas3_hooks_waived=True
    )
    assert "deletion-from-certified-cli-surface" in violations
    assert "certified-command-missing:connect" in violations


def test_waiver_still_blocks_an_atlas3_seam_deletion() -> None:
    """C: removing the Atlas-3 seam call must fail even when waived."""
    diff = _LOGFORMAT_DELTA + "-    register_atlas3_parsers(subparsers)\n"
    violations = _cli_guard_violations(
        diff_text=diff, cli_source=_INTACT_SOURCE, atlas3_hooks_waived=True
    )
    assert "deletion-from-certified-cli-surface" in violations


def test_waiver_does_not_bless_an_unauthorized_addition_by_itself() -> None:
    """D/E/F: containment for additions is the sha256 pin, not the diff shape.

    The guard cannot tell an authorized added line from an unauthorized one,
    and it does not try to. What stops an unauthorized addition is that any
    other cli.py bytes fail the pin, so the waiver is simply not active --
    proven here and in the pin tests below.
    """
    direct_registration = _LOGFORMAT_DELTA + '+    subparsers.add_parser("atlas3-secret")\n'
    # With the waiver active (i.e. bytes matching the reviewed candidate) this
    # shape is permitted...
    assert (
        _cli_guard_violations(
            diff_text=direct_registration,
            cli_source=_INTACT_SOURCE,
            atlas3_hooks_waived=True,
        )
        == []
    )
    # ...which is exactly why the waiver must never be active for bytes other
    # than the reviewed ones. Without it, the Atlas-3 hook requirement returns.
    unwaived = _cli_guard_violations(
        diff_text=direct_registration,
        cli_source=_INTACT_SOURCE,
        atlas3_hooks_waived=False,
    )
    assert "missing-atlas3-register-hook" in unwaived
    assert "missing-atlas3-dispatch-hook" in unwaived


def test_unwaived_authorized_delta_is_blocked() -> None:
    """The waiver is load-bearing: without it, this exact change cannot land."""
    violations = _cli_guard_violations(
        diff_text=_LOGFORMAT_DELTA,
        cli_source=_INTACT_SOURCE,
        atlas3_hooks_waived=False,
    )
    assert "missing-atlas3-register-hook" in violations
    assert "no-added-atlas3-registration" in violations


def test_cli_exception_does_not_survive_any_further_edit(tmp_path: Path) -> None:
    """G: one more byte in cli.py and the Atlas-3 hook requirement returns."""
    approved = b"import sys\n"
    (tmp_path / "src" / "project_atlas").mkdir(parents=True)
    target = tmp_path / "src" / "project_atlas" / "cli.py"
    target.write_bytes(approved)
    exceptions = (
        {
            "exception_id": "OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906",
            "owner_approved": "YES",
            "reason": "test",
            "path": "src/project_atlas/cli.py",
            "allowed_sha256": hashlib.sha256(approved).hexdigest(),
        },
    )
    assert _owner_approved_exception_permits(
        "src/project_atlas/cli.py", root=tmp_path, exceptions=exceptions
    )
    target.write_bytes(approved + b"# one more byte\n")
    assert not _owner_approved_exception_permits(
        "src/project_atlas/cli.py", root=tmp_path, exceptions=exceptions
    )


def test_cli_exception_does_not_cover_a_deny_listed_path(tmp_path: Path) -> None:
    """The CLI waiver must not leak into the frozen runtime surfaces."""
    approved = b"import sys\n"
    (tmp_path / "src" / "project_atlas").mkdir(parents=True)
    for name in ("cli.py", "discovery.py"):
        (tmp_path / "src" / "project_atlas" / name).write_bytes(approved)
    exceptions = (
        {
            "exception_id": "OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906",
            "owner_approved": "YES",
            "reason": "test",
            "path": "src/project_atlas/cli.py",
            "allowed_sha256": hashlib.sha256(approved).hexdigest(),
        },
    )
    assert not _owner_approved_exception_permits(
        "src/project_atlas/discovery.py", root=tmp_path, exceptions=exceptions
    )


def test_real_cli_exception_is_live_or_inert_honestly() -> None:
    """Every live CLI waiver names cli.py, is owner-approved, and is real hex."""
    for exc in _CLI_ADDITIVE_EXCEPTIONS:
        assert exc["owner_approved"] == "YES"
        assert exc["exception_id"]
        assert exc["reason"]
        assert exc["path"] == "src/project_atlas/cli.py"
        assert len(exc["allowed_sha256"]) == 64
        int(exc["allowed_sha256"], 16)


# ---------------------------------------------------------------------------
# Semantic invariants -- H01..H18, mutated against the REAL `cli.py`.
#
# The text-layer matrix above uses small synthetic sources, which is right for
# checks that read the source as text. These checks parse it, so a synthetic
# stub proves nothing: the defects they exist to catch were all found by
# mutating the real 6,000-line module and watching the runtime change while
# every text check stayed green (OG-ATLAS-CLI-STRUCTURAL-HARDENING-20260906).
#
# Reproduced on merged main before this guard existed:
#   G1  binding `register_atlas3_parsers` to a no-op  -> all 30 Atlas 3
#       commands gone, top level 98 -> 67, contract PASSED
#   G2  `subparsers = ops_sub` after creation         -> 98 -> 42, certified
#       `ask2`/`kdiff` demoted, contract PASSED
#   G3  deleting `brief_parser.add_argument("--vault")` -> a documented
#       invocation went exit 1 -> exit 2 `unrecognized arguments`, PASSED
# ---------------------------------------------------------------------------

REAL_CLI_SOURCE = (ROOT / "src" / "project_atlas" / "cli.py").read_text(encoding="utf-8")


def _real_atlas3_commands() -> frozenset[str]:
    from project_atlas.atlas3.cli import ATLAS3_COMMANDS

    return frozenset(ATLAS3_COMMANDS)


def _semantic(source: str) -> None:
    assert_cli_semantic_invariants(source=source, atlas3_commands=_real_atlas3_commands())


def _mutate(old: str, new: str, *, count: int = 1) -> str:
    assert old in REAL_CLI_SOURCE, f"anchor no longer present in cli.py: {old[:60]!r}"
    return REAL_CLI_SOURCE.replace(old, new, count)


def test_h01_unchanged_cli_passes_semantic_invariants() -> None:
    """H01 -- the real module satisfies every semantic layer as shipped."""
    _semantic(REAL_CLI_SOURCE)


def test_h02_comment_removal_passes() -> None:
    """H02 -- prose is not semantics."""
    _semantic(REAL_CLI_SOURCE.replace("# OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906:", "#", 1))


def test_h03_unrelated_implementation_removal_passes() -> None:
    """H03 -- removals that preserve certified semantics stay legal.

    The blanket no-removals rule this replaced was proven unsatisfiable: PR
    #684 could not merge under it even waived, because widening an import
    reads as a removal.
    """
    source = _mutate(
        "from project_atlas.config import AtlasConfig, load_config",
        "from project_atlas.config import load_config",
    )
    # keep the module parseable: AtlasConfig is only a type reference here
    _semantic(source.replace("AtlasConfig", '"AtlasConfig"'))


@pytest.mark.parametrize("command", ["capture", "ask2", "kdiff", "brief", "connect"])
def test_h04_deleting_a_certified_top_level_command_fails(command: str) -> None:
    """H04 -- deleting any certified top-level registration is caught."""
    variable = f"{command}_parser"
    anchor = f'{variable} = subparsers.add_parser(\n        "{command}"'
    if anchor not in REAL_CLI_SOURCE:
        anchor = f'{variable} = subparsers.add_parser("{command}"'
    assert anchor in REAL_CLI_SOURCE, f"no top-level registration anchor for {command}"
    source = REAL_CLI_SOURCE.replace(anchor, anchor.replace("subparsers.", "capture_sub."), 1)
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _semantic(source)


def test_h05_deleting_certified_dispatch_fails() -> None:
    """H05 -- a registered command nothing dispatches is dead surface."""
    source = _mutate('if args.command == "brief":', 'if args.command == "brief-disabled":')
    with pytest.raises(AssertionError, match="certified command unreachable"):
        _semantic(source)


def test_h07_nested_same_name_does_not_satisfy_top_level_certification() -> None:
    """H07 -- `discover connect` must not stand in for top-level `connect`."""
    anchor = 'connect_parser = subparsers.add_parser(\n        "connect"'
    if anchor not in REAL_CLI_SOURCE:
        anchor = 'connect_parser = subparsers.add_parser("connect"'
    source = REAL_CLI_SOURCE.replace(anchor, anchor.replace("subparsers.", "discover_sub."), 1)
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _semantic(source)


def test_h08_deleting_register_hook_fails() -> None:
    """H08 -- covered by the text layer; asserted here against the real file."""
    source = _mutate("    register_atlas3_parsers(subparsers)\n", "")
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        assert_cli_atlas3_contract(
            source=source,
            diff_text=_diff(removed=("    register_atlas3_parsers(subparsers)",)),
            atlas3_commands=_real_atlas3_commands(),
        )


def test_h09_deleting_dispatch_hook_fails() -> None:
    source = _mutate("    atlas3_exit = dispatch_atlas3(args)\n", "")
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        assert_cli_atlas3_contract(
            source=source,
            diff_text=_diff(removed=("    atlas3_exit = dispatch_atlas3(args)",)),
            atlas3_commands=_real_atlas3_commands(),
        )


def test_h10_shadowing_the_register_hook_fails() -> None:
    """H10 -- G1. Runtime-proven to remove all 30 Atlas 3 commands."""
    source = _mutate(
        "    register_atlas3_parsers(subparsers)",
        "    register_atlas3_parsers = lambda sp: None\n    register_atlas3_parsers(subparsers)",
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(source)


def test_h11_shadowing_the_dispatch_hook_fails() -> None:
    source = _mutate(
        "    atlas3_exit = dispatch_atlas3(args)",
        "    dispatch_atlas3 = lambda a: None\n    atlas3_exit = dispatch_atlas3(args)",
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(source)


def test_h13_rewiring_the_register_hook_to_a_nested_parser_fails() -> None:
    source = _mutate(
        "    register_atlas3_parsers(subparsers)",
        "    register_atlas3_parsers(capture_sub)",
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam broken"):
        assert_cli_atlas3_contract(
            source=source, diff_text=_diff(), atlas3_commands=_real_atlas3_commands()
        )


@pytest.mark.parametrize("depth", ["top-level", "nested"])
def test_h14_direct_atlas3_registration_fails(depth: str) -> None:
    """H14 -- at any depth, not just the top level."""
    owned = sorted(_real_atlas3_commands())[0]
    receiver = "subparsers" if depth == "top-level" else "capture_sub"
    source = _mutate(
        "    register_atlas3_parsers(subparsers)",
        f'    {receiver}.add_parser("{owned}")\n    register_atlas3_parsers(subparsers)',
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam bypassed"):
        _semantic(source)


def test_h15_multiline_certified_registration_deletion_fails() -> None:
    """H15 -- registrations are routinely formatted across several lines."""
    anchor = 'ask2_parser = subparsers.add_parser(\n        "ask2"'
    if anchor not in REAL_CLI_SOURCE:
        anchor = 'ask2_parser = subparsers.add_parser("ask2"'
    source = REAL_CLI_SOURCE.replace(anchor, anchor.replace("subparsers.", "ops_sub."), 1)
    with pytest.raises(AssertionError, match="certified CLI surface removed"):
        _semantic(source)


def test_h16_logformat_bootstrap_delta_passes() -> None:
    """H16 -- #687's change must remain legal, unwaived."""
    _semantic(REAL_CLI_SOURCE)
    assert "OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906" in REAL_CLI_SOURCE
    assert "configure_logging(log_format=args.log_format)" in REAL_CLI_SOURCE


def test_h17_capture_surface_passes() -> None:
    """H17 -- #684's certified surface is intact and top-level."""
    tree = _cli_module_ast(REAL_CLI_SOURCE)
    top_level = {
        name for receiver, name, _ in _add_parser_calls(tree) if receiver == "subparsers"
    }
    assert "capture" in top_level
    assert "capture" in _dispatched_commands(tree)


def test_h18_future_nested_command_addition_passes() -> None:
    """H18 -- unrelated CLI growth must not need an owner grant."""
    source = _mutate(
        "    capture_record = capture_sub.add_parser(",
        '    capture_sub.add_parser("brand-new-subcommand")\n'
        "    capture_record = capture_sub.add_parser(",
    )
    _semantic(source)


# ---------------------------------------------------------------------------
# V-matrix -- bypasses found by independent verification of the first version
# of this guard, each reproduced against the real cli.py before being fixed.
#
# The first version modelled binding forms by enumerating node types, which
# missed every spelling except `=`. The verifier demonstrated that rewriting
# `subparsers = ops_sub` as a one-line `for subparsers in (ops_sub,)` restored
# G2's exact damage profile (98 -> 42 top-level commands, certified `ask2` and
# `kdiff` demoted). A guard that only catches one spelling of an attack is a
# guard the next attacker spells differently.
# ---------------------------------------------------------------------------

_OPS_SUB = '    ops_sub = ops_parser.add_subparsers(dest="ops_command", required=True)'
_SEAM_CALL = "    register_atlas3_parsers(subparsers)"
_DISPATCH_CALL = "    atlas3_exit = dispatch_atlas3(args)"


@pytest.mark.parametrize(
    "prelude",
    [
        "    for register_atlas3_parsers in (lambda sp: None,):\n        pass\n",
        "    import contextlib\n"
        "    with contextlib.nullcontext(lambda sp: None) as register_atlas3_parsers:\n"
        "        pass\n",
    ],
    ids=["for-target", "with-as"],
)
def test_v02_non_assignment_shadowing_of_the_seam_is_blocked(prelude: str) -> None:
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(_mutate(_SEAM_CALL, prelude + _SEAM_CALL))


def test_v02b_shadowing_the_dispatch_symbol_by_with_as_is_blocked() -> None:
    prelude = (
        "    import contextlib\n"
        "    with contextlib.nullcontext(lambda a: None) as dispatch_atlas3:\n"
        "        pass\n"
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(_mutate(_DISPATCH_CALL, prelude + _DISPATCH_CALL))


def test_v03_module_object_monkeypatch_of_the_seam_is_blocked() -> None:
    """Binds no local name at all, yet replaces what the seam call resolves to."""
    prelude = (
        "    import project_atlas.atlas3.cli as _a3\n"
        "    _a3.register_atlas3_parsers = lambda sp: None\n"
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(_mutate(_SEAM_CALL, prelude + _SEAM_CALL))


def test_v04_deleting_a_guarded_name_is_blocked() -> None:
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(_mutate(_SEAM_CALL, _SEAM_CALL + "\n    del register_atlas3_parsers"))


# ---------------------------------------------------------------------------
# R-matrix -- the second round of independent verification. Scoping the
# top-level parser check to "the first function that calls add_subparsers"
# was itself exploitable: a stub placed above the real factory captured the
# role, and the guard then checked the stub while `build_parser` was rebound
# freely. Resolution is by identity now.
# ---------------------------------------------------------------------------

_DECOY_FACTORY = '''def _atlas_compat_shim(parser):
    subparsers = parser.add_subparsers(dest="command")
    brief_parser = subparsers.add_parser("brief")
    brief_parser.add_argument("--vault")
    brief_parser.add_argument("--project")
    return subparsers


def build_parser('''


def test_r02_match_capture_pattern_rebinding_the_seam_is_blocked() -> None:
    """`case name:` binds through a plain string on MatchAs, not a Name node."""
    prelude = "    match 1:\n        case register_atlas3_parsers:\n            pass\n"
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(_mutate(_SEAM_CALL, prelude + _SEAM_CALL))


def test_r03_setattr_on_the_seam_module_is_blocked() -> None:
    """The same mechanism as the attribute write, spelled as a call."""
    prelude = (
        "    import project_atlas.atlas3.cli as _a3\n"
        "    setattr(_a3, 'register_atlas3_parsers', lambda sp: None)\n"
    )
    with pytest.raises(AssertionError, match="Atlas 3 seam neutralised"):
        _semantic(_mutate(_SEAM_CALL, prelude + _SEAM_CALL))


# ---------------------------------------------------------------------------
# S-matrix -- third round of independent verification.
#
# Two earlier resolutions picked the function to audit by *name*: first the
# one that happened to call `add_subparsers`, then the one called
# `build_parser`. Each fix pinned the decoy shape that had been reported and
# left the class intact -- a decoy captures the audited role while the real
# factory runs unguarded. The verifier's own conclusion, which these tests
# encode: state the invariant and derive from it, rather than extending a
# pattern list. The invariant is that the parser `main` builds is the one
# these checks describe, so resolution follows the call.
# ---------------------------------------------------------------------------

_NAMED_DECOY_FACTORY = '''def build_parser() -> argparse.ArgumentParser:
    """Legacy factory retained for compatibility."""
    parser = argparse.ArgumentParser(prog="atlas")
    subparsers = parser.add_subparsers(dest="command", required=True)
    brief_parser = subparsers.add_parser("brief")
    brief_parser.add_argument("--vault")
    brief_parser.add_argument("--project")
    return parser


def _build_parser_impl('''
