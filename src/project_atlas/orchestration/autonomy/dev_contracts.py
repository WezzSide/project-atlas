"""Sealed work / result / verification / finding contracts for the autonomous development loop.

AS-DEVLOOP-001, slice 2. Transport-neutral: these are plain sealed records that any transport
(GitHub-as-bus, a ``DispatchPort`` adapter, a filesystem spool, the in-memory reference backend)
can carry. Roles and capabilities are modelled; hosts are a deployment mapping.

Invariants enforced here (and tested):
  * RESULT != VERDICT: a result record can never be consumed as a verdict (distinct kinds).
  * IMPLEMENTER != VERIFIER: verification requires a different execution identity.
  * Verifier input references the EXACT result revision/tree, never an implementer summary.
  * A PASS verdict is bound to the exact result the verifier was asked about.
  * Repair never widens authority (same authority ref, scope, repository) and is bounded by the
    attempt ceiling; its lineage root is preserved.
  * Owner-only findings (secret, trust root, privilege, destructive, owner merge) are never
    auto-repaired; architecture blockers stop the lane.
  * Nothing here grants an owner gate, merges, or dispatches.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from project_atlas.orchestration.autonomy.evidence import hash_payload

_SHA = re.compile(r"^[0-9a-f]{40}$")


class ContractError(ValueError):
    code = "DEV_CONTRACT_INVALID"


class Role(StrEnum):
    PLANNER = "PLANNER"
    IMPLEMENTER = "IMPLEMENTER"
    VERIFIER = "VERIFIER"


class RecordKind(StrEnum):
    WORK = "WORK"
    RESULT = "RESULT"
    VERIFICATION_REQUEST = "VERIFICATION_REQUEST"
    VERDICT = "VERDICT"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class FindingClass(StrEnum):
    REPAIRABLE_WITHIN_AUTHORITY = "REPAIRABLE_WITHIN_AUTHORITY"
    OWNER_AUTHORITY_REQUIRED = "OWNER_AUTHORITY_REQUIRED"
    NON_REPAIRABLE = "NON_REPAIRABLE"


class FindingCategory(StrEnum):
    DEFECT = "DEFECT"
    TEST_GAP = "TEST_GAP"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    SECRET_REQUIRED = "SECRET_REQUIRED"
    TRUST_ROOT = "TRUST_ROOT"
    PRIVILEGE_EXPANSION = "PRIVILEGE_EXPANSION"
    DESTRUCTIVE_ACTION = "DESTRUCTIVE_ACTION"
    OWNER_MERGE = "OWNER_MERGE"
    ARCHITECTURE = "ARCHITECTURE"


_OWNER_CATEGORIES = frozenset(
    {
        FindingCategory.SECRET_REQUIRED,
        FindingCategory.TRUST_ROOT,
        FindingCategory.PRIVILEGE_EXPANSION,
        FindingCategory.DESTRUCTIVE_ACTION,
        FindingCategory.OWNER_MERGE,
    }
)


class _Sealed(BaseModel):
    """Frozen, strict record with a content digest over every other field."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    KIND: ClassVar[RecordKind]
    seal: str = ""

    def _unsigned(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude={"seal"})
        d["kind"] = self.KIND.value
        return d

    def compute_seal(self) -> str:
        return hash_payload(self._unsigned())

    def sealed(self) -> Any:
        return self.model_copy(update={"seal": self.compute_seal()})

    def verify_seal(self) -> None:
        if not self.seal or self.seal != self.compute_seal():
            raise ContractError(f"{self.KIND.value}: seal mismatch")


def _sha(v: str, name: str) -> str:
    if not _SHA.match(v):
        raise ValueError(f"{name} must be a 40-hex lowercase sha")
    return v


class WorkItem(_Sealed):
    """What the planner hands the implementation role. Immutable; identity is the seal."""

    KIND: ClassVar[RecordKind] = RecordKind.WORK
    task_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    lineage_root: str = Field(min_length=1)
    parent_task_id: str | None = None
    repository: str = Field(min_length=1)
    base_revision: str
    authority_ref: str = Field(min_length=1)
    required_role: Role = Role.IMPLEMENTER
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    acceptance_contract: tuple[str, ...] = ()
    attempt: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1)

    @field_validator("base_revision")
    @classmethod
    def _base(cls, v: str) -> str:
        return _sha(v, "base_revision")


class ResultRecord(_Sealed):
    """What the implementation role produced. Carries identity, never a verdict."""

    KIND: ClassVar[RecordKind] = RecordKind.RESULT
    task_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    executor_identity: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_revision: str
    result_revision: str
    result_tree: str
    changed_paths: tuple[str, ...] = ()
    test_evidence_digests: tuple[str, ...] = ()
    artifact_digests: tuple[str, ...] = ()
    work_seal: str = Field(min_length=1)

    @field_validator("base_revision", "result_revision", "result_tree")
    @classmethod
    def _shas(cls, v: str) -> str:
        return _sha(v, "sha field")


class VerificationRequest(_Sealed):
    """What the verifier is given: the exact artifact, re-derivable, not a summary."""

    KIND: ClassVar[RecordKind] = RecordKind.VERIFICATION_REQUEST
    task_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_revision: str
    result_revision: str
    result_tree: str
    executor_identity: str = Field(min_length=1)
    verifier_identity: str = Field(min_length=1)
    acceptance_contract: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    result_seal: str = Field(min_length=1)

    @field_validator("base_revision", "result_revision", "result_tree")
    @classmethod
    def _shas(cls, v: str) -> str:
        return _sha(v, "sha field")


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    finding_id: str = Field(min_length=1)
    category: FindingCategory
    message: str = ""
    paths: tuple[str, ...] = ()


class VerdictRecord(_Sealed):
    """What the verifier concluded about one exact artifact."""

    KIND: ClassVar[RecordKind] = RecordKind.VERDICT
    task_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    verifier_identity: str = Field(min_length=1)
    verdict: Verdict
    result_revision: str
    result_tree: str
    findings: tuple[Finding, ...] = ()
    request_seal: str = Field(min_length=1)

    @field_validator("result_revision", "result_tree")
    @classmethod
    def _shas(cls, v: str) -> str:
        return _sha(v, "sha field")


def _err(exc: ValidationError) -> ContractError:
    return ContractError(str(exc.errors()[0].get("msg", "invalid record")))


def make_work(**kw: Any) -> WorkItem:
    try:
        return WorkItem(**kw).sealed()  # type: ignore[no-any-return]
    except ValidationError as exc:
        raise _err(exc) from exc


def make_result(work: WorkItem, **kw: Any) -> ResultRecord:
    """Bind a result to the exact sealed work it answers; identity must line up."""
    work.verify_seal()
    try:
        rec = ResultRecord(
            task_id=work.task_id,
            execution_id=work.execution_id,
            repository=work.repository,
            base_revision=work.base_revision,
            work_seal=work.seal,
            **kw,
        )
    except ValidationError as exc:
        raise _err(exc) from exc
    return rec.sealed()  # type: ignore[no-any-return]


def make_verification_request(
    work: WorkItem, result: ResultRecord, *, verifier_identity: str
) -> VerificationRequest:
    """IMPLEMENTER != VERIFIER; the request re-states the exact artifact, not a summary."""
    work.verify_seal()
    result.verify_seal()
    if result.work_seal != work.seal or result.task_id != work.task_id:
        raise ContractError("result does not answer this work item")
    if verifier_identity == result.executor_identity:
        raise ContractError("verifier identity must differ from executor identity")
    try:
        req = VerificationRequest(
            task_id=work.task_id,
            execution_id=work.execution_id,
            repository=work.repository,
            base_revision=work.base_revision,
            result_revision=result.result_revision,
            result_tree=result.result_tree,
            executor_identity=result.executor_identity,
            verifier_identity=verifier_identity,
            acceptance_contract=work.acceptance_contract,
            allowed_paths=work.allowed_paths,
            forbidden_paths=work.forbidden_paths,
            result_seal=result.seal,
        )
    except ValidationError as exc:
        raise _err(exc) from exc
    return req.sealed()  # type: ignore[no-any-return]


def make_verdict(
    request: VerificationRequest,
    *,
    verdict: Verdict,
    findings: tuple[Finding, ...] = (),
    result_revision: str | None = None,
    result_tree: str | None = None,
) -> VerdictRecord:
    """A verdict is bound to the artifact it judged; PASS may not carry findings."""
    request.verify_seal()
    rev = result_revision or request.result_revision
    tree = result_tree or request.result_tree
    if rev != request.result_revision or tree != request.result_tree:
        raise ContractError("verdict must judge the exact requested artifact")
    if verdict is Verdict.PASS and findings:
        raise ContractError("PASS cannot carry findings")
    if verdict is Verdict.FAIL and not findings:
        raise ContractError("FAIL requires at least one finding")
    try:
        rec = VerdictRecord(
            task_id=request.task_id,
            execution_id=request.execution_id,
            verifier_identity=request.verifier_identity,
            verdict=verdict,
            result_revision=rev,
            result_tree=tree,
            findings=findings,
            request_seal=request.seal,
        )
    except ValidationError as exc:
        raise _err(exc) from exc
    return rec.sealed()  # type: ignore[no-any-return]


def classify_finding(finding: Finding, work: WorkItem) -> FindingClass:
    """Deterministic classification; anything unknown fails toward NOT auto-repairing."""
    if finding.category in _OWNER_CATEGORIES:
        return FindingClass.OWNER_AUTHORITY_REQUIRED
    if finding.category is FindingCategory.ARCHITECTURE:
        return FindingClass.NON_REPAIRABLE
    if finding.category is FindingCategory.SCOPE_VIOLATION:
        # a scope violation is repairable only by REMOVING the out-of-scope change; needing the
        # scope widened is an authority question
        return FindingClass.REPAIRABLE_WITHIN_AUTHORITY
    if finding.category in {FindingCategory.DEFECT, FindingCategory.TEST_GAP}:
        forbidden = tuple(p for p in finding.paths if _matches(p, work.forbidden_paths))
        outside = (
            tuple(p for p in finding.paths if not _matches(p, work.allowed_paths))
            if work.allowed_paths
            else ()
        )
        if forbidden or outside:
            return FindingClass.OWNER_AUTHORITY_REQUIRED
        return FindingClass.REPAIRABLE_WITHIN_AUTHORITY
    return FindingClass.NON_REPAIRABLE


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


class RepairDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: str  # REPAIR | OWNER_REQUIRED | BLOCKED
    reason: str
    repair_work: WorkItem | None = None
    classes: tuple[str, ...] = ()


def materialize_repair(
    work: WorkItem, verdict: VerdictRecord, *, result: ResultRecord
) -> RepairDecision:
    """Turn a FAIL verdict into the next bounded repair item, or stop.

    The repair item continues from the failed result revision, keeps the same authority ref,
    repository and path scope (no widening) and the same lineage root, and consumes one attempt.
    """
    work.verify_seal()
    verdict.verify_seal()
    result.verify_seal()
    if verdict.verdict is not Verdict.FAIL:
        raise ContractError("repair is only materialized from a FAIL verdict")
    if (
        verdict.task_id != work.task_id
        or result.task_id != work.task_id
        or verdict.result_revision != result.result_revision
        or verdict.result_tree != result.result_tree
    ):
        raise ContractError("verdict does not judge this task's result")
    classes = tuple(classify_finding(f, work).value for f in verdict.findings)
    if FindingClass.OWNER_AUTHORITY_REQUIRED.value in classes:
        return RepairDecision(
            action="OWNER_REQUIRED", reason="OWNER_AUTHORITY_REQUIRED", classes=classes
        )
    if FindingClass.NON_REPAIRABLE.value in classes:
        return RepairDecision(action="BLOCKED", reason="NON_REPAIRABLE", classes=classes)
    if work.attempt >= work.max_attempts:
        return RepairDecision(action="BLOCKED", reason="ATTEMPT_CEILING_EXHAUSTED", classes=classes)
    nxt = make_work(
        task_id=f"{work.lineage_root}-R{work.attempt}",
        execution_id=f"{work.execution_id}-R{work.attempt}",
        lineage_root=work.lineage_root,
        parent_task_id=work.task_id,
        repository=work.repository,
        base_revision=result.result_revision,
        authority_ref=work.authority_ref,
        required_role=work.required_role,
        allowed_paths=work.allowed_paths,
        forbidden_paths=work.forbidden_paths,
        expected_outputs=work.expected_outputs,
        acceptance_contract=work.acceptance_contract
        + tuple(f"RESOLVE:{f.finding_id}" for f in verdict.findings),
        attempt=work.attempt + 1,
        max_attempts=work.max_attempts,
    )
    return RepairDecision(
        action="REPAIR", reason="REPAIRABLE_WITHIN_AUTHORITY", repair_work=nxt, classes=classes
    )
