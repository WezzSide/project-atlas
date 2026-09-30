"""AS-ORCH-LOCAL-DISPATCH-001: governed local-process dispatch port.

Wires ``orchestration.local_process_transport``'s disabled-by-default LOCAL
PROCESS execution primitive (PR-B, ``AS-ORCH-LOCAL-PROC-001``) into
``AutonomousLoop``'s existing ``DispatchPort`` seam (``loop.py``) -- the
same extensibility point the loop already has for any non-``IN_PROCESS``
execution host, previously unused in production (the real CLI entrypoint,
``run_governor_loop_tick()``, constructs ``AutonomousLoop`` with
``dispatch=None``).

Does not create a second governor. Does not bypass ``lease()``'s existing
checks (``READY`` state, dependency satisfaction, owner gate, surface
overlap) -- this port is only ever invoked from ``_dispatch_leased()``,
strictly downstream of an already-granted lease. Does not accept
arbitrary caller-supplied authority: every field in the task envelope
this port builds is read from the DURABLE LEASE PROJECTION record for
the currently active lease, never from a parameter a caller could set.

DISABLED BY DEFAULT: constructing this port requires an already-enabled
``LocalProcessExecutorConfig`` (``config.enabled=True``, PR-B's own
disabled-by-default gate) AND an explicit, non-empty, fixed ``argv_template``
supplied by the operator at construction time -- never derived from the
WorkNode/proposal/lease content itself (a governed node choosing its own
executable would be a code-injection vector). The actual argv a task runs
is always ``(*argv_template, <absolute path to a governed request JSON
file>)`` -- the fixed template decides HOW to interpret that file; this
port never interprets task content itself, and never implies Cursor,
OpenAI, Anthropic, network access, or API billing (mirrors
``local_process_transport.py``'s own non-claims).

ISOLATED PER-ATTEMPT WORKTREE (IV findings, PR #662 review rounds 1-2):
every dispatch attempt runs inside a fresh ``git worktree`` checked out
from the lease's own ``base_pin`` -- never inside the project root's own
working tree, and never reused across attempts. This is the "lease
worktree" pipeline stage the directive's own design names explicitly.
Two independent adversarial IV rounds both rejected an earlier version
of this module that instead ran every attempt directly against the
shared project root and tried to restore/commit it clean between
attempts (``git reset --hard``/``git commit`` against ``root`` itself):
round 1 found that left the shared root permanently dirty after ANY
outcome (blocking every later dispatch, including a legitimate
remediation retry of the very same lease); round 2 found the
restore/commit step's own failure path (a missing git identity, an
embedded ``.git`` inside the changed tree, a task that self-commits)
reproduced the exact same lockout under realistic conditions its own
tests never exercised, AND that auto-committing onto whatever branch
``root`` happened to have checked out bypasses this codebase's own
established ``RESULT_ADAPTER_CAN_AUTHORIZE_MERGE = NO`` contract
(``dispatcher.py``'s ``AS-ORCH-001D-RESULT-BINDING-001``) with no
review/binding gate in between. Per-attempt worktree isolation removes
the entire bug class rather than patching around it again: each
attempt's worktree starts life freshly checked out (so
``local_process_transport.py``'s own ``_require_clean_worktree()``
precondition trivially holds every time, for every attempt, regardless
of what any other attempt or any other lease did), an attempt's
in-scope changes are left as real, inspectable, UNCOMMITTED working-tree
state on its own disposable branch (never committed onto ``root``'s
checked-out branch, never merged, never claiming merge/execution
authority -- a later, separate result-binding step is what would ever
promote them), and the project root's own working tree is never touched
by a dispatch at all. Worktrees are durable evidence, kept (not deleted)
after an attempt finishes -- like every other durable record this
package keeps (receipts, lease projection rows) -- so accumulation over
a long-running governed session is an explicit, disclosed, deferred
cleanup concern for a future reaper (mirroring this package's own
existing release/reaper precedent for leases), not a correctness gap.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from project_atlas.orchestration.autonomy.lease_projection import (
    RELATIVE_DEFAULT as LEASE_PROJECTION_RELATIVE_DEFAULT,
)
from project_atlas.orchestration.autonomy.lease_projection import (
    ProjectedLease,
    active_rows,
    load_projection,
)
from project_atlas.orchestration.local_process_transport import (
    LocalProcessExecutorConfig,
    LocalTaskEnvelope,
    run_local_task,
)

PACKAGE_ID: Final[str] = "AS-ORCH-LOCAL-DISPATCH-001"

#: Where durable dispatch receipts live, relative to the project root --
#: sibling to the lease projection store, same ``.atlas/orchestration/``
#: convention every other durable store in this package already uses.
RECEIPTS_RELATIVE: Final[Path] = Path(".atlas") / "orchestration" / "autonomy" / "local_dispatch"

#: Where per-attempt isolated worktrees are checked out, relative to the
#: project root -- a sibling of the receipts store, same convention.
WORKTREES_RELATIVE: Final[Path] = RECEIPTS_RELATIVE / "worktrees"

_DISPATCH_ID_PREFIX: Final[str] = "local-process:"
_BRANCH_PREFIX: Final[str] = "atlas-local-dispatch/"
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")
#: `_safe_token()` collapses everything outside [A-Za-z0-9._-] to a
#: single "_" -- distinct lease_ids could in principle collapse to the
#: same receipt filename. This module does not itself enforce a
#: narrower charset: it relies on `lease_id` already being constrained
#: upstream by `models.AgentLease`/`lease_projection.ProjectedLease`'s
#: own `ID_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, a strict subset of
#: what `_safe_token` leaves untouched) and, in production, always being
#: auto-generated (`LEASE-{sequence}`), never attacker-supplied.
#: Independent-verification note (AS-ORCH-LEASE-RECOVERY-001 owner
#: review round 2 delta IV): if that upstream pattern is ever loosened,
#: this collapsing becomes a real filename-collision surface again --
#: flagged here as the enforced precondition, not re-validated in this
#: module.


class LocalDispatchError(ValueError):
    """A local-process dispatch could not proceed at all -- a genuine
    protocol/precondition violation (no active lease, ambiguous active
    lease, duplicate dispatch), never an ordinary task-execution failure
    (those are reported as a ``FAILED`` status, not raised)."""

    code: str = "LOCAL_DISPATCH_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class LocalDispatchReceiptStatus(StrEnum):
    """The exact three values ``dispatch_once()`` ever writes to a real
    receipt's ``status`` field (see its own body) -- nothing else is a
    legitimate value, and this is the single source of truth every reader
    validates against, not a value any reader invents independently.

    ``RUNNING`` is written before the child process starts and can be the
    FINAL on-disk state if the whole process crashed mid-attempt -- it is
    real, well-formed, and genuinely unresolved, never terminal-failure
    evidence. ``FAILED`` is the only status a receipt can carry that is
    real, positive proof of a terminal, exhausted failure (an exception
    before/without a result, a non-clean exit, a non-authority-clean
    result, or the supervisor-integrity guard overriding an otherwise-
    passing result back to FAILED). ``COMPLETED`` is written if and only
    if ``exit_code == 0 and authority_clean`` -- production never writes
    ``COMPLETED`` with a false/absent ``authority_clean``, so no MEANING
    is lost by treating ``COMPLETED`` as unconditionally a real success
    signal.
    """

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class LocalDispatchReceipt(BaseModel):
    """Schema for a real, on-disk local-process dispatch receipt.

    AS-ORCH-LEASE-RECOVERY-001 owner review round 2 (2026-09-02): the
    evidence gate that consumes these receipts used to accept anything
    that did NOT positively look like a hidden success (``status ==
    "COMPLETED" and authority_clean is True``) -- a missing ``status``
    field, ``status == "RUNNING"``, an unrecognized status string, or a
    tampered non-bool ``authority_clean`` all silently passed through as
    "not a hidden success", which is not the same claim as "positive
    proof of a real, terminal failure". This model is the fix: every
    receipt ``list_dispatch_receipts()`` returns has ALREADY been
    validated against this exact, real production shape -- a receipt
    that doesn't parse is a malformed/tampered receipt and fails closed
    at the source (``LocalDispatchError``, code ``MALFORMED_RECEIPT``),
    never silently treated as evidence of anything.

    Strict typing (``StrictBool``/``StrictInt``) so a value like the
    string ``"true"`` or the int ``1`` where a real bool is required
    fails validation instead of pydantic's default coercion silently
    accepting it -- a receipt that is not byte-for-byte what
    ``dispatch_once()`` itself would have written is corruption, not a
    liberally-typed input to be normalized.

    ``authority_clean``/``exit_code``/``timed_out``/``error`` are all
    optional: the real exception-path receipt (``dispatch_once()``,
    before a ``LocalExecutionResult`` ever exists) legitimately carries
    only ``dispatch_id``/``lease_id``/``package_id``/``status``/``error``
    -- requiring the others would make that real, valid shape
    unparseable. Extra fields (``changed_paths``, ``violations``,
    ``stdout_digest``, ``stderr_digest``, ``worktree``,
    ``supervisor_integrity_violation``, ...) are real but not needed by
    the evidence gate, so they are ignored rather than modeled here --
    this is deliberately a validation schema for the recovery evidence
    gate's own needs, not a competing full receipt contract.
    """

    model_config = ConfigDict(extra="ignore")

    dispatch_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    status: LocalDispatchReceiptStatus
    authority_clean: StrictBool | None = None
    exit_code: StrictInt | None = None
    timed_out: StrictBool | None = None
    error: str | None = None


def _dispatch_id_for(lease_id: str) -> str:
    return f"{_DISPATCH_ID_PREFIX}{lease_id}"


def _safe_token(dispatch_id: str) -> str:
    # dispatch_id contains ':' (from _dispatch_id_for's prefix), which is
    # not a safe filename character on Windows and not a safe git branch
    # component either -- substitute for filenames/branch names ONLY; the
    # logical dispatch_id string used in every comparison/return value is
    # never altered.
    return _SAFE_TOKEN_RE.sub("_", dispatch_id)


def _receipt_filename(dispatch_id: str) -> str:
    return _safe_token(dispatch_id) + ".json"


def _receipts_dir(root: Path) -> Path:
    resolved_root = root.expanduser().resolve()
    target = (resolved_root / RECEIPTS_RELATIVE).resolve()
    if not target.is_relative_to(resolved_root):
        raise LocalDispatchError("receipt store path escapes project root", code="PATH_UNSAFE")
    return target


def _worktrees_dir(root: Path) -> Path:
    resolved_root = root.expanduser().resolve()
    target = (resolved_root / WORKTREES_RELATIVE).resolve()
    if not target.is_relative_to(resolved_root):
        raise LocalDispatchError(
            "dispatch worktrees store path escapes project root", code="PATH_UNSAFE"
        )
    return target


def _worktree_path(root: Path, dispatch_id: str) -> Path:
    resolved_root = root.expanduser().resolve()
    target = (resolved_root / WORKTREES_RELATIVE / _safe_token(dispatch_id)).resolve()
    if not target.is_relative_to(resolved_root):
        raise LocalDispatchError("dispatch worktree path escapes project root", code="PATH_UNSAFE")
    return target


def _dispatch_branch_name(dispatch_id: str) -> str:
    return f"{_BRANCH_PREFIX}{_safe_token(dispatch_id)}"


def _write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    """Write ``payload`` to ``target`` atomically, WITHOUT ever following a
    symlink planted at the predictable ``.{name}.tmp`` sibling path (IV
    finding, PR #662: a locally-dispatched task can compute both its own
    ``dispatch_id`` and ``root`` from the request JSON it is handed, plant a
    symlink at the exact tmp path this function will use next, exit
    cleanly, and have the SUPERVISOR's own subsequent receipt write follow
    that symlink -- an arbitrary-file-write primitive reachable entirely
    outside the worktree-diff scope check and the supervisor-integrity
    guard). Always unlink whatever currently occupies the tmp path (a
    stale leftover from a prior crashed write, or a planted symlink --
    unlinking a symlink only removes the link, never touches whatever it
    points at) and then create a FRESH regular file with
    O_CREAT|O_EXCL(|O_NOFOLLOW where available), so a symlink planted in
    the narrow window between the unlink and the open is refused rather
    than silently followed (O_EXCL alone fails closed with EEXIST if that
    race is lost, even on platforms -- Windows included -- where
    O_NOFOLLOW is unavailable/0). The final ``tmp.replace(target)`` never
    follows a symlink at ``target`` either -- rename() replaces the
    directory entry itself, not whatever it points at, so even a
    symlinked ``target`` is safely replaced with a fresh regular file
    rather than having its link target's content clobbered.

    Any OTHER ``OSError`` along this path -- the predictable tmp path
    obstructed by a directory or a permission-locked file, a disk-full
    write, anything else that isn't the two cases handled above -- is
    converted to ``LocalDispatchError(code="RECEIPT_WRITE_BLOCKED")``
    rather than left to escape as a raw, unwrapped exception (IV finding,
    PR #662 fresh IV round 3: a raw OSError here is not in
    ``run_governor_loop_tick()``'s catch tuple, so it would crash the
    real CLI entrypoint ungracefully instead of the clean fail-closed
    JSON response every other genuine protocol violation in this module
    already gets).
    """
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        # IV finding, PR #662 fresh IV round 4: this mkdir() previously sat
        # OUTSIDE the try/except OSError block below, so an obstructed
        # ancestor path (e.g. a plain file where a directory component of
        # `target.parent` is expected) raised a raw, unwrapped OSError --
        # reachable on dispatch_once()'s very first durable write, before
        # its own try/except Exception block even begins, with nothing
        # upstream (loop.py's _dispatch_leased() explicitly does not wrap
        # this call) to catch it either. Moved inside so it is covered by
        # the same fail-closed conversion as every other OSError here.
        target.parent.mkdir(parents=True, exist_ok=True)
        with suppress(FileNotFoundError):
            tmp.unlink()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(encoded)
        except BaseException:
            with suppress(FileNotFoundError):
                tmp.unlink()
            raise
        tmp.replace(target)
    except OSError as exc:
        raise LocalDispatchError(
            f"atomic write to {target} could not proceed ({exc.__class__.__name__}) -- "
            "failing closed rather than letting an unhandled OSError escape this "
            "module's fail-closed boundary",
            code="RECEIPT_WRITE_BLOCKED",
        ) from exc


def _read_receipt(root: Path, dispatch_id: str) -> dict[str, object] | None:
    """``None`` means "no receipt was ever written for this dispatch_id" --
    the ONLY case callers may treat as "this attempt slot is free"/"nothing
    is known yet". Anything else that stops this from returning a real
    decoded receipt (unreadable file, invalid JSON, a non-object JSON
    value) is a genuine integrity problem -- fail closed with
    ``LocalDispatchError`` rather than silently conflating "corrupt/
    tampered receipt" with "no dispatch ever happened" (IV finding, PR
    #662: the prior version returned ``None`` for both, which would let
    ``dispatch_once()`` silently reuse/overwrite an attempt slot whose
    receipt was corrupted or maliciously planted, and would let
    ``recover()``/``find_active_dispatch_id()`` silently lose track of a
    real in-flight or completed attempt)."""
    path = _receipts_dir(root) / _receipt_filename(dispatch_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalDispatchError(
            f"dispatch receipt for {dispatch_id!r} exists but could not be read "
            f"({exc.__class__.__name__}) -- refusing to treat a corrupt/inaccessible "
            "receipt as though no dispatch ever happened for this attempt",
            code="CORRUPT_RECEIPT",
        ) from exc
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalDispatchError(
            f"dispatch receipt for {dispatch_id!r} is not valid JSON -- failing "
            "closed rather than treating it as though this attempt never happened",
            code="CORRUPT_RECEIPT",
        ) from exc
    if not isinstance(decoded, dict):
        raise LocalDispatchError(
            f"dispatch receipt for {dispatch_id!r} did not decode to a JSON object "
            "-- failing closed rather than treating it as though this attempt never happened",
            code="CORRUPT_RECEIPT",
        )
    return decoded


def _write_receipt(root: Path, dispatch_id: str, payload: dict[str, object]) -> None:
    path = _receipts_dir(root) / _receipt_filename(dispatch_id)
    _write_json_atomic(path, payload)


def _single_active_lease(root: Path) -> ProjectedLease:
    """The one active lease this dispatch is for.

    Mirrors ``DispatchPort.find_active_dispatch_id()``'s own documented
    assumption that the underlying dispatch slot is a single GLOBAL slot,
    not per-lease (see ``loop.py``'s ``DispatchPort`` docstring) -- this
    port makes that assumption explicit and fails closed rather than
    guessing when it does not hold, exactly the same posture the existing
    001D dispatch subsystem already has.
    """
    store = root / LEASE_PROJECTION_RELATIVE_DEFAULT
    projection = load_projection(store)
    active = active_rows(projection)
    if not active:
        raise LocalDispatchError("no active lease found to dispatch", code="NO_ACTIVE_LEASE")
    if len(active) > 1:
        raise LocalDispatchError(
            "more than one active lease found -- cannot determine which to dispatch",
            code="AMBIGUOUS_ACTIVE_LEASE",
        )
    return active[0]


def _request_payload(lease: ProjectedLease, *, dispatch_id: str) -> dict[str, object]:
    """Informational content for the launched process to read -- never
    itself a source of authority (argv/authorized_paths/forbidden_paths
    are set on the LocalTaskEnvelope directly from the SAME lease
    fields, independent of whether the launched process reads or honors
    this file at all)."""
    return {
        "schema_version": 1,
        "package_id": "AS-ORCH-LOCAL-DISPATCH-001",
        "dispatch_id": dispatch_id,
        "lease_id": lease.lease_id,
        "work_id": lease.package_id,
        "base_pin": lease.base_pin,
        "worktree": lease.worktree,
        "authorized_paths": list(lease.authorized_paths),
        "forbidden_paths": list(lease.forbidden_paths),
        "merge_authorized": False,
        "execution_authorized": False,
    }


def _run_git(args: list[str], *, cwd: Path, timeout_seconds: int = 30) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout_seconds
    )


def _supervisor_git_status(root: Path) -> str:
    """The supervisor checkout's own tracked-file status -- must read
    IDENTICAL before and after every dispatch attempt. An isolated
    per-attempt worktree (see module docstring) gives GIT-level
    isolation (its own branch/index), never OS-level sandboxing:
    nothing stops a launched process from using an absolute or
    ``../``-traversal path to reach back into ``root``'s own checkout
    regardless of its own cwd. This is the supervisor-side half of that
    check (tracked files); see ``_protected_state_digest`` for the
    other half (the gitignored durable governance state)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def _protected_state_digest(root: Path, *, worktrees_dir: Path) -> str:
    """Content digest of every file under ``.atlas/orchestration/`` --
    the durable governance state (lease projection, loop state,
    origination projection, and -- as of the receipts-blind-spot fix
    below -- every dispatch's own receipts too) a dispatch attempt must
    never be able to touch -- EXCLUDING only this port's own per-attempt
    WORKTREE checkouts (``worktrees_dir``), whose content changing during
    a live attempt is expected and legitimate. ``.atlas/orchestration/``
    is gitignored, so ``_supervisor_git_status`` alone never sees a
    mutation here -- this is what closes that blind spot for the SAME
    class of attack (an escape via an absolute/traversal path from inside
    the isolated worktree, or from a completely unrelated concurrent
    process).

    Receipts ARE now covered (IV finding, PR #662 fresh IV round: the
    prior version excluded the ENTIRE receipts store -- which nests
    ``WORKTREES_RELATIVE`` inside ``RECEIPTS_RELATIVE`` -- so a dispatched
    task tampering with a receipt file, its own or another lease's, went
    completely undetected by this guard). This is safe to do unguarded:
    every call site in this module snapshots "before" AFTER this
    dispatch's own RUNNING receipt and request-JSON writes, and checks
    "after" BEFORE this dispatch's own final receipt write (see
    ``dispatch_once()``), so this dispatch's own legitimate receipt
    writes never fall inside the guarded window at all.
    """
    orchestration_dir = (root / ".atlas" / "orchestration").resolve()
    if not orchestration_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    resolved_worktrees_dir = worktrees_dir.resolve()
    hasher = hashlib.sha256()
    for path in sorted(orchestration_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.resolve().is_relative_to(resolved_worktrees_dir):
            continue
        hasher.update(path.relative_to(orchestration_dir).as_posix().encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _supervisor_integrity_violation(
    root: Path, *, worktrees_dir: Path, status_before: str, state_before: str
) -> str | None:
    """Re-snapshot ``root`` and compare against the "before" snapshot a
    caller captured earlier -- returns a short, human-readable violation
    description if anything outside this dispatch's own worktree
    changed, ``None`` if the supervisor checkout is confirmed
    byte-for-byte unchanged. See ``_supervisor_git_status``/
    ``_protected_state_digest`` docstrings for what each half covers."""
    status_after = _supervisor_git_status(root)
    state_after = _protected_state_digest(root, worktrees_dir=worktrees_dir)
    if status_before == status_after and state_before == state_after:
        return None
    parts = []
    if status_before != status_after:
        parts.append("tracked-file status changed")
    if state_before != state_after:
        parts.append("governance state under .atlas/orchestration/ changed")
    return "supervisor checkout was not byte-clean throughout the attempt: " + ", ".join(parts)


def _create_dispatch_worktree(root: Path, *, dispatch_id: str, base_pin: str) -> Path:
    """Check out a fresh, disposable ``git worktree`` for exactly one
    dispatch attempt, on its own never-reused branch, starting at the
    lease's own ``base_pin`` -- never at whatever ``root`` currently has
    checked out. Never reused across attempts: a fresh dispatch_id (this
    port's own attempt-indexing, unchanged) always means a fresh branch
    name and a fresh worktree path, so there is no stale state from a
    prior attempt to reset or clean -- the checkout itself already start
    from ``local_process_transport.py``'s own required clean baseline.

    Raises (via the underlying ``git`` command's non-zero exit,
    surfaced as ``subprocess.CalledProcessError``) if the branch or path
    somehow already exists, or ``base_pin`` does not resolve -- both
    genuine, real failures this function never swallows; the caller
    treats this the same as any other dispatch-attempt failure.
    """
    resolved_root = root.expanduser().resolve()
    path = _worktree_path(resolved_root, dispatch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    branch = _dispatch_branch_name(dispatch_id)
    _run_git(
        ["worktree", "add", "-b", branch, str(path), base_pin],
        cwd=resolved_root,
        timeout_seconds=60,
    )
    return path


class LocalProcessDispatchPort:
    """``loop.py.DispatchPort`` implementation backed by
    ``local_process_transport.run_local_task()``.

    Every authority-relevant field on the ``LocalTaskEnvelope`` this port
    builds (``authorized_paths``, ``forbidden_paths``) comes from the
    durable lease projection record for the currently active lease --
    never from ``argv_template`` (fixed, operator-supplied, applies
    uniformly to every dispatch this port instance ever performs) and
    never from anything a WorkNode/proposal/task content could influence.
    """

    def __init__(
        self,
        *,
        config: LocalProcessExecutorConfig,
        argv_template: tuple[str, ...],
    ) -> None:
        if not config.enabled:
            raise LocalDispatchError(
                "LocalProcessDispatchPort requires an already-enabled "
                "LocalProcessExecutorConfig -- refusing to construct a port "
                "that could never actually dispatch anything, which would "
                "silently mask a misconfiguration rather than fail loudly "
                "at startup",
                code="LOCAL_EXECUTION_DISABLED",
            )
        if not argv_template:
            raise LocalDispatchError(
                "argv_template must be a non-empty, explicit, operator-"
                "supplied command -- never inferred",
                code="ARGV_TEMPLATE_REQUIRED",
            )
        self._config = config
        self._argv_template = argv_template

    def dispatch_once(self, root: Path) -> dict[str, object]:
        resolved_root = root.expanduser().resolve()
        lease = _single_active_lease(resolved_root)
        # Attempt-indexed dispatch_id, not one fixed id per lease: the
        # existing governed remediation cycle (governor.py's
        # complete_verification()/remediate_and_resume(), unchanged by
        # this PR) legitimately re-dispatches the SAME lease again after a
        # genuine, already-resolved failure (up to
        # MAX_AUTONOMOUS_REMEDIATION_CYCLES times) -- that is a real,
        # governed retry, not a duplicate. loop.py's own
        # `completed_dispatch_ids` guard also requires each dispatch to
        # have its own never-reused id regardless. Only a dispatch whose
        # PRIOR attempt receipt is still genuinely unresolved (status
        # RUNNING -- a real overlapping/concurrent re-dispatch, or a
        # caller bug that never awaited the first one) is refused.
        attempt = 0
        while True:
            dispatch_id = f"{_dispatch_id_for(lease.lease_id)}:{attempt}"
            existing = _read_receipt(resolved_root, dispatch_id)
            if existing is None:
                break
            if existing.get("status") == "RUNNING":
                raise LocalDispatchError(
                    f"dispatch {dispatch_id!r} is still unresolved -- refusing "
                    "to start an overlapping dispatch for the same lease",
                    code="DUPLICATE_DISPATCH",
                )
            attempt += 1
        # Written BEFORE the (potentially long-running) child process
        # starts, so a crash of THIS process mid-run leaves a durable,
        # positively-checkable "something was dispatched for this lease"
        # marker -- find_active_dispatch_id()/recover() rely on this
        # existing, never on inferring anything from the child's own
        # aliveness.
        _write_receipt(
            resolved_root,
            dispatch_id,
            {
                "dispatch_id": dispatch_id,
                "lease_id": lease.lease_id,
                "package_id": lease.package_id,
                "status": "RUNNING",
            },
        )
        request_path = _receipts_dir(resolved_root) / f"{_safe_token(dispatch_id)}-request.json"
        _write_json_atomic(request_path, _request_payload(lease, dispatch_id=dispatch_id))

        # Supervisor-checkout integrity guard (explicit adversarial
        # requirement, PR #662): captured AFTER this dispatch's own
        # legitimate RUNNING-receipt and request-JSON writes above and
        # compared again once the attempt finishes, BEFORE this
        # dispatch's own final receipt write -- so those legitimate
        # writes never fall inside the guarded window, while every OTHER
        # receipt (this dispatch's prior attempts, other leases') and
        # every other governance file IS protected (only this dispatch's
        # own isolated worktree, under `worktrees_dir`, is excluded).
        # Isolated-worktree containment is a git-level property only;
        # this is the independent, filesystem-level check that
        # `resolved_root` itself -- tracked files AND the gitignored
        # durable governance state alike -- stayed byte-for-byte
        # identical throughout, regardless of what the attempt did
        # inside its own worktree or attempted via an absolute/traversal
        # path.
        worktrees_dir = _worktrees_dir(resolved_root)
        guard_status_before = _supervisor_git_status(resolved_root)
        guard_state_before = _protected_state_digest(resolved_root, worktrees_dir=worktrees_dir)

        try:
            worktree_path = _create_dispatch_worktree(
                resolved_root, dispatch_id=dispatch_id, base_pin=lease.base_pin
            )
            envelope = LocalTaskEnvelope(
                work_id=lease.package_id,
                # An ABSOLUTE path -- the child process's cwd is the
                # isolated worktree, a different directory than
                # `resolved_root`, so a root-relative path (this port's
                # pre-isolation design) would not resolve there.
                argv=(*self._argv_template, str(request_path)),
                authorized_paths=lease.authorized_paths,
                forbidden_paths=lease.forbidden_paths,
            )
            result = run_local_task(envelope, self._config, project_root=worktree_path)
        except Exception as exc:
            # dispatch_once() must never let an ordinary task-execution
            # failure escape as an uncaught exception -- loop.py's
            # _dispatch_leased() does not wrap this call in a try/except,
            # so an uncaught exception here would crash the whole tick
            # rather than cleanly finalize the node as failed. Genuine
            # protocol violations (no active lease, duplicate dispatch)
            # are raised ABOVE this point, before any process starts;
            # everything past that point -- including a failure to even
            # create the isolated worktree -- is treated as this
            # dispatch's own, cleanly-reported outcome. The guard is
            # still checked here: a crash partway through must not give
            # a launched process a free pass on the same integrity
            # requirement a clean completion is held to.
            failure_payload: dict[str, object] = {
                "dispatch_id": dispatch_id,
                "lease_id": lease.lease_id,
                "package_id": lease.package_id,
                "status": "FAILED",
                "error": str(exc),
            }
            guard_violation = _supervisor_integrity_violation(
                resolved_root,
                worktrees_dir=worktrees_dir,
                status_before=guard_status_before,
                state_before=guard_state_before,
            )
            if guard_violation is not None:
                failure_payload["supervisor_integrity_violation"] = guard_violation
            _write_receipt(resolved_root, dispatch_id, failure_payload)
            return {"dispatch_id": dispatch_id, "status": "FAILED", "digest": dispatch_id}

        passed = result.exit_code == 0 and result.authority_clean
        # No commit/restore step against `resolved_root` needed here (IV
        # findings, PR #662 review rounds 1-2, see module docstring) --
        # the attempt ran entirely inside its own isolated worktree,
        # which is left exactly as the attempt produced it (an accepted
        # result's in-scope changes stay real, inspectable, UNCOMMITTED
        # working-tree state on the attempt's own disposable branch,
        # never touching `resolved_root`'s own working tree at all) --
        # so `resolved_root` itself is never dirtied by any dispatch,
        # and no subsequent dispatch (a legitimate remediation retry of
        # this SAME lease, or a completely unrelated lease) can ever be
        # blocked by a prior attempt's leftover state.
        worktree_relative = worktree_path.relative_to(resolved_root).as_posix()
        receipt: dict[str, object] = {
            "dispatch_id": dispatch_id,
            "lease_id": lease.lease_id,
            "package_id": lease.package_id,
            "status": "COMPLETED" if passed else "FAILED",
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "authority_clean": result.authority_clean,
            "changed_paths": list(result.changed_paths),
            "violations": [{"path": v.path, "reason": v.reason} for v in result.violations],
            "stdout_digest": result.stdout_digest,
            "stderr_digest": result.stderr_digest,
            "worktree": worktree_relative,
        }
        guard_violation = _supervisor_integrity_violation(
            resolved_root,
            worktrees_dir=worktrees_dir,
            status_before=guard_status_before,
            state_before=guard_state_before,
        )
        if guard_violation is not None:
            # An accepted result the supervisor's own checkout integrity
            # cannot vouch for is not safe to certify, regardless of
            # what the isolated worktree itself reported.
            passed = False
            receipt["status"] = "FAILED"
            receipt["supervisor_integrity_violation"] = guard_violation
        _write_receipt(resolved_root, dispatch_id, receipt)
        return {
            "dispatch_id": dispatch_id,
            "status": "COMPLETED" if passed else "FAILED",
            "digest": dispatch_id,
        }

    def recover(self, root: Path, dispatch_id: str) -> dict[str, object]:
        receipt = _read_receipt(root, dispatch_id)
        if receipt is None:
            # Should not normally happen -- dispatch_once() always writes
            # a RUNNING receipt before loop.py could ever have learned
            # this dispatch_id. Treated as "cannot confirm completion",
            # the same non-committal default CallableDispatchPort itself
            # falls back to -- never guessed as COMPLETED/FAILED.
            return {"dispatch_id": dispatch_id, "status": "RUNNING"}
        return {
            "dispatch_id": dispatch_id,
            "status": str(receipt.get("status", "RUNNING")),
            "digest": dispatch_id,
        }

    def find_active_dispatch_id(self, root: Path, *, lease_id: str) -> str | None:
        # The MOST RECENT attempt for this lease -- the one a crash could
        # plausibly have interrupted before the loop's own state learned
        # its id. Earlier, already-superseded attempts (a completed
        # remediation retry) are not "active"; this returns None only when
        # NO attempt at all has ever been recorded for this lease.
        base = _dispatch_id_for(lease_id)
        attempt = 0
        found: str | None = None
        while True:
            candidate = f"{base}:{attempt}"
            if _read_receipt(root, candidate) is None:
                break
            found = candidate
            attempt += 1
        return found


_ATTEMPT_SUFFIX_RE = re.compile(r"^(\d+)\.json$")


def list_dispatch_receipts(
    root: Path,
    *,
    lease_id: str,
    expected_package_id: str | None = None,
) -> tuple[LocalDispatchReceipt, ...]:
    """Every real, already-persisted receipt recorded for ``lease_id``, in
    attempt order, each validated against ``LocalDispatchReceipt`` --
    the real production schema -- AND bound to the durable slot it was
    read from. Read-only -- the exact same underlying receipt files
    ``recover()``/``find_active_dispatch_id()`` already trust, via the
    same ``_read_receipt()`` (so a corrupt/tampered receipt fails closed
    here too, never silently treated as "no attempt"). Returns an empty
    tuple only when NO attempt was ever recorded for this lease; never
    guesses or fabricates a receipt.

    Independent-verification finding (AS-ORCH-LEASE-RECOVERY-001 IV round
    2): the original version scanned attempts ``0, 1, 2, ...`` and
    stopped at the first missing index. If an intermediate receipt file
    were ever lost (deleted, a future reaper, disk corruption), a LATER
    attempt's receipt -- including a genuine, authority-clean COMPLETED
    success -- would be silently invisible to this function, and
    therefore to ``lease_recovery``'s evidence gate. This lists the real
    directory instead of assuming contiguity, so a gap can never hide a
    later receipt; every attempt actually present on disk is read and
    returned, sorted by attempt number.

    Owner review round 2 (2026-09-02): a syntactically-valid-JSON receipt
    is not the same claim as a real, well-formed, self-consistent
    receipt. Every entry returned here has been positively validated
    (fails closed with ``LocalDispatchError`` otherwise, never silently
    dropped or silently accepted) against three independent properties:
    real schema (``LocalDispatchReceipt`` -- required fields present,
    ``status`` one of the three real values, strictly-typed
    ``authority_clean``), slot identity (the receipt's own
    ``dispatch_id`` field must equal the dispatch_id implied by the
    durable slot/filename it was actually read from -- a receipt cannot
    claim to be a different attempt than the one it was found at), and
    lease identity (the receipt's own ``lease_id`` must equal the
    ``lease_id`` this call was made for). ``expected_package_id``, when
    supplied, additionally binds the receipt's ``package_id`` to the
    caller's own independently-sourced expectation (e.g. the governing
    lease projection row) -- optional because not every caller has that
    context, never skipped when a caller does.

    AS-ORCH-LEASE-RECOVERY-001: this is the evidence source
    ``lease_recovery.release_stalled_lease_after_exhausted_dispatch()``
    reads before it will release a lease its owning loop got permanently
    stuck on -- see that module's docstring for the incident that makes
    real, on-disk receipts (not a caller's assertion) the only acceptable
    evidence.
    """
    prefix = _safe_token(_dispatch_id_for(lease_id)) + "_"
    receipts_dir = _receipts_dir(root)
    attempts: dict[int, str] = {}
    # Reviewer finding (GitHub Copilot, PR #673) plus a follow-up finding
    # from delta IV round 2 (HIGH) on the first fix attempt:
    #
    # `int("05") == int("5")`, so a non-canonical (e.g. zero-padded)
    # filename can name the same attempt as a canonical one, or an
    # attempt number no other file claims. The first fix only checked
    # for the former (two filenames colliding on one integer) by
    # assigning into a dict keyed by that integer -- a lone non-canonical
    # file naming an UNCLAIMED attempt never collided with anything, so
    # it was recorded as "present" during this scan, then silently
    # vanished at READ time below: `_read_receipt()` reconstructs and
    # looks up only the CANONICAL filename for `attempt_number`, gets
    # `FileNotFoundError`, and a bare `continue` dropped it with no
    # error. Reproduced end to end through the real CLI: a genuine
    # COMPLETED/authority_clean=true receipt stored only under a
    # non-canonical name was invisible to the evidence gate, and the
    # lease was abandoned anyway -- exactly the hidden-success
    # fabrication this whole mechanism exists to prevent.
    #
    # Requiring the discovered suffix text to equal `str(attempt_number)`
    # exactly (i.e. refusing any non-canonical filename outright, fail
    # closed, at THIS scan step) closes both the collision shape and the
    # unclaimed-attempt shape at once, and makes read-time loss
    # structurally unreachable rather than just less likely: every entry
    # that reaches `attempts` below has, by construction, the one
    # filename `_read_receipt()` will look up. Sorted iteration also
    # makes which file is reported first deterministic, matching
    # `iterdir()`'s own non-guaranteed order otherwise being a second,
    # independent source of nondeterminism this fix removes as a
    # byproduct.
    entries: list[Path] = []
    if receipts_dir.is_dir():
        entries = sorted(receipts_dir.iterdir(), key=lambda item: item.name)
    for entry in entries:
        if not entry.is_file() or not entry.name.startswith(prefix):
            continue
        match = _ATTEMPT_SUFFIX_RE.match(entry.name[len(prefix) :])
        if match is None:
            continue  # not an attempt receipt (e.g. the sibling "-request.json")
        suffix_text = match.group(1)
        attempt_number = int(suffix_text)
        if suffix_text != str(attempt_number):
            raise LocalDispatchError(
                f"lease {lease_id!r} has a receipt filename "
                f"({entry.name!r}) whose attempt suffix ({suffix_text!r}) "
                f"is not the canonical form of attempt {attempt_number} -- "
                "a non-canonical (e.g. zero-padded) attempt filename is "
                "refused rather than silently excluded",
                code="NONCANONICAL_ATTEMPT_FILENAME",
            )
        # Unreachable in practice once the check above holds (the
        # canonical decimal string is unique per integer, so two
        # DIFFERENT canonical filenames can never share an
        # `attempt_number`) -- kept as a defense-in-depth backstop, not
        # dead code removed on a purely theoretical argument: if the
        # canonical-form rule above is ever loosened or refactored, this
        # still fails closed instead of silently reintroducing the
        # original collision shape.
        if attempt_number in attempts:
            raise LocalDispatchError(
                f"lease {lease_id!r} has two different receipt filenames "
                f"that both name attempt {attempt_number} -- a duplicate "
                "attempt filename is refused rather than silently keeping "
                "one and discarding the other's evidence",
                code="DUPLICATE_ATTEMPT_FILENAME",
            )
        attempts[attempt_number] = f"{_dispatch_id_for(lease_id)}:{attempt_number}"
    receipts: list[LocalDispatchReceipt] = []
    for attempt in sorted(attempts):
        expected_dispatch_id = attempts[attempt]
        raw = _read_receipt(root, expected_dispatch_id)
        if raw is None:
            continue
        try:
            parsed = LocalDispatchReceipt.model_validate(raw)
        except ValidationError as exc:
            raise LocalDispatchError(
                f"dispatch receipt at slot {expected_dispatch_id!r} does not "
                "match the real receipt schema -- refusing to treat a "
                f"malformed/incomplete receipt as evidence of anything: {exc}",
                code="MALFORMED_RECEIPT",
            ) from exc
        if parsed.dispatch_id != expected_dispatch_id:
            raise LocalDispatchError(
                f"receipt at slot {expected_dispatch_id!r} internally claims "
                f"dispatch_id {parsed.dispatch_id!r} -- its own identity "
                "disagrees with the durable slot it was actually read from",
                code="RECEIPT_SLOT_IDENTITY_MISMATCH",
            )
        if parsed.lease_id != lease_id:
            raise LocalDispatchError(
                f"receipt {expected_dispatch_id!r} belongs to lease "
                f"{parsed.lease_id!r}, not the requested {lease_id!r}",
                code="RECEIPT_LEASE_MISMATCH",
            )
        if expected_package_id is not None and parsed.package_id != expected_package_id:
            raise LocalDispatchError(
                f"receipt {expected_dispatch_id!r} belongs to package "
                f"{parsed.package_id!r}, not the expected {expected_package_id!r}",
                code="RECEIPT_PACKAGE_MISMATCH",
            )
        receipts.append(parsed)
    return tuple(receipts)


__all__ = [
    "PACKAGE_ID",
    "RECEIPTS_RELATIVE",
    "WORKTREES_RELATIVE",
    "LocalDispatchError",
    "LocalDispatchReceipt",
    "LocalDispatchReceiptStatus",
    "LocalProcessDispatchPort",
    "list_dispatch_receipts",
]
