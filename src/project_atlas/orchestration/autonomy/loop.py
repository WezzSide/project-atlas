"""AS-ORCH-001E persistent autonomous loop above the landed 001D dispatcher.

AUTONOMOUS_EXECUTION != AUTONOMOUS_OWNER_AUTHORITY
LOOP_CAN_SELECT_READY_WORK = YES
LOOP_CAN_LEASE_AUTHORIZED_WORK = YES
LOOP_CAN_DISPATCH_AUTHORIZED_WORK = YES
LOOP_CAN_BYPASS_OWNER_GATE = NO
LOOP_CAN_AUTHORIZE_MERGE = NO
LOOP_CAN_GRANT_WAIVER = NO
LOOP_CAN_EXPAND_OBJECTIVE = NO

One tick performs at most one 001D dispatch. The loop never grants
owner authority and never auto-merges.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_atlas.orchestration.autonomy.continuation import select_next
from project_atlas.orchestration.autonomy.dag import IllegalTransitionError
from project_atlas.orchestration.autonomy.evidence import hash_payload
from project_atlas.orchestration.autonomy.governor import AutonomousGovernor, GovernorError
from project_atlas.orchestration.autonomy.lease_projection import ProjectionError
from project_atlas.orchestration.autonomy.leases import expand_lease
from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    ExecutionHostClass,
    NodeState,
    OwnerGateKind,
    StopReason,
    TrustedAnchorRecord,
)
from project_atlas.orchestration.autonomy.owner_gates import OwnerGateError, require_owner
from project_atlas.orchestration.autonomy.trust import (
    evaluate_target_moved,
    repository_identities_match,
    require_full_pin,
)
from project_atlas.source_identity import IdentityLockError, ProjectIdentityLock

LOOP_PACKAGE_ID: Final[Literal["AS-ORCH-001E"]] = "AS-ORCH-001E"
LOOP_DIRECTIVE_ID: Final[Literal["D-AS-ORCH-001D-OWNER-MERGE-010"]] = (
    "D-AS-ORCH-001D-OWNER-MERGE-010"
)
MAX_TICKS_PER_INVOCATION: Final[int] = 8
STATE_DIR_RELATIVE: Final[Path] = Path(".atlas") / "orchestration" / "loop"
CURRENT_NAME: Final[str] = "current.json"
LOCK_NAME: Final[str] = ".loop.lock"
PLACEHOLDER_DIGEST: Final[str] = "0" * 64


class LoopError(ValueError):
    """Fail-closed loop error. Not an authority grant."""

    code = "LOOP_FAILED_CLOSED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class LoopPhase(StrEnum):
    IDLE = "IDLE"
    LEASED = "LEASED"
    DISPATCHING = "DISPATCHING"
    AWAITING_RESULT = "AWAITING_RESULT"
    VALIDATING = "VALIDATING"
    STOPPED = "STOPPED"
    FAILED_CLOSED = "FAILED_CLOSED"


class DispatchPort(Protocol):
    """001D dispatch boundary. The loop does not start processes itself."""

    def dispatch_once(self, root: Path) -> dict[str, object]:
        """Run one 001D hop. Must not grant authority."""

    def recover(self, root: Path, dispatch_id: str) -> dict[str, object]:
        """Recover an in-flight hop. Must not respawn a new process."""

    def find_active_dispatch_id(self, root: Path, *, lease_id: str) -> str | None:
        """Discovery of a dispatch identity the loop itself never recorded
        (ORCH001E-008 P3): a crash between `dispatch_once()` persisting its
        own record and the loop persisting `active_dispatch_id` must not be
        indistinguishable from "nothing was dispatched".

        Contract (independent-IV-hardened, do not weaken):
        - Return the id only if it genuinely belongs to `lease_id`.
          The underlying 001D active-dispatch slot is a single GLOBAL
          slot, not per-lease -- a naive "is anything active" check can
          return an *unrelated* dispatch from a different lease and
          corrupt the wrong governor node. Adapters MUST scope the match
          themselves; this abstract port cannot verify it for them.
        - Return `None` ONLY when you can positively confirm nothing was
          dispatched for this lease. An empty string is never a valid
          "found" value -- treat it the same as `None`.
        - Raise (do not guess, do not return `None`) when you cannot
          positively determine either way -- e.g. the 001D-side record is
          itself unreadable/tampered/ambiguous. The loop treats a raised
          exception as "cannot determine" and fails closed rather than
          risking a duplicate dispatch; it treats a clean `None` as
          "confirmed nothing", safe to retry."""


class LoopState(BaseModel):
    """Persisted loop runtime. Evidence identity, not owner authority."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    repository_identity: str = Field(min_length=1, max_length=256)
    trusted_main: str = Field(min_length=40, max_length=40)
    trusted_tree: str = Field(min_length=40, max_length=40)
    phase: LoopPhase
    sequence: int = Field(ge=0, le=1_000_000)
    ticks_in_invocation: int = Field(ge=0, le=MAX_TICKS_PER_INVOCATION)
    active_package_id: str | None = None
    active_lease_id: str | None = None
    active_dispatch_id: str | None = None
    completed_lease_ids: tuple[str, ...] = ()
    completed_dispatch_ids: tuple[str, ...] = ()
    completed_result_digests: tuple[str, ...] = ()
    stop_reason: StopReason | None = None
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False
    record_digest: str = Field(min_length=64, max_length=64)

    @field_validator("trusted_main", "trusted_tree")
    @classmethod
    def _pin(cls, value: str) -> str:
        return require_full_pin(value, "loop pin")

    @model_validator(mode="after")
    def _no_authority(self) -> LoopState:
        if self.merge_authorized or self.execution_authorized or self.authority_granted:
            raise ValueError("loop state cannot carry authority")
        return self

    def unsigned_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload.pop("record_digest")
        return payload


class LoopTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: LoopPhase
    stop_reason: StopReason | None = None
    #: The specific refusal code behind a coarse ``stop_reason`` when one
    #: is known (e.g. ``STALE_ORIGINATION_IDENTITY`` under
    #: ``HARD_BLOCKER``). Diagnostic detail for the operator-visible
    #: receipt only -- never authority, and deliberately not persisted
    #: into the digest-sealed ``LoopState``. ``None`` for stops that have
    #: no more specific code than their reason.
    stop_detail: str | None = None
    dispatched: bool = False
    recovered: bool = False
    package_id: str | None = None
    lease_id: str | None = None
    dispatch_id: str | None = None
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False
    authority_granted: Literal[False] = False


def seal_loop_state(state: LoopState) -> LoopState:
    return state.model_copy(update={"record_digest": hash_payload(state.unsigned_payload())})


def verify_loop_state(state: LoopState) -> LoopState:
    expected = hash_payload(state.unsigned_payload())
    if state.record_digest != expected:
        raise LoopError("loop state digest mismatch", code="STATE_CORRUPT")
    return state


def initial_loop_state(trusted: TrustedAnchorRecord) -> LoopState:
    return seal_loop_state(
        LoopState(
            repository_identity=trusted.repository_identity,
            trusted_main=trusted.trusted_main,
            trusted_tree=trusted.trusted_tree,
            phase=LoopPhase.IDLE,
            sequence=0,
            ticks_in_invocation=0,
            record_digest=PLACEHOLDER_DIGEST,
        )
    )


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _store_path(root: Path, relative: str) -> Path:
    if ".." in Path(relative).parts or Path(relative).is_absolute():
        raise LoopError("loop store path is unsafe", code="PATH_UNSAFE")
    target = (root / relative).resolve()
    if not _inside(root, target):
        raise LoopError("loop store path escapes root", code="PATH_UNSAFE")
    return target


def _write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(encoded + "\n", encoding="utf-8")
    os.replace(tmp, target)


def load_loop_state(store: Path) -> LoopState:
    path = _store_path(store, CURRENT_NAME)
    if not path.is_file():
        raise LoopError("loop state is missing", code="STATE_MISSING")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LoopError("loop state is unreadable", code="STATE_CORRUPT") from exc
    if not isinstance(payload, dict):
        raise LoopError("loop state is schema-invalid", code="STATE_CORRUPT")
    try:
        state = LoopState.model_validate(payload)
    except Exception as exc:
        raise LoopError("loop state is schema-invalid", code="STATE_CORRUPT") from exc
    return verify_loop_state(state)


def persist_loop_state(store: Path, state: LoopState) -> LoopState:
    sealed = seal_loop_state(state)
    verify_loop_state(sealed)
    root = store.resolve()
    lock_path = _store_path(root, LOCK_NAME)
    try:
        with ProjectIdentityLock(lock_path, wait_seconds=2.0, stale_seconds=30.0):
            _write_json_atomic(
                _store_path(root, CURRENT_NAME),
                sealed.model_dump(mode="json"),
            )
    except IdentityLockError as exc:
        raise LoopError("loop lock is held", code="CONCURRENT_LOOP") from exc
    return sealed


class AutonomousLoop:
    """Persistent continuation above 001D. Never invents owner authority."""

    def __init__(
        self,
        *,
        governor: AutonomousGovernor,
        trusted: TrustedAnchorRecord,
        store: Path,
        root: Path,
        dispatch: DispatchPort | None = None,
        branch: str = "feat/as-orch-001e-autonomous-loop",
        worktree: str = "loop-worktree",
        expected_repository_identity: str = CANONICAL_REPOSITORY_IDENTITY,
        execution_host_class_override: ExecutionHostClass | None = None,
    ) -> None:
        """``execution_host_class_override`` (AS-ORCH-LOCAL-DISPATCH-001,
        PR-C): ``None`` by default -- every existing caller (in
        particular the real CLI entrypoint, ``run_governor_loop_tick()``,
        which never passes this) is completely unaffected; a node this
        loop leases keeps whatever ``execution_host_class`` origination
        materialized it with (``IN_PROCESS`` today, always). Only an
        explicit, opt-in caller that ALSO supplies a real ``dispatch=``
        port can set this to route newly-leased work through that port
        instead -- see ``governor.lease()``'s own docstring for why this
        can only ever narrow HOW an already-authorized lease executes,
        never WHETHER it is authorized at all.
        """
        if not repository_identities_match(
            trusted.repository_identity, expected_repository_identity
        ):
            raise LoopError("cross-project loop reuse is forbidden", code="CROSS_PROJECT")
        self._governor = governor
        self._trusted = trusted
        self._store = store
        self._root = root
        self._dispatch = dispatch
        self._branch = branch
        self._worktree = worktree
        self._execution_host_class_override = execution_host_class_override
        snapshot = governor.snapshot()
        if evaluate_target_moved(
            snapshot.current_main, snapshot.current_tree, trusted
        ) or snapshot.target_moved:
            raise LoopError("refusing loop on moved target", code="TARGET_MOVED")
        if not store.exists():
            persist_loop_state(store, initial_loop_state(trusted))
        self._state = load_loop_state(store)
        if (
            self._state.trusted_main != trusted.trusted_main
            or self._state.trusted_tree != trusted.trusted_tree
        ):
            raise LoopError("loop store pin does not match trusted anchor", code="TARGET_MOVED")

    @property
    def state(self) -> LoopState:
        return self._state

    def _save(self, **updates: object) -> LoopState:
        current = self._state.model_copy(update=updates)
        self._state = persist_loop_state(self._store, current)
        return self._state

    def _result(
        self,
        *,
        dispatched: bool = False,
        recovered: bool = False,
    ) -> LoopTickResult:
        return LoopTickResult(
            phase=self._state.phase,
            stop_reason=self._state.stop_reason,
            dispatched=dispatched,
            recovered=recovered,
            package_id=self._state.active_package_id,
            lease_id=self._state.active_lease_id,
            dispatch_id=self._state.active_dispatch_id,
        )

    def _stop(self, reason: StopReason, *, code: str | None = None) -> LoopTickResult:
        """Stop the loop, optionally naming the specific refusal behind a
        coarse ``StopReason``.

        Owner directive D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE:
        ``code`` was previously accepted and immediately discarded, so
        every distinct refusal that maps to ``HARD_BLOCKER`` (TARGET_MOVED,
        SURFACE_OVERLAP, the three origination-authority denials, ...)
        produced an operator-visible receipt that named only the bucket.
        An operator reading "HARD_BLOCKER" could not tell a superseded
        revision from a surface overlap. It is now carried through to the
        returned receipt (`LoopTickResult.stop_detail`), which
        ``run_governor_loop_tick()`` dumps straight into its JSON payload.

        Deliberately NOT persisted into ``LoopState``: that record is
        digest-sealed, and widening it would be a persisted-schema change
        (and a migration) for what is diagnostic detail, not authority.
        The stop itself -- the part that governs behavior -- is already
        durable via ``stop_reason``.
        """
        self._save(phase=LoopPhase.STOPPED, stop_reason=reason)
        result = self._result()
        if code is None:
            return result
        return result.model_copy(update={"stop_detail": code})

    def _fail(self, message: str, *, code: str) -> NoReturn:
        self._save(phase=LoopPhase.FAILED_CLOSED, stop_reason=StopReason.SAFETY_BOUNDARY)
        raise LoopError(message, code=code)

    def refuse_owner_actions(self) -> None:
        """Hard safety: the loop cannot grant any owner gate.

        ORCHAUT-010 (2026-08-28): previously checked only gate A, while this
        method's own docstring and the module-level ``LOOP_CAN_BYPASS_OWNER_
        GATE = NO`` contract claimed the general property. Checks all six
        ``OwnerGateKind`` values now, so gates C/D/E/F (certified-object
        mutation, security/governance policy, destructive ops, material
        external spend) fail closed the same way A/B already did, before
        persistent autonomy becomes load-bearing.
        """
        for gate in OwnerGateKind:
            require_owner(gate, owner_grant=False)

    def _may_resume_from_no_eligible_work(self) -> bool:
        """True only when the prior stop was "nothing to do" and the
        freshly-rehydrated governor now has a selectable READY node.
        Does not resume OWNER_GATE / SAFETY / RESOURCE / HARD_BLOCKER.

        Cursor Bugbot finding on PR #654 (Medium): this used to call
        ``select_next()`` without ``hard_blockers`` and without checking
        ``target_moved``, unlike ``_select_and_lease()``. A candidate that
        looked selectable here (mismatched eligibility) could immediately
        re-hit ``HARD_BLOCKER`` in the very same tick once
        ``_select_and_lease()`` applied its full check -- and because
        HARD_BLOCKER is never auto-resumed by this method, that turned a
        recoverable NO_ELIGIBLE_WORK stop into a permanent one, even after
        the blocking condition (e.g. a moved target) was later resealed.
        Reuse the exact same eligibility computation as the real lease
        path so a "yes" here always survives the subsequent attempt.
        """
        if self._state.stop_reason is not StopReason.NO_ELIGIBLE_WORK:
            return False
        snapshot = self._governor.snapshot()
        if snapshot.target_moved:
            return False
        decision = select_next(snapshot.nodes, hard_blockers=snapshot.hard_blockers)
        return decision.next_package_id is not None

    def tick(self) -> LoopTickResult:
        """One fail-closed continuation step. At most one 001D dispatch."""
        verify_loop_state(self._state)
        if self._state.ticks_in_invocation >= MAX_TICKS_PER_INVOCATION:
            result = self._stop(StopReason.RESOURCE_BOUNDARY)
            self._enqueue_resource_yield()
            return result
        self._save(ticks_in_invocation=self._state.ticks_in_invocation + 1)
        if self._state.phase == LoopPhase.FAILED_CLOSED:
            return self._result()
        if self._state.phase == LoopPhase.STOPPED:
            if self._state.stop_reason is StopReason.PROJECTION_CONTENTION:
                # Lock contention, not corruption -- always worth one more
                # attempt; the lock holder from the prior tick is expected
                # to have released it by now.
                self._save(phase=LoopPhase.IDLE, stop_reason=None)
            elif not self._may_resume_from_no_eligible_work():
                return self._result()
            else:
                # Codex P1 on PR #654: a later origination scan can add a
                # valid READY node after this process previously persisted
                # STOPPED/NO_ELIGIBLE_WORK. There is no separate resume
                # command -- only this tick. SAFETY/OWNER/RESOURCE stops
                # stay terminal.
                self._save(phase=LoopPhase.IDLE, stop_reason=None)
        if self._state.phase in {LoopPhase.DISPATCHING, LoopPhase.AWAITING_RESULT}:
            return self.recover()
        if self._state.phase == LoopPhase.LEASED:
            return self._dispatch_leased()
        if self._state.phase == LoopPhase.VALIDATING:
            return self._complete_validated()
        return self._select_and_lease()

    def run_until_stop(self) -> LoopTickResult:
        last = self._result()
        for _ in range(MAX_TICKS_PER_INVOCATION):
            last = self.tick()
            if last.phase in {LoopPhase.STOPPED, LoopPhase.FAILED_CLOSED}:
                return last
            if last.phase == LoopPhase.AWAITING_RESULT:
                return last
        result = self._stop(StopReason.RESOURCE_BOUNDARY)
        self._enqueue_resource_yield()
        return result

    def recover(self) -> LoopTickResult:
        """Crash/restart recovery. Never respawns a duplicate process."""
        verify_loop_state(self._state)
        if self._state.phase == LoopPhase.DISPATCHING and self._state.active_dispatch_id:
            if self._dispatch is None:
                return self._fail("in-flight dispatch cannot be recovered", code="ORPHAN_PROCESS")
            recovered = self._dispatch.recover(self._root, self._state.active_dispatch_id)
            status = str(recovered.get("status", ""))
            if status in {"COMPLETED", "FAILED"}:
                digest = str(recovered.get("digest") or recovered.get("dispatch_id") or "")
                return self.apply_observed_result(
                    self._state.active_dispatch_id,
                    digest,
                    passed=status == "COMPLETED",
                    recovered=True,
                )
            self._save(phase=LoopPhase.AWAITING_RESULT)
            return self._result(recovered=True)
        if self._state.phase == LoopPhase.DISPATCHING and not self._state.active_dispatch_id:
            # ORCH001E-008 P3 remediation: a crash between `dispatch_once()`
            # returning (or even just persisting its own 001D-side record)
            # and this loop persisting `active_dispatch_id` used to be
            # silently indistinguishable from "no dispatch was ever
            # attempted" -- the loop would sit in DISPATCHING forever with
            # no path to find what, if anything, actually started. Ask the
            # dispatch port itself, which may hold an independent record
            # the loop's own state file never learned about.
            if self._dispatch is None:
                return self._fail("in-flight dispatch cannot be recovered", code="ORPHAN_PROCESS")
            lease_id = self._state.active_lease_id
            if lease_id is None:
                return self._fail(
                    "dispatching phase missing lease identity", code="PARTIAL_COMPLETION"
                )
            try:
                found = self._dispatch.find_active_dispatch_id(self._root, lease_id=lease_id)
            except Exception as exc:
                # Independent-IV finding: the port could not positively
                # confirm either way (e.g. its own record is unreadable).
                # Do NOT treat this the same as "confirmed nothing" -- an
                # automatic retry here could duplicate a real in-flight
                # process. Fail closed instead: stuck-but-observable is
                # the accepted tradeoff, exactly as it already is for a
                # dispatch port this loop has no way to recover at all.
                return self._fail(
                    f"dispatch discovery could not determine outcome: {exc}",
                    code="DISPATCH_RECOVERY_AMBIGUOUS",
                )
            if not found:
                # `None` (or, defensively, an empty string -- never a
                # valid identity) means the port positively confirms
                # nothing was dispatched for this lease. Safe to retry
                # from LEASED: no process is known to exist that a retry
                # could duplicate.
                self._save(phase=LoopPhase.LEASED)
                return self._dispatch_leased()
            if found in self._state.completed_dispatch_ids:
                raise LoopError("recovered dispatch already completed", code="RESULT_REPLAY")
            self._save(active_dispatch_id=found)
            recovered = self._dispatch.recover(self._root, found)
            status = str(recovered.get("status", ""))
            if status in {"COMPLETED", "FAILED"}:
                digest = str(recovered.get("digest") or recovered.get("dispatch_id") or "")
                return self.apply_observed_result(
                    found, digest, passed=status == "COMPLETED", recovered=True
                )
            self._save(phase=LoopPhase.AWAITING_RESULT)
            return self._result(recovered=True)
        if self._state.phase == LoopPhase.AWAITING_RESULT:
            if self._dispatch is not None and self._state.active_dispatch_id:
                observed = self._dispatch.recover(self._root, self._state.active_dispatch_id)
                status = str(observed.get("status", ""))
                if status in {"COMPLETED", "FAILED"}:
                    digest = str(observed.get("digest") or self._state.active_dispatch_id)
                    return self.apply_observed_result(
                        self._state.active_dispatch_id,
                        digest,
                        passed=status == "COMPLETED",
                        recovered=True,
                    )
            return self._result(recovered=True)
        if self._state.phase == LoopPhase.LEASED and self._state.active_lease_id:
            return self._dispatch_leased()
        if self._state.phase == LoopPhase.VALIDATING:
            return self._complete_validated()
        return self._result(recovered=True)

    def apply_observed_result(
        self,
        dispatch_id: str,
        result_digest: str,
        *,
        passed: bool,
        recovered: bool = False,
    ) -> LoopTickResult:
        if dispatch_id in self._state.completed_dispatch_ids:
            raise LoopError("duplicate dispatch completion", code="RESULT_REPLAY")
        if result_digest and result_digest in self._state.completed_result_digests:
            raise LoopError("duplicate result digest", code="RESULT_REPLAY")
        if self._state.active_dispatch_id not in {None, dispatch_id}:
            raise LoopError("result does not match active dispatch", code="RESULT_MISMATCH")
        package_id = self._state.active_package_id
        if package_id is None:
            raise LoopError("result has no active package", code="PARTIAL_COMPLETION")
        self._save(phase=LoopPhase.VALIDATING)
        node = next(
            (item for item in self._governor.snapshot().nodes if item.package_id == package_id),
            None,
        )
        if node is None:
            # D-203 round 3: a freshly-constructed AutonomousGovernor (what a
            # real process restart gets -- see run_governor_loop_tick() in
            # cli.py, which builds a brand-new governor from live inventory
            # alone on every invocation) has no in-memory node/lease state at
            # all. Governor node/lease rehydration across a real process
            # restart is not implemented here -- that is ORCH001E-011's own,
            # separately tracked scope. Until that lands, fail closed with a
            # structured, CLI-catchable LoopError instead of letting a bare
            # StopIteration escape uncaught.
            return self._fail(
                f"governor has no node for persisted active package {package_id!r}; "
                "governor state was not rehydrated across a process restart "
                "(see D-203/ORCH001E-011)",
                code="GOVERNOR_STATE_NOT_REHYDRATED",
            )
        if node.state is NodeState.LEASED:
            self._governor.transition(package_id, NodeState.ACTIVE, "LOOP_PROCESS_STARTED")
        if passed:
            self._governor.transition(package_id, NodeState.VERIFYING, "LOOP_RESULT_VALIDATED")
            try:
                self._governor.complete_verification(package_id, passed=True)
            except OwnerGateError:
                return self._stop(StopReason.OWNER_GATE)
        else:
            self._governor.transition(package_id, NodeState.VERIFYING, "LOOP_RESULT_FAILED")
            self._governor.complete_verification(package_id, passed=False)
            node = next(
                (item for item in self._governor.snapshot().nodes if item.package_id == package_id),
                None,
            )
            if node is None:
                # Defensive: same class of gap as the lookup above -- this
                # governor just transitioned this exact package_id, so it is
                # not reachable in the same in-memory tick, only across an
                # unrehydrated process restart replaying a stale dispatch.
                return self._fail(
                    f"governor has no node for persisted active package {package_id!r}; "
                    "governor state was not rehydrated across a process restart "
                    "(see D-203/ORCH001E-011)",
                    code="GOVERNOR_STATE_NOT_REHYDRATED",
                )
            if node.state == NodeState.BLOCKED:
                return self._stop(StopReason.HARD_BLOCKER)
            if node.state == NodeState.REMEDIATING:
                self._governor.remediate_and_resume(package_id)
                self._save(
                    phase=LoopPhase.LEASED,
                    active_dispatch_id=None,
                    sequence=self._state.sequence + 1,
                )
                return self._result(recovered=recovered)
        return self._finalize_validated(dispatch_id, result_digest, recovered=recovered)

    def _finalize_validated(
        self, dispatch_id: str, result_digest: str, *, recovered: bool
    ) -> LoopTickResult:
        """Shared tail of a successful `VALIDATING` completion: everything
        left to do is LoopState-side bookkeeping (mark the dispatch/lease/
        result as completed, return to IDLE) once the governor's own node
        transitions are already known-good. Factored out so
        `_complete_validated()`'s dangling-`active_dispatch_id` recovery
        (ORCH001E-012) can reach the same finish line without repeating
        governor mutations that a prior, interrupted call already made --
        re-running them would attempt an illegal transition out of an
        already-terminal node state (see PR #637's identical
        check-node-state-before-redo idiom in `_dispatch_leased()`).
        """
        if dispatch_id in self._state.completed_dispatch_ids:
            raise LoopError("duplicate dispatch completion", code="RESULT_REPLAY")
        if result_digest and result_digest in self._state.completed_result_digests:
            raise LoopError("duplicate result digest", code="RESULT_REPLAY")
        lease_id = self._state.active_lease_id
        completed_leases = self._state.completed_lease_ids
        completed_dispatches = self._state.completed_dispatch_ids
        completed_results = self._state.completed_result_digests
        if lease_id is not None:
            completed_leases = (*completed_leases, lease_id)
        completed_dispatches = (*completed_dispatches, dispatch_id)
        if result_digest:
            completed_results = (*completed_results, result_digest)
        self._save(
            phase=LoopPhase.IDLE,
            active_package_id=None,
            active_lease_id=None,
            active_dispatch_id=None,
            completed_lease_ids=completed_leases,
            completed_dispatch_ids=completed_dispatches,
            completed_result_digests=completed_results,
            sequence=self._state.sequence + 1,
        )
        return self._result(recovered=recovered)

    def _select_and_lease(self) -> LoopTickResult:
        snapshot = self._governor.snapshot()
        if snapshot.target_moved:
            return self._stop(StopReason.HARD_BLOCKER)
        decision = select_next(snapshot.nodes, hard_blockers=snapshot.hard_blockers)
        if decision.next_package_id is None:
            return self._stop(decision.stop_reason or StopReason.NO_ELIGIBLE_WORK)
        node = next(item for item in snapshot.nodes if item.package_id == decision.next_package_id)
        if node.owner_gate is not None:
            # ORCHAUT-010 remediation (2026-08-28): defense-in-depth twin of
            # the select_next fix -- `select_next` should already exclude
            # any owner-gated node, but this is the last check before a
            # real lease is granted, so it must not be a dead-code
            # `state != READY` comparison that a READY owner-gated node
            # always fails (i.e. never actually stops it).
            return self._stop(StopReason.OWNER_GATE)
        if node.state == NodeState.MERGE_ELIGIBLE:
            return self._stop(StopReason.OWNER_GATE)
        try:
            lease = self._governor.lease(
                node.package_id,
                self._first_agent(),
                branch=self._branch,
                worktree=self._worktree,
                execution_host_class_override=self._execution_host_class_override,
            )
        except ProjectionError as exc:
            if exc.code == "CONCURRENT_PROJECTION":
                # Cursor Bugbot finding on PR #654 (Medium): a lock-wait
                # timeout is contention, not corruption. Mapping it to the
                # same HARD_BLOCKER as LEASE_REPLAY/STALE_LEASE/STATE_CORRUPT
                # permanently froze the loop on what is normally a
                # self-clearing condition (the other writer releases the
                # lock). Fail closed but resumably.
                return self._stop(StopReason.PROJECTION_CONTENTION)
            # Durable lease-projection failures (LEASE_REPLAY / STALE_LEASE
            # / STATE_CORRUPT) used to crash the tick uncaught. Fail closed
            # the same way the overlapping GovernorError codes already do.
            return self._stop(StopReason.HARD_BLOCKER)
        except GovernorError as exc:
            # DEPENDENCIES_NOT_SATISFIED (D-PHASE2A-1a): select_next() has
            # its own, independent dependency-readiness check (deps_ok,
            # continuation.py) that should already exclude anything
            # governor.lease() would reject here -- this is the same
            # defense-in-depth relationship SURFACE_OVERLAP/NODE_NOT_READY
            # already have with their own upstream advisory checks. If the
            # two checks ever disagree (e.g. a future WorkNode shape
            # neither one anticipated), fail closed with a StopReason
            # instead of letting the loop crash on an uncaught exception.
            #
            # Owner directive D-ATLAS-PR678-CASE-A-LEASE-AUTHORITY-CLOSURE:
            # the three origination-authority refusals belong in exactly
            # this bucket for exactly the reason above. `lease()` now
            # denies a node whose originating revision is stale,
            # unverifiable, or missing its provenance -- all correct, all
            # fail-CLOSED, and all reachable in ordinary operation (a scan
            # can supersede a READY-but-unleased node between ticks).
            # Unlisted, they would `raise` and crash the tick on an
            # uncaught exception instead of stopping honestly with a
            # receipt naming the real reason. No lease is granted either
            # way, but a crash is not a governed stop.
            #
            # HARD_BLOCKER rather than PROJECTION_CONTENTION: unlike a
            # lock-wait timeout, this durable state IS known to be wrong
            # for this node and does NOT self-clear on the next tick --
            # only a fresh origination scan can supersede the stale
            # revision, so auto-resuming every tick would merely spin.
            if exc.code in {
                "TARGET_MOVED",
                "SURFACE_OVERLAP",
                "NODE_NOT_READY",
                "DEPENDENCIES_NOT_SATISFIED",
                "STALE_ORIGINATION_IDENTITY",
                "ORIGINATION_AUTHORITY_UNAVAILABLE",
                "MISSING_ORIGINATION_IDENTITY",
            }:
                return self._stop(StopReason.HARD_BLOCKER, code=exc.code)
            raise
        if lease.lease_id in self._state.completed_lease_ids:
            raise LoopError("lease replay is forbidden", code="LEASE_REPLAY")
        try:
            expand_lease(lease)
        except Exception:
            pass
        else:  # pragma: no cover - expand_lease always raises
            raise LoopError("lease expansion succeeded", code="SCOPE_EXPANSION")
        self._save(
            phase=LoopPhase.LEASED,
            active_package_id=node.package_id,
            active_lease_id=lease.lease_id,
            sequence=self._state.sequence + 1,
        )
        return self._dispatch_leased()

    def _dispatch_leased(self) -> LoopTickResult:
        package_id = self._state.active_package_id
        lease_id = self._state.active_lease_id
        if package_id is None or lease_id is None:
            return self._fail("leased phase missing identity", code="PARTIAL_COMPLETION")
        node = next(
            (item for item in self._governor.snapshot().nodes if item.package_id == package_id),
            None,
        )
        if node is None:
            # D-203 round 3 (review thread PRRT_kwDOTtguR86dRAuB on PR #637):
            # when recovery runs after a real process restart,
            # run_governor_loop_tick() (cli.py) constructs a brand-new
            # AutonomousGovernor from live inventory alone -- its node/lease
            # list is empty, only this loop's own LoopState survives on
            # disk. A LEASED/ACTIVE phase whose package_id the fresh
            # governor never saw used to fall through to the bare `next()`
            # below and raise an uncaught StopIteration, which cli.py's
            # `except (TrustError, DiscoveryError, LoopError)` does not
            # catch. Full governor node/lease rehydration across a process
            # restart is intentionally out of scope here -- it is
            # ORCH001E-011's own, separately tracked fix (see
            # docs/evidence/D-203-...md and the sibling in-flight PR) -- so
            # until that lands, fail closed with a structured,
            # CLI-catchable LoopError instead of crashing uncaught.
            return self._fail(
                f"governor has no node for persisted leased package {package_id!r}; "
                "governor state was not rehydrated across a process restart "
                "(see D-203/ORCH001E-011)",
                code="GOVERNOR_STATE_NOT_REHYDRATED",
            )
        if node.execution_host_class.value == "IN_PROCESS":
            in_process_id = f"in-process:{lease_id}"
            if node.state == NodeState.LEASED:
                # Normal path: not yet executed. execute_leased() runs
                # synchronously in-process, so if this call itself crashes
                # partway through, the governor's own transition() call is
                # what would have raised -- nothing to recover from here,
                # it simply didn't complete. What this guard protects
                # against is the *other* crash window: execute_leased()
                # already succeeded (node moved to ACTIVE) but the process
                # died before apply_observed_result() below ever ran, so
                # this same LEASED-recovery branch gets hit again on
                # restart with a node that is no longer LEASED.
                try:
                    self._governor.execute_leased(lease_id)
                except IllegalTransitionError as exc:  # pragma: no cover - defensive
                    return self._fail(
                        f"in-process execution transition rejected: {exc}",
                        code="EXECUTION_STATE_CONFLICT",
                    )
            elif node.state != NodeState.ACTIVE:
                # Independent-IV finding: node.state != LEASED alone is not
                # enough to assume "already executed, safe to finalize".
                # apply_observed_result() itself unconditionally attempts
                # `transition(..., VERIFYING, ...)`, which is only legal
                # from ACTIVE (see dag.py's ALLOWED_TRANSITIONS) -- any
                # other unexpected state (READY, BLOCKED, OWNER_HELD, ...)
                # would just reintroduce an uncaught IllegalTransitionError
                # one level down, the exact crash-loop this fix exists to
                # close. Fail closed with a clear diagnostic instead of
                # guessing.
                return self._fail(
                    f"in-process recovery found unexpected node state "
                    f"{node.state.value} (expected LEASED or ACTIVE)",
                    code="EXECUTION_STATE_CONFLICT",
                )
            # else node.state == NodeState.ACTIVE: already executed before a
            # crash between execute_leased() succeeding and
            # apply_observed_result() completing. IN_PROCESS execution has
            # no partial/async state -- its outcome is fully re-derived from
            # the same deterministic id, and apply_observed_result() is
            # itself replay-guarded (RESULT_REPLAY) against being applied
            # twice, so re-entering it here is safe rather than re-running
            # execute_leased() (which would attempt an illegal
            # ACTIVE -> ACTIVE-style transition and raise instead of
            # recovering).
            return self.apply_observed_result(in_process_id, in_process_id, passed=True)
        if self._dispatch is None:
            return self._fail("external dispatch port is required", code="DISPATCH_UNAVAILABLE")
        self._save(phase=LoopPhase.DISPATCHING)
        receipt = self._dispatch.dispatch_once(self._root)
        dispatch_id = str(receipt.get("dispatch_id") or "")
        if not dispatch_id:
            return self._fail("001D dispatch returned no identity", code="DISPATCH_UNAVAILABLE")
        if dispatch_id in self._state.completed_dispatch_ids:
            raise LoopError("duplicate process dispatch", code="DUPLICATE_DISPATCH")
        status = str(receipt.get("status") or "")
        self._save(active_dispatch_id=dispatch_id, phase=LoopPhase.AWAITING_RESULT)
        if status in {"COMPLETED", "FAILED"}:
            digest = str(receipt.get("digest") or dispatch_id)
            return self.apply_observed_result(dispatch_id, digest, passed=status == "COMPLETED")
        if status in {"OWNER_REQUIRED", "TERMINAL"}:
            return self._stop(StopReason.OWNER_GATE)
        return self._result(dispatched=True)

    def _complete_validated(self) -> LoopTickResult:
        """ORCH001E-012: `apply_observed_result()` persists `phase=
        VALIDATING` (via `_save`) well before its own final `_save(...)`
        clears `active_dispatch_id` -- if whatever interrupted the earlier
        call (an uncaught exception a caller swallowed and retried on the
        same, still-live `AutonomousLoop`/`AutonomousGovernor` objects; a
        real cross-process crash is already rejected earlier by
        `rehydrate_governor()`'s fail-closed `EXECUTION_STATE_NOT_
        REHYDRATABLE` for VALIDATING, see D-205) happened in that window,
        a plain `tick()`/`recover()` used to land here and silently no-op
        forever -- no error, no progress, not even a terminal phase.

        Fixed the same way `_dispatch_leased()` already resolves the
        equivalent LEASED/ACTIVE ambiguity (PR #637): read the governor's
        *current* node state -- still live in this same process -- to
        learn exactly how far the interrupted call actually got, then
        either safely redrive from scratch (nothing was mutated yet) or
        finish only the bookkeeping that never ran (governor mutations
        already succeeded) -- never blindly repeat a governor transition
        that may no longer be legal from the node's current state.
        """
        if self._state.active_dispatch_id is None:
            self._save(phase=LoopPhase.IDLE)
            return self._result()
        dispatch_id = self._state.active_dispatch_id
        package_id = self._state.active_package_id
        if package_id is None:
            self._fail(
                "validating phase missing active package for dangling dispatch",
                code="PARTIAL_COMPLETION",
            )
        node = next(
            (item for item in self._governor.snapshot().nodes if item.package_id == package_id),
            None,
        )
        if node is None:
            # Same class of gap as apply_observed_result()'s own node
            # lookups: a freshly-constructed governor (a real process
            # restart) never saw this package. Cannot happen for the
            # genuinely-scoped same-process re-entrancy this fix targets
            # (the live governor object is unchanged), but defend anyway
            # rather than let a bare StopIteration through.
            self._fail(
                f"governor has no node for persisted active package {package_id!r}; "
                "governor state was not rehydrated across a process restart "
                "(see D-203/ORCH001E-011)",
                code="GOVERNOR_STATE_NOT_REHYDRATED",
            )
        is_in_process = node.execution_host_class.value == "IN_PROCESS"
        if node.state in {NodeState.LEASED, NodeState.ACTIVE}:
            # Verification never started (or never got far enough to
            # mutate the node) before the interruption -- safe to redrive
            # apply_observed_result() exactly as a first call, re-deriving
            # the true outcome from the dispatch's own durable record (the
            # same move recover() already makes for DISPATCHING/
            # AWAITING_RESULT) rather than trusting a stale in-memory
            # `passed` value this loop never persisted anywhere.
            passed, digest = self._reobserve_dispatch_outcome(dispatch_id, in_process=is_in_process)
            return self.apply_observed_result(dispatch_id, digest, passed=passed, recovered=True)
        if node.state == NodeState.BLOCKED:
            return self._stop(StopReason.HARD_BLOCKER)
        if node.state == NodeState.REMEDIATING:
            self._governor.remediate_and_resume(package_id)
            self._save(
                phase=LoopPhase.LEASED,
                active_dispatch_id=None,
                sequence=self._state.sequence + 1,
            )
            return self._result(recovered=True)
        if node.state in {NodeState.CERTIFIED, NodeState.OWNER_HELD, NodeState.MERGE_ELIGIBLE}:
            # complete_verification(passed=True) already fully ran before
            # the interruption -- only apply_observed_result()'s own tail
            # (LoopState bookkeeping) never finished. Re-driving
            # apply_observed_result() here would attempt an illegal
            # transition out of an already-terminal node state; finish the
            # bookkeeping directly via the same tail it would have reached
            # (_finalize_validated) instead.
            observed_passed, digest = self._reobserve_dispatch_outcome(
                dispatch_id, in_process=is_in_process
            )
            if not observed_passed:
                self._fail(
                    f"node {package_id!r} reached a passed-verification state "
                    f"({node.state.value}) but the dispatch outcome now "
                    f"reobserves as failed for {dispatch_id!r} -- durable "
                    "evidence is inconsistent, refusing to guess which is "
                    "authoritative",
                    code="VALIDATION_STATE_AMBIGUOUS",
                )
            return self._finalize_validated(dispatch_id, digest, recovered=True)
        # VERIFYING (complete_verification() itself was interrupted -- no
        # normal, synchronous code path leaves a node here, see the
        # docstring above) or any other state this crash window has no
        # established mapping for: do not guess which outcome it implies.
        self._fail(
            f"validating recovery found unexpected node state {node.state.value} "
            "(expected LEASED, ACTIVE, CERTIFIED, OWNER_HELD, MERGE_ELIGIBLE, "
            "BLOCKED, or REMEDIATING)",
            code="VALIDATION_STATE_AMBIGUOUS",
        )

    def _reobserve_dispatch_outcome(
        self, dispatch_id: str, *, in_process: bool
    ) -> tuple[bool, str]:
        """Re-derive the true dispatch outcome from its own durable record
        -- the same move `recover()` already makes for DISPATCHING/
        AWAITING_RESULT -- instead of trusting a stale in-memory `passed`
        value `apply_observed_result()` never persisted anywhere.
        """
        if in_process:
            # IN_PROCESS execution has exactly one call site
            # (`_dispatch_leased()`), which always hardcodes `passed=True`
            # and uses the dispatch id itself as the digest -- there is no
            # external durable record to query, but none is needed: the
            # outcome is a structural constant of the code, not runtime
            # data.
            return True, dispatch_id
        if self._dispatch is None:
            self._fail(
                f"dangling dispatch {dispatch_id!r} cannot be reobserved without "
                "a dispatch port",
                code="DISPATCH_UNAVAILABLE",
            )
        observed = self._dispatch.recover(self._root, dispatch_id)
        status = str(observed.get("status", ""))
        if status not in {"COMPLETED", "FAILED"}:
            self._fail(
                f"dangling dispatch {dispatch_id!r} has no terminal status to "
                f"reobserve (dispatch port reports {status!r})",
                code="VALIDATION_STATE_AMBIGUOUS",
            )
        digest = str(observed.get("digest") or observed.get("dispatch_id") or dispatch_id)
        return status == "COMPLETED", digest

    def _enqueue_resource_yield(self) -> None:
        """RESOURCE_BOUNDARY is YIELD, not OWNER_REQUIRED. Atomic finalize."""
        from project_atlas.orchestration.autonomy.continuation_broker import (
            finalize_governor_checkpoint,
        )

        cycle_id = f"YIELD-{self._state.sequence}"
        finalize_governor_checkpoint(
            self._root,
            result_class="RESOURCE_YIELD",
            cycle_id=cycle_id,
            trusted_main=self._trusted.trusted_main,
            trusted_tree=self._trusted.trusted_tree,
            next_action_class="RESOURCE_YIELD",
            repository_identity=self._trusted.repository_identity,
            dag_generation=self._state.sequence,
            safe_dag_work_remains=True,
        )

    def _first_agent(self) -> str:
        for agent in self._governor.snapshot().agents:
            if agent.available:
                return agent.agent_id
        raise LoopError("no available agent", code="AGENT_UNAVAILABLE")


class CallableDispatchPort:
    """Adapter around callables. Tests and CLI inject 001D here."""

    def __init__(
        self,
        dispatch_once: Callable[[Path], dict[str, object]],
        recover: Callable[[Path, str], dict[str, object]] | None = None,
        find_active_dispatch_id: Callable[[Path, str], str | None] | None = None,
    ) -> None:
        self._dispatch_once = dispatch_once
        self._recover = recover
        self._find_active_dispatch_id = find_active_dispatch_id

    def dispatch_once(self, root: Path) -> dict[str, object]:
        return self._dispatch_once(root)

    def recover(self, root: Path, dispatch_id: str) -> dict[str, object]:
        if self._recover is None:
            return {"dispatch_id": dispatch_id, "status": "RUNNING"}
        return self._recover(root, dispatch_id)

    def find_active_dispatch_id(self, root: Path, *, lease_id: str) -> str | None:
        if self._find_active_dispatch_id is None:
            return None
        return self._find_active_dispatch_id(root, lease_id)
