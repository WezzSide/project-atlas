"""AS-ORCH-DURABLE-LEASE-PROJECTION-001 — durable read projection of leases.

PRIMARY_GOVERNOR remains the grant/ack source. This file is visibility
and recovery evidence only.

DURABLE_PROJECTION_IS_AUTHORITY = NO
LEASE_GRANT_SOURCE = PRIMARY_GOVERNOR
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from project_atlas.orchestration.autonomy.models import AgentCapability, AgentLease, NodeState
from project_atlas.source_identity import IdentityLockError, ProjectIdentityLock

PACKAGE_ID: Final[Literal["AS-ORCH-DURABLE-LEASE-PROJECTION-001"]] = (
    "AS-ORCH-DURABLE-LEASE-PROJECTION-001"
)
PROJECTION_NAME: Final[str] = "leases.json"
LOCK_NAME: Final[str] = "leases.lock"
RELATIVE_DEFAULT: Final[Path] = Path(".atlas") / "orchestration" / "autonomy"


class ProjectionError(ValueError):
    code = "LEASE_PROJECTION_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ProjectedLease(BaseModel):
    """Durable row. Not a grant. Sequence is logical, not wall-clock authority."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    package_id: str = Field(min_length=1, max_length=128)
    branch: str = Field(min_length=1, max_length=256)
    worktree: str = Field(min_length=1, max_length=256)
    base_pin: str = Field(min_length=40, max_length=40)
    authorized_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    forbidden_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    start_state: NodeState
    # ABANDONED (AS-ORCH-LEASE-RECOVERY-001): a lease released after real,
    # evidence-gated EXHAUSTED FAILURE (see lease_recovery.py), never a
    # success. Deliberately distinct from RELEASED, which every other
    # caller (governor.release_lease(), reap_orphaned_lease_releases())
    # only ever writes AFTER a real completion. AS-LEASE-RELEASED-
    # CERTIFIED-WITNESS-001: ``_originate()`` still must not treat a
    # planted RELEASED row as a CERTIFIED witness by itself -- the
    # durable ``LoopState.completed_lease_ids`` must corroborate the
    # lease_id. Conflating ABANDONED with RELEASED would additionally
    # let an evidence-gated failure release be reconstructed as a
    # certified success. An ABANDONED row is excluded from both
    # active_rows() and the CERTIFIED-witness set, so the node it names
    # simply falls through to _originate()'s ordinary add_node()+
    # mark_ready() path on its next materialization -- a genuine fresh
    # attempt, never a fabricated one.
    status: Literal["ACTIVE", "RELEASED", "ABANDONED"]
    created_sequence: int = Field(ge=1, le=1_000_000)
    # Shared by RELEASED and ABANDONED alike: "the sequence at which this
    # row left ACTIVE" -- not a claim about which of the two it was; read
    # `status` for that.
    released_sequence: int | None = Field(default=None, ge=1, le=1_000_000)
    projection_is_authority: Literal[False] = False

    @field_validator("base_pin")
    @classmethod
    def _pin(cls, value: str) -> str:
        if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("base_pin must be a 40-char lowercase git SHA")
        return value


class ProjectionHonesty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projection_is_authority: Literal[False] = False
    grant_source: Literal["PRIMARY_GOVERNOR"] = "PRIMARY_GOVERNOR"
    ack_source: Literal["PRIMARY_GOVERNOR"] = "PRIMARY_GOVERNOR"
    wall_clock_is_authority: Literal[False] = False


class LeaseProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    package: Literal["AS-ORCH-DURABLE-LEASE-PROJECTION-001"] = PACKAGE_ID
    honesty: ProjectionHonesty = Field(default_factory=ProjectionHonesty)
    leases: tuple[ProjectedLease, ...] = Field(default_factory=tuple, max_length=256)


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _is_regular_file(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


def _store_file(store: Path) -> Path:
    root = store.expanduser().resolve()
    if ".." in Path(PROJECTION_NAME).parts:
        raise ProjectionError("projection path is unsafe", code="PATH_UNSAFE")
    candidate = root / PROJECTION_NAME
    if candidate.is_symlink():
        raise ProjectionError("projection path is a symlink", code="PATH_UNSAFE")
    target = candidate.resolve()
    if not _inside(root, target):
        raise ProjectionError("projection path escapes store", code="PATH_UNSAFE")
    return target


def _write_atomic(target: Path, payload: dict[str, Any]) -> None:
    """Atomically replace the projection without following a planted tmp symlink.

    ORCH-LEASE-SYMLINK-ESCAPE-001: a predictable ``.{name}.tmp`` path that is
    written via Path.write_text will follow a pre-planted symlink and can
    overwrite a foreign store. Exclusive ``O_NOFOLLOW`` create plus a unique
    tmp name keeps the write inside ``target.parent``.
    """
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ProjectionError("projection directory is a symlink", code="PATH_UNSAFE")
    if target.exists() and (target.is_symlink() or not _is_regular_file(target)):
        raise ProjectionError("projection path is not a regular file", code="PATH_UNSAFE")
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    data = encoded.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    fd = -1
    tmp_path: Path | None = None
    try:
        for _attempt in range(8):
            candidate = parent / f".{target.name}.{os.urandom(8).hex()}.tmp"
            try:
                fd = os.open(os.fspath(candidate), flags, 0o644)
                tmp_path = candidate
                break
            except FileExistsError:
                continue
            except OSError as exc:
                if getattr(exc, "errno", None) in {errno.ELOOP, errno.EEXIST}:
                    raise ProjectionError(
                        "temporary lease file is unsafe",
                        code="PATH_UNSAFE",
                    ) from exc
                raise
        if fd < 0 or tmp_path is None:
            raise ProjectionError(
                "could not create exclusive temporary lease file",
                code="PATH_UNSAFE",
            )
        if not _inside(parent.resolve(), tmp_path.resolve()):
            raise ProjectionError("temporary lease path escaped store", code="PATH_UNSAFE")
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                raise OSError("short write to lease projection")
            written += n
        os.fsync(fd)
        os.close(fd)
        fd = -1
        st = os.lstat(tmp_path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            tmp_path.unlink(missing_ok=True)
            tmp_path = None
            raise ProjectionError(
                "temporary lease file is not a regular file",
                code="PATH_UNSAFE",
            )
        os.replace(tmp_path, target)
        tmp_path = None
        final = os.lstat(target)
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            raise ProjectionError(
                "lease projection resolved to a non-regular file",
                code="PATH_UNSAFE",
            )
        if not _inside(parent.resolve(), target.resolve()):
            raise ProjectionError("lease projection escaped store", code="PATH_UNSAFE")
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def load_projection(store: Path) -> LeaseProjection:
    path = _store_file(store)
    if not path.is_file():
        return LeaseProjection()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectionError("projection is unreadable", code="STATE_CORRUPT") from exc
    if not isinstance(raw, dict):
        raise ProjectionError("projection is schema-invalid", code="STATE_CORRUPT")
    try:
        return LeaseProjection.model_validate(raw)
    except Exception as exc:
        raise ProjectionError("projection is schema-invalid", code="STATE_CORRUPT") from exc


def persist_projection(store: Path, projection: LeaseProjection) -> LeaseProjection:
    return _mutate_projection(store, lambda _current: projection)


def _mutate_projection(
    store: Path,
    mutator: Callable[[LeaseProjection], LeaseProjection],
) -> LeaseProjection:
    root = store.expanduser().resolve()
    lock_path = (root / LOCK_NAME).resolve()
    if not _inside(root, lock_path):
        raise ProjectionError("lock path escapes store", code="PATH_UNSAFE")
    try:
        with ProjectIdentityLock(lock_path, wait_seconds=2.0, stale_seconds=30.0):
            updated = mutator(load_projection(store))
            _write_atomic(root / PROJECTION_NAME, updated.model_dump(mode="json"))
    except IdentityLockError as exc:
        raise ProjectionError("projection lock is held", code="CONCURRENT_PROJECTION") from exc
    return updated


def _row_from_lease(lease: AgentLease, *, status: Literal["ACTIVE", "RELEASED"]) -> ProjectedLease:
    return ProjectedLease(
        lease_id=lease.lease_id,
        agent_id=lease.agent_id,
        package_id=lease.package_id,
        branch=lease.branch,
        worktree=lease.worktree,
        base_pin=lease.base_pin,
        authorized_paths=lease.authorized_paths,
        forbidden_paths=lease.forbidden_paths,
        capabilities=tuple(item.value for item in lease.capabilities),
        start_state=lease.start_state,
        status=status,
        created_sequence=lease.sequence,
        released_sequence=None if status == "ACTIVE" else lease.sequence,
        projection_is_authority=False,
    )


def active_rows(projection: LeaseProjection) -> tuple[ProjectedLease, ...]:
    return tuple(row for row in projection.leases if row.status == "ACTIVE")


def reject_stale_base(*, row: ProjectedLease, live_main: str) -> None:
    if row.base_pin != live_main:
        raise ProjectionError("stale lease base_pin rejected", code="STALE_LEASE")


def reject_foreign_worker(*, row: ProjectedLease, agent_id: str) -> None:
    if row.agent_id != agent_id:
        raise ProjectionError("foreign worker rejected", code="FOREIGN_WORKER")


def reject_foreign_package(*, row: ProjectedLease, package_id: str) -> None:
    if row.package_id != package_id:
        raise ProjectionError("foreign package rejected", code="FOREIGN_PACKAGE")


def project_grant(store: Path, lease: AgentLease, *, live_main: str) -> LeaseProjection:
    """Project a governor-issued grant. Does not itself grant authority."""
    if lease.base_pin != live_main:
        raise ProjectionError("stale lease base_pin rejected", code="STALE_LEASE")

    def _apply(current: LeaseProjection) -> LeaseProjection:
        for row in current.leases:
            if row.lease_id != lease.lease_id:
                continue
            if (
                row.status == "ACTIVE"
                and row.agent_id == lease.agent_id
                and row.package_id == lease.package_id
                and row.base_pin == lease.base_pin
            ):
                return current
            raise ProjectionError("lease replay is forbidden", code="LEASE_REPLAY")
        for row in active_rows(current):
            if row.package_id == lease.package_id:
                raise ProjectionError(
                    "duplicate active lease for package",
                    code="DUPLICATE_ACTIVE_LEASE",
                )
            if row.agent_id == lease.agent_id:
                raise ProjectionError(
                    "worker already holds an active lease",
                    code="FOREIGN_WORKER",
                )
        return LeaseProjection(leases=(*current.leases, _row_from_lease(lease, status="ACTIVE")))

    return _mutate_projection(store, _apply)


def _require_still_active(row: ProjectedLease, *, lease_id: str) -> None:
    """Re-check a row's status against the FRESH, lock-time-accurate
    ``current`` a mutator closure receives (loaded inside
    ``_mutate_projection()``'s own lock) -- never a caller-supplied,
    possibly-stale row read earlier and unlocked. Both ``project_release()``
    and ``project_abandon()`` write a lease's terminal status starting
    from an assumption, made by their own caller, that the row is still
    ``ACTIVE``; without this re-check that assumption can go stale in the
    real window between the caller's own unlocked read and this locked
    write, and either function would then silently overwrite whatever the
    row legitimately became in between.

    Independent-verification finding (delta IV, AS-ORCH-LEASE-RECOVERY-001
    owner review round 2): this was added to ``project_abandon()`` alone
    first (a genuine completion racing in and being silently stomped back
    to ``ABANDONED``), and a following delta IV round found the exact
    mirror-image gap was still open in the untouched ``project_release()``
    -- a lease this evidence-gated mechanism had already, legitimately
    marked ``ABANDONED`` could be silently overwritten back to
    ``RELEASED`` by an unrelated, out-of-band completion (e.g. a
    still-alive in-process governor object from before the owning loop's
    ``RESOURCE_BOUNDARY`` stop, independently finishing and calling
    ``governor.release_lease()``) -- fabricating exactly the CERTIFIED
    witness this whole mechanism exists to prevent, in the reverse
    direction. Shared here so both callers enforce the identical
    invariant identically, rather than two independently-maintained
    copies of the same check drifting apart.

    Uses its own, distinct error code (``LEASE_STATUS_RACE``) rather than
    reusing ``lease_recovery.py``'s own, differently-scoped
    ``LEASE_NOT_ACTIVE`` (that one fires on an early, UNLOCKED read,
    before any evidence-gate work even starts; this one fires on the
    locked write itself detecting a genuine concurrent transition) -- a
    second delta-IV finding: conflating the two under one code string
    left a caller keying off ``blocker`` alone unable to distinguish
    "never was active" from "raced with a real concurrent transition".
    """
    if row.status != "ACTIVE":
        raise ProjectionError(
            f"lease {lease_id!r} is no longer ACTIVE (now {row.status!r}) -- "
            "a concurrent transition happened between this call's own "
            "caller-side read and this locked write; refusing to "
            "overwrite it",
            code="LEASE_STATUS_RACE",
        )


def project_release(store: Path, lease: AgentLease, *, live_main: str) -> LeaseProjection:
    def _apply(current: LeaseProjection) -> LeaseProjection:
        rows: list[ProjectedLease] = []
        found = False
        for row in current.leases:
            if row.lease_id != lease.lease_id:
                rows.append(row)
                continue
            found = True
            _require_still_active(row, lease_id=lease.lease_id)
            reject_foreign_worker(row=row, agent_id=lease.agent_id)
            reject_foreign_package(row=row, package_id=lease.package_id)
            reject_stale_base(row=row, live_main=live_main)
            rows.append(
                row.model_copy(
                    update={"status": "RELEASED", "released_sequence": lease.sequence}
                )
            )
        if not found:
            raise ProjectionError("lease not in projection", code="LEASE_UNKNOWN")
        return LeaseProjection(leases=tuple(rows))

    return _mutate_projection(store, _apply)


def project_abandon(store: Path, lease: AgentLease, *, live_main: str) -> LeaseProjection:
    """Sibling of ``project_release()`` for AS-ORCH-LEASE-RECOVERY-001's
    evidence-gated failure path (``lease_recovery.py``) ONLY -- every
    other caller in this codebase releases a lease after a real success
    and must keep calling ``project_release()``, never this. See
    ``ProjectedLease.status``'s own docstring for why the two are not
    interchangeable: ``_originate()``'s CERTIFIED-witness inference
    still reads corroborated ``RELEASED`` rows as completion witnesses,
    and writing that status here would look like a success release.
    Same reject_foreign_worker/reject_foreign_package/
    reject_stale_base defense-in-depth as ``project_release()``.
    """

    def _apply(current: LeaseProjection) -> LeaseProjection:
        rows: list[ProjectedLease] = []
        found = False
        for row in current.leases:
            if row.lease_id != lease.lease_id:
                rows.append(row)
                continue
            found = True
            _require_still_active(row, lease_id=lease.lease_id)
            reject_foreign_worker(row=row, agent_id=lease.agent_id)
            reject_foreign_package(row=row, package_id=lease.package_id)
            reject_stale_base(row=row, live_main=live_main)
            rows.append(
                row.model_copy(
                    update={"status": "ABANDONED", "released_sequence": lease.sequence}
                )
            )
        if not found:
            raise ProjectionError("lease not in projection", code="LEASE_UNKNOWN")
        return LeaseProjection(leases=tuple(rows))

    return _mutate_projection(store, _apply)


def reap_orphaned_lease_releases(
    store: Path, completed_lease_ids: tuple[str, ...]
) -> tuple[str, ...]:
    """Retry ``project_release()`` for every durably-proven-complete lease
    whose projection row is still ``ACTIVE`` (AUTONOMY_PROJECTION_ERROR_
    RECOVERY_BOUNDARY, post-#654 follow-up).

    ``completed_lease_ids`` (``AutonomousLoop.LoopState``) is durable,
    load-bearing proof that a lease's governed work already reached a
    terminal outcome: it is written atomically with the SAME ``phase=
    IDLE`` save that clears ``active_lease_id`` (``_finalize_validated()``
    , loop.py) -- strictly before anything ever attempts to release the
    durable projection row. If that release write is lost to lock
    contention or a crash between those two points, nothing previously
    revisited it: a fresh process's rehydration only restores a lease
    when ``LoopState.phase == LEASED`` (still in progress), and this one
    is already ``IDLE``. The row then stayed ``ACTIVE`` forever,
    permanently excluding its ``package_id`` from rediscovery (see
    ``rehydration._originate()``'s ``known.update(active_ids)``).

    Matching identity, not elapsed time, is the only recovery evidence
    used -- no expiry/TTL semantics are invented. A row is touched only
    when its OWN ``lease_id`` appears in ``completed_lease_ids``,
    reconstructed entirely from that row's own fields (never a live or
    independently-supplied lease object), and released against its OWN
    recorded ``base_pin`` -- never the current live main, since
    finalizing historical bookkeeping for provably-finished work must not
    depend on whether main has since moved (``project_release()``'s own
    ``reject_stale_base`` would otherwise wrongly refuse to release old,
    completed work once main advances). This can never touch a
    different, still-legitimately-``ACTIVE`` lease for the same or any
    other package: that lease's id is not (yet) in
    ``completed_lease_ids`` by construction, so it is never looked up,
    never reconstructed, and never passed to ``project_release()``.

    Idempotent: a row already ``RELEASED`` is left untouched -- safe to
    call on every rehydration, not just once. Transient
    ``CONCURRENT_PROJECTION`` on one row is swallowed and that id is
    simply left for a later invocation to retry -- the same durable proof
    makes that safe, no additional recovery-intent record needs to be
    written. Any other ``ProjectionError`` (``STATE_CORRUPT``, or a
    reconstruction failure) is NOT swallowed: it propagates, matching the
    "fail closed on corruption, never guess" policy this module already
    applies to the LEASED-restore path (``rehydration._restore_leased_
    node()``). A row's own reject_foreign_worker/reject_foreign_package
    checks stay in force as a defense-in-depth self-consistency
    assertion even though this call site always reconstructs from that
    same row -- they still fire if reconstruction and the row somehow
    disagree by the time the write lock is held.

    Returns the lease_ids actually transitioned ACTIVE -> RELEASED by
    this call (empty if nothing needed reaping).
    """
    if not completed_lease_ids:
        return ()
    projection = load_projection(store)  # STATE_CORRUPT propagates, by design
    by_id: dict[str, ProjectedLease] = {}
    for existing_row in projection.leases:
        if existing_row.lease_id in by_id:
            # Codex/Copilot review finding on PR #658: a plain dict
            # comprehension would silently keep only the LAST duplicate
            # and drop the rest -- for a fail-closed-on-corruption
            # function, picking an arbitrary row instead of detecting
            # the corruption is exactly the wrong default.
            raise ProjectionError(
                f"duplicate lease_id {existing_row.lease_id!r} in projection",
                code="STATE_CORRUPT",
            )
        by_id[existing_row.lease_id] = existing_row
    reaped: list[str] = []
    for lease_id in completed_lease_ids:
        row = by_id.get(lease_id)
        # Independent-verification finding (AS-ORCH-LEASE-RECOVERY-001 IV
        # round 2): this used to check only `!= "RELEASED"`, silently
        # treating any OTHER non-ACTIVE status the same as ACTIVE and
        # reconstructing+writing it. Proven repro: a row already
        # ABANDONED (lease_recovery.py's evidence-gated failure path)
        # would be flipped ABANDONED -> RELEASED here -- exactly the
        # CERTIFIED-witness fabrication AS-ORCH-LEASE-RECOVERY-001 exists
        # to prevent, reachable through this sibling function instead.
        # `!= "ACTIVE"` is exhaustive against every current AND future
        # non-active status by construction, not just the ones known
        # when this line was last edited.
        if row is None or row.status != "ACTIVE":
            continue
        try:
            capabilities = tuple(AgentCapability(value) for value in row.capabilities)
        except ValueError as exc:
            raise ProjectionError(
                f"completed lease {lease_id!r} has an unrecognized capability "
                f"value: {exc}",
                code="STATE_CORRUPT",
            ) from exc
        try:
            lease = AgentLease(
                lease_id=row.lease_id,
                agent_id=row.agent_id,
                package_id=row.package_id,
                branch=row.branch,
                worktree=row.worktree,
                base_pin=row.base_pin,
                authorized_paths=row.authorized_paths,
                forbidden_paths=row.forbidden_paths,
                capabilities=capabilities,
                start_state=row.start_state,
                # The only two AgentLease fields the durable projection
                # does not carry -- see leases.py:grant_lease, the sole
                # place an AgentLease is ever minted, and rehydration.py's
                # identical reconstruction for the LEASED-restore path.
                expected_output="EVIDENCE_BUNDLE",
                expiry_or_terminal_condition="UNTIL_NODE_TERMINAL",
                # Codex/Copilot review finding on PR #658: this object is
                # only ever used as a release() payload, never tracked as
                # a live lease anywhere -- active=False (matching
                # leases.release_lease()'s own output shape) says so
                # honestly, reducing the chance a future caller
                # misreads it as still-active. project_release() itself
                # never reads this field either way.
                active=False,
                sequence=row.created_sequence,
            )
        except ValidationError as exc:
            raise ProjectionError(
                f"completed lease {lease_id!r} does not reconstruct into a "
                f"valid AgentLease: {exc}",
                code="STATE_CORRUPT",
            ) from exc
        try:
            project_release(store, lease, live_main=row.base_pin)
        except ProjectionError as exc:
            if exc.code == "CONCURRENT_PROJECTION":
                continue  # retry on a later invocation; nothing is lost
            raise
        reaped.append(lease_id)
    return tuple(reaped)


def visible_active_lease(
    store: Path,
    *,
    lease_id: str,
    agent_id: str,
    package_id: str,
    live_main: str,
) -> ProjectedLease:
    """Read-only ack/visibility. Projection is not a grant."""
    for row in active_rows(load_projection(store)):
        if row.lease_id != lease_id:
            continue
        reject_foreign_worker(row=row, agent_id=agent_id)
        reject_foreign_package(row=row, package_id=package_id)
        reject_stale_base(row=row, live_main=live_main)
        return row
    raise ProjectionError("active lease not visible", code="LEASE_NOT_VISIBLE")
