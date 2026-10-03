"""Repair-package support of the generic DEVQ package builder (``dev_package``).

Every repair WorkItem here is produced by the real ``dev_contracts.materialize_repair``; the
builder must reproduce it seal for seal and must never let a repair check out ``main``.
Synthetic identities only: nothing here builds, dispatches or resolves a real package.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from project_atlas.orchestration.autonomy.dev_contracts import (
    Finding,
    FindingCategory,
    ResultRecord,
    Verdict,
    WorkItem,
    make_result,
    make_verdict,
    make_verification_request,
    materialize_repair,
)
from project_atlas.orchestration.autonomy.dev_package import (
    FORBIDDEN_FLOOR,
    INSTRUCTIONS_PREFIX,
    REPAIR_SUFFIX,
    PackageSpecError,
    build_package,
    build_work,
    load_spec,
    package_sha256,
    render_package,
    sealed_instructions_sha256,
    spec_sha256,
    verify_checkout_ref,
)

MAIN_REVISION = "0123456789abcdef0123456789abcdef01234567"
RESULT_REVISION = "a1" * 20  # the FAILED result revision == the repair base
RESULT_TREE = "b2" * 20
OTHER_REVISION = "c3" * 20
RESULT_BRANCH = "atlas/agent-1000001-1"  # synthetic previous-result branch

# Parent: implementation attempt 2 of 3 (execution ordinal 2), as the builder produces it.
PARENT_SPEC: dict[str, Any] = {
    "task_id": "SYNTH-PKG-0001",
    "execution_ordinal": 2,
    "lineage_root": "SYNTH-PKG-0001",
    "repository": "example-owner/example-repo",
    "base_revision": MAIN_REVISION,
    "authority_ref": "TEST-AUTHORITY-SYNTHETIC",
    "allowed_paths": ["src/project_atlas/example.py", "tests/unit/"],
    "forbidden_paths": [*FORBIDDEN_FLOOR, "src/project_atlas/ingestion.py"],
    "expected_outputs": ["dedicated atlas/agent-* branch with exact HEAD/TREE"],
    "acceptance_contract": ["example behaviour fixed", "ruff and mypy clean on changed files"],
    "statement": "Fix the synthetic example defect in src/project_atlas/example.py.",
    "acceptance_commands": ["pytest tests/unit/x.py -q", "ruff check src tests", "mypy src"],
    "attempt": 2,
    "max_attempts": 3,
    "attempt_kind": "implementation",
}

# Pinned from the UNMODIFIED builder at main d9a36c922eb5411ca19816445cd8fcd2e5beece7
# (tests/unit/test_orchestration_dev_package.py BASE_SPEC; second row: attempt=2,
# execution_ordinal=2). Columns: package_sha256, work_seal, spec_sha256, workflow_inputs_sha256.
PRE_CHANGE_GOLDEN = {
    "attempt-1": (
        "6289f1ce5e9101370dabf76a0788803cc23b5e3670f11d946e82c80f292484cc",
        "d2a7642602ea4d3ae46f612552a20aef62a76c4feeb98c74e883edce6c379d49",
        "23981d51ab1e073b0ec5614d0fce3b3869ea387ba0266dba11d347ca64a1efad",
        "f210841310d061b14a61dfd3df722a5609fcd44e90334fbc79a9cbbf388b8d93",
    ),
    "attempt-2": (
        "e875867593ef8ab93851fbea39961da4ff9577c14293913066449758a8972f44",
        "b57299b219be3994a4e2f13898003b445f8c3b2c17ab6a7620ab883081b468a3",
        "7c641ba3d028b26d9e81b7b45a47c3177e23e83fd9339ffb9dec8925cf6fa54b",
        "fa662943859a047889303febfb51192b7ad6f1515ff134239ff5da2465751c19",
    ),
}


def _materialize(parent: WorkItem, *finding_ids: str) -> tuple[WorkItem, ResultRecord]:
    """Run the real contract path: result -> FAIL verdict -> materialize_repair."""
    result = make_result(
        parent,
        executor_identity="synthetic-executor",
        result_revision=RESULT_REVISION,
        result_tree=RESULT_TREE,
        changed_paths=("src/project_atlas/example.py",),
    )
    request = make_verification_request(parent, result, verifier_identity="synthetic-verifier")
    findings = tuple(
        Finding(
            finding_id=f,
            category=FindingCategory.DEFECT,
            paths=("src/project_atlas/example.py",),
        )
        for f in finding_ids
    )
    verdict = make_verdict(request, verdict=Verdict.FAIL, findings=findings)
    decision = materialize_repair(parent, verdict, result=result)
    assert decision.action == "REPAIR" and decision.repair_work is not None
    return decision.repair_work, result


def _parent() -> WorkItem:
    return build_work(load_spec(json.dumps(PARENT_SPEC)))


def _repair_spec(repair: WorkItem, parent: WorkItem, **over: Any) -> dict[str, Any]:
    """The JSON spec a caller writes to package ``repair``; values come from the WorkItem."""
    spec = {
        **PARENT_SPEC,
        "task_id": repair.task_id,
        "lineage_root": repair.lineage_root,
        "parent_task_id": repair.parent_task_id,
        "parent_execution_id": parent.execution_id,
        "base_revision": repair.base_revision,
        "base_branch": RESULT_BRANCH,
        "acceptance_contract": list(repair.acceptance_contract),
        "attempt": repair.attempt,
        "max_attempts": repair.max_attempts,
        "attempt_kind": "repair",
    }
    spec.update(over)
    return {k: v for k, v in spec.items() if v is not ...}


@pytest.fixture
def repair() -> tuple[WorkItem, WorkItem, dict[str, Any]]:
    parent = _parent()
    work, _ = _materialize(parent, "F-0001", "F-0002")
    return parent, work, _repair_spec(work, parent)


def _reason(spec: dict[str, Any]) -> str:
    with pytest.raises(PackageSpecError) as ei:
        build_package(load_spec(json.dumps(spec)))
    return ei.value.reason


def _verify_reason(package: dict[str, Any], sha: object) -> str:
    with pytest.raises(PackageSpecError) as ei:
        verify_checkout_ref(package, sha)
    return ei.value.reason


# -- A. the checkout branch must resolve exactly to the repair base ------------------------


def test_a_repair_checkout_branch_must_resolve_exactly_to_repair_base(repair: Any) -> None:
    _, work, spec = repair
    pkg = build_package(load_spec(json.dumps(spec)))
    assert work.base_revision == RESULT_REVISION != MAIN_REVISION
    assert pkg["base_revision"] == RESULT_REVISION
    assert pkg["workflow_inputs"]["base_branch"] == RESULT_BRANCH
    assert pkg["checkout"]["base_branch"] == RESULT_BRANCH
    assert pkg["checkout"]["required_revision"] == RESULT_REVISION
    assert pkg["abort_conditions"][0] == (
        f"{RESULT_BRANCH} is not at the sealed base revision before dispatch"
    )
    assert f"base revision {RESULT_REVISION}" in pkg["workflow_inputs"]["task_prompt"]
    assert verify_checkout_ref(pkg, RESULT_REVISION) is None
    # the rendered document verifies the same way as the in-memory package
    assert verify_checkout_ref(json.loads(render_package(pkg)), RESULT_REVISION) is None


def test_a_implementation_package_verifies_only_against_main_at_sealed_base() -> None:
    pkg = build_package(load_spec(json.dumps(PARENT_SPEC)))
    assert pkg["workflow_inputs"]["base_branch"] == "main"
    assert verify_checkout_ref(pkg, MAIN_REVISION) is None
    assert _verify_reason(pkg, RESULT_REVISION) == "CHECKOUT_REF_MISMATCH"


# -- B. mismatched branch / base fails closed ----------------------------------------------


@pytest.mark.parametrize(
    ("sha", "reason"),
    [
        (OTHER_REVISION, "CHECKOUT_REF_MISMATCH"),
        (MAIN_REVISION, "CHECKOUT_REF_MISMATCH"),  # branch still at main's revision
        (RESULT_REVISION[:12], "CHECKOUT_SHA_INVALID"),
        (RESULT_REVISION.upper(), "CHECKOUT_SHA_INVALID"),
        (RESULT_REVISION + "\n", "CHECKOUT_SHA_INVALID"),
        (" " + RESULT_REVISION, "CHECKOUT_SHA_INVALID"),
        (RESULT_REVISION + "0", "CHECKOUT_SHA_INVALID"),
        ("", "CHECKOUT_SHA_INVALID"),
        (None, "CHECKOUT_SHA_INVALID"),
        (RESULT_REVISION.encode(), "CHECKOUT_SHA_INVALID"),
        (int(RESULT_REVISION, 16), "CHECKOUT_SHA_INVALID"),
        (True, "CHECKOUT_SHA_INVALID"),
    ],
    ids=[
        "wrong",
        "main-rev",
        "short",
        "upper",
        "newline",
        "padded",
        "long",
        "empty",
        "none",
        "bytes",
        "int",
        "bool",
    ],
)
def test_b_mismatched_or_malformed_resolved_sha_fails_closed(
    repair: Any, sha: object, reason: str
) -> None:
    pkg = build_package(load_spec(json.dumps(repair[2])))
    assert _verify_reason(pkg, sha) == reason


@pytest.mark.parametrize(
    ("branch", "reason"),
    [
        ("main", "REPAIR_BASE_BRANCH_IS_MAIN"),
        (..., "REPAIR_BASE_BRANCH_MISSING"),  # key absent: never defaults to main
        (None, "REPAIR_BASE_BRANCH_MISSING"),
        (7, "SPEC_TYPE"),
        ("", "BASE_BRANCH_INVALID"),
        ("atlas/agent-1-1/../main", "BASE_BRANCH_INVALID"),
        ("atlas/..", "BASE_BRANCH_INVALID"),
        ("/atlas/agent-1-1", "BASE_BRANCH_INVALID"),
        ("atlas/agent-1-1.git", "BASE_BRANCH_INVALID"),
        ("atlas/agent 1-1", "BASE_BRANCH_INVALID"),
        ("atlas/agent-1-1\n", "BASE_BRANCH_INVALID"),
        ("atlas/agent-1-1;id", "BASE_BRANCH_INVALID"),
        ("atlas/agent-$(id)-1", "BASE_BRANCH_INVALID"),
        ("atlas/agent-\u0661-1", "BASE_BRANCH_INVALID"),  # non-ASCII digit
        ("Main", "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),
        ("refs/heads/main", "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),
        ("master", "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),
        ("feature/x", "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),
        ("atlas/agent-1", "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),
        ("atlas/agent-1-1/x", "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),
        (RESULT_REVISION, "REPAIR_BASE_BRANCH_NOT_RESULT_BRANCH"),  # a sha is not a branch
    ],
)
def test_b_repair_base_branch_rejections(repair: Any, branch: object, reason: str) -> None:
    parent, work, _ = repair
    assert _reason(_repair_spec(work, parent, base_branch=branch)) == reason


def test_b_tampered_package_fails_closed_even_with_the_right_sha(repair: Any) -> None:
    pkg = build_package(load_spec(json.dumps(repair[2])))

    def tampered(mutate: Any) -> dict[str, Any]:
        p = copy.deepcopy(pkg)
        mutate(p)
        return p

    cases: list[tuple[Any, str, str]] = [
        # branch swapped to main without re-hashing the inputs
        (
            lambda p: p["workflow_inputs"].update(base_branch="main"),
            RESULT_REVISION,
            "CHECKOUT_PACKAGE_INVALID",
        ),
        (
            lambda p: p.update(base_revision=MAIN_REVISION),
            MAIN_REVISION,
            "CHECKOUT_PACKAGE_INVALID",
        ),
        (lambda p: p.update(base_revision="main"), RESULT_REVISION, "CHECKOUT_PACKAGE_INVALID"),
        (lambda p: p.pop("checkout"), RESULT_REVISION, "CHECKOUT_PACKAGE_INVALID"),
        (lambda p: p.pop("workflow_inputs"), RESULT_REVISION, "CHECKOUT_PACKAGE_INVALID"),
        (lambda p: p.pop("attempt_kind"), RESULT_REVISION, "CHECKOUT_PACKAGE_INVALID"),
        (lambda p: p.update(parent_task_id=None), RESULT_REVISION, "CHECKOUT_PACKAGE_INVALID"),
        (
            lambda p: p["checkout"].update(required_revision=OTHER_REVISION),
            RESULT_REVISION,
            "CHECKOUT_PACKAGE_INVALID",
        ),
        # relabelled as an implementation while still pointing at a result branch
        (
            lambda p: p.update(attempt_kind="implementation"),
            RESULT_REVISION,
            "CHECKOUT_PACKAGE_INVALID",
        ),
    ]
    for mutate, sha, reason in cases:
        assert _verify_reason(tampered(mutate), sha) == reason
    assert _verify_reason({}, RESULT_REVISION) == "CHECKOUT_PACKAGE_INVALID"


def test_b_repair_package_consistently_rehashed_to_main_is_still_refused(repair: Any) -> None:
    """Even a self-consistent forgery (inputs re-hashed) cannot make a repair check out main."""
    from project_atlas.orchestration.autonomy.dev_fabric_adapter import DispatchPayload

    pkg = build_package(load_spec(json.dumps(repair[2])))
    pkg["workflow_inputs"]["base_branch"] = "main"
    pkg["checkout"]["base_branch"] = "main"
    pkg["workflow_inputs_sha256"] = DispatchPayload(
        workflow=pkg["workflow"], ref=pkg["workflow_ref"], inputs=pkg["workflow_inputs"]
    ).sha256()
    assert _verify_reason(pkg, RESULT_REVISION) == "REPAIR_BASE_BRANCH_IS_MAIN"


def test_b_implementation_spec_cannot_carry_repair_fields() -> None:
    for name, value in (
        ("base_branch", RESULT_BRANCH),
        ("base_branch", "main"),
        ("parent_task_id", "SYNTH-PKG-0001"),
        ("parent_execution_id", "SYNTH-PKG-0001-E1"),
    ):
        assert _reason({**PARENT_SPEC, name: value}) == "IMPLEMENTATION_REPAIR_FIELD"


# -- C. parent_task_id survives ------------------------------------------------------------


def test_c_parent_task_id_survives_into_work_item_and_rendered_package(repair: Any) -> None:
    parent, work, spec = repair
    built = build_work(load_spec(json.dumps(spec)))
    assert work.parent_task_id == parent.task_id
    assert built.parent_task_id == work.parent_task_id
    rendered = json.loads(render_package(build_package(load_spec(json.dumps(spec)))))
    assert rendered["parent_task_id"] == work.parent_task_id
    assert rendered["parent_execution_id"] == parent.execution_id
    assert rendered["lineage_root"] == work.lineage_root
    assert rendered["attempt_kind"] == "repair"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"parent_task_id": ...}, "REPAIR_PARENT_MISSING"),
        ({"parent_task_id": None}, "REPAIR_PARENT_MISSING"),
        ({"parent_execution_id": ...}, "REPAIR_PARENT_MISSING"),
        ({"parent_task_id": ""}, "REPAIR_PARENT_INVALID"),
        ({"parent_task_id": "has space"}, "REPAIR_PARENT_INVALID"),
        ({"parent_execution_id": "../x"}, "REPAIR_PARENT_INVALID"),
        ({"parent_task_id": 3}, "SPEC_TYPE"),
        ({"task_id": "SYNTH-PKG-0001"}, "REPAIR_IDENTITY_MISMATCH"),
        ({"task_id": "SYNTH-PKG-0001-R1"}, "REPAIR_IDENTITY_MISMATCH"),
        ({"task_id": "SYNTH-PKG-0001-R3"}, "REPAIR_IDENTITY_MISMATCH"),
        ({"lineage_root": "OTHER-ROOT"}, "REPAIR_IDENTITY_MISMATCH"),
    ],
)
def test_c_repair_parent_and_identity_rejections(
    repair: Any, over: dict[str, Any], reason: str
) -> None:
    parent, work, _ = repair
    assert _reason(_repair_spec(work, parent, **over)) == reason


def test_c_wrong_parent_changes_the_seal(repair: Any) -> None:
    """parent_task_id / parent_execution_id are sealed: a wrong one is a different WorkItem."""
    _, work, spec = repair
    for over in (
        {"parent_task_id": "SYNTH-PKG-0009"},
        {"parent_execution_id": "SYNTH-PKG-0001-E9"},
    ):
        other = build_work(load_spec(json.dumps({**spec, **over})))
        assert other.seal != work.seal


# -- D. the packaged WorkItem is seal-identical to the materialized repair ------------------


def test_d_packaged_work_is_seal_identical_to_materialized_repair(repair: Any) -> None:
    parent, work, spec = repair
    built = build_work(load_spec(json.dumps(spec)))
    built.verify_seal()
    assert built == work
    assert built.seal == work.seal == work.compute_seal() == built.compute_seal()
    assert built.model_dump(mode="json") == work.model_dump(mode="json")
    for name in (
        "lineage_root",
        "authority_ref",
        "repository",
        "required_role",
        "allowed_paths",
        "forbidden_paths",
        "expected_outputs",
        "max_attempts",
    ):
        assert getattr(built, name) == getattr(work, name) == getattr(parent, name), name
    assert built.acceptance_contract == work.acceptance_contract
    assert built.acceptance_contract == (
        *parent.acceptance_contract,
        "RESOLVE:F-0001",
        "RESOLVE:F-0002",
    )
    pkg = build_package(load_spec(json.dumps(spec)))
    assert pkg["work_seal"] == work.seal
    assert pkg["acceptance"]["contract"] == list(work.acceptance_contract)
    assert pkg["allowed_paths"] == list(work.allowed_paths)
    assert pkg["forbidden_paths"] == list(work.forbidden_paths)
    assert pkg["authority_reference"] == work.authority_ref
    assert f"seal {work.seal}" in pkg["workflow_inputs"]["task_prompt"]


@pytest.mark.parametrize(
    "over",
    [
        {"authority_ref": "TEST-AUTHORITY-WIDER"},
        {"allowed_paths": ["src/project_atlas/example.py", "tests/unit/", "docs/"]},
        {"forbidden_paths": [*FORBIDDEN_FLOOR]},
        {"expected_outputs": ["something else"]},
        {"max_attempts": 4},
        {"repository": "example-owner/other-repo"},
        {"base_revision": OTHER_REVISION},
    ],
    ids=["authority", "allowed", "forbidden", "outputs", "ceiling", "repository", "base"],
)
def test_d_any_authority_or_scope_drift_breaks_seal_equality(
    repair: Any, over: dict[str, Any]
) -> None:
    """The builder cannot widen silently: a drifted spec is a DIFFERENT seal, never this one."""
    _, work, spec = repair
    assert build_work(load_spec(json.dumps({**spec, **over}))).seal != work.seal


def test_d_repair_cannot_change_the_sealed_instructions(repair: Any) -> None:
    _, work, spec = repair
    digest = INSTRUCTIONS_PREFIX + sealed_instructions_sha256(load_spec(json.dumps(PARENT_SPEC)))
    assert digest in work.acceptance_contract  # inherited from the sealed parent
    assert (
        _reason({**spec, "statement": "Do something else entirely."})
        == "REPAIR_INSTRUCTIONS_MISMATCH"
    )
    assert (
        _reason({**spec, "acceptance_commands": ["pytest tests/unit -q"]})
        == "REPAIR_INSTRUCTIONS_MISMATCH"
    )
    contract = list(work.acceptance_contract)
    forged = [INSTRUCTIONS_PREFIX + "0" * 64 if c == digest else c for c in contract]
    assert _reason({**spec, "acceptance_contract": forged}) == "REPAIR_INSTRUCTIONS_MISMATCH"
    without = [c for c in contract if c != digest]
    assert _reason({**spec, "acceptance_contract": without}) == "REPAIR_INSTRUCTIONS_MISSING"
    doubled = [*contract[:-2], digest.upper(), *contract[-2:]]
    assert _reason({**spec, "acceptance_contract": doubled}) == "REPAIR_INSTRUCTIONS_MISSING"
    trailing = [*contract, "one more requirement"]
    assert _reason({**spec, "acceptance_contract": trailing}) == "REPAIR_CONTRACT_SHAPE"
    # the executor still gets the sealed statement plus only the fixed repair suffix
    prompt = build_package(load_spec(json.dumps(spec)))["workflow_inputs"]["task_prompt"]
    assert PARENT_SPEC["statement"] + REPAIR_SUFFIX in prompt
    assert all(f"  - run: {c}" in prompt for c in PARENT_SPEC["acceptance_commands"])
    assert "  - RESOLVE:F-0001" in prompt and "  - RESOLVE:F-0002" in prompt


def test_d_implementation_spec_still_cannot_supply_an_instructions_entry() -> None:
    forged = [*PARENT_SPEC["acceptance_contract"], INSTRUCTIONS_PREFIX + "0" * 64]
    assert _reason({**PARENT_SPEC, "acceptance_contract": forged}) == "CONTRACT_RESERVED"


# -- E. attempt ceiling and identities -----------------------------------------------------


def test_e_repair_from_attempt_2_of_3_is_attempt_3_of_3_with_r2_identities(repair: Any) -> None:
    parent, work, spec = repair
    assert (parent.attempt, parent.max_attempts) == (2, 3)
    # what materialize_repair actually generated
    assert (work.attempt, work.max_attempts) == (3, 3)
    assert work.task_id == f"{parent.lineage_root}-R2" == "SYNTH-PKG-0001-R2"
    assert work.execution_id == f"{parent.execution_id}-R2" == "SYNTH-PKG-0001-E2-R2"
    # and what the builder derives for the package
    loaded = load_spec(json.dumps(spec))
    assert loaded.execution_id == work.execution_id
    pkg = build_package(loaded)
    assert (pkg["task_id"], pkg["execution_id"]) == (work.task_id, work.execution_id)
    assert pkg["failure_ceiling"]["max_attempts"] == 3
    assert "attempt 3/3" in pkg["workflow_inputs"]["task_prompt"]
    # execution identity is derived from the parent, never supplied
    assert _reason({**spec, "execution_id": work.execution_id}) == "SPEC_UNKNOWN_KEY"
    # the ceiling is exhausted: there is no attempt 4 to package
    assert _reason({**spec, "attempt": 4, "task_id": "SYNTH-PKG-0001-R3"}) == "ATTEMPT_INVALID"


def test_e_chained_repair_of_a_repair_is_also_seal_identical() -> None:
    """Attempt 1 -> repair R1 (attempt 2) -> repair R2 (attempt 3): both package faithfully."""
    first_spec = {**PARENT_SPEC, "attempt": 1, "execution_ordinal": 1}
    first = build_work(load_spec(json.dumps(first_spec)))
    r1, _ = _materialize(first, "F-0001")
    r1_built = build_work(load_spec(json.dumps(_repair_spec(r1, first))))
    assert r1_built.seal == r1.seal
    assert (r1.task_id, r1.execution_id, r1.attempt) == (
        "SYNTH-PKG-0001-R1",
        "SYNTH-PKG-0001-E1-R1",
        2,
    )
    r2, _ = _materialize(r1_built, "F-0002")
    r2_built = build_work(load_spec(json.dumps(_repair_spec(r2, r1))))
    assert r2_built.seal == r2.seal
    assert (r2.task_id, r2.execution_id, r2.parent_task_id, r2.attempt) == (
        "SYNTH-PKG-0001-R2",
        "SYNTH-PKG-0001-E1-R1-R2",
        "SYNTH-PKG-0001-R1",
        3,
    )


# -- F. implementation packages are unchanged ----------------------------------------------


@pytest.mark.parametrize(
    ("key", "over"),
    [("attempt-1", {"attempt": 1, "execution_ordinal": 1}), ("attempt-2", {})],
)
def test_f_implementation_package_bytes_unchanged_since_d9a36c92(
    key: str, over: dict[str, Any]
) -> None:
    # identical to BASE_SPEC of test_orchestration_dev_package.py at d9a36c92
    golden_commands = [
        "pytest tests/unit/x.py -q",
        "ruff check src tests",
        "ruff format --check src tests",
        "mypy src",
    ]
    spec = load_spec(json.dumps({**PARENT_SPEC, "acceptance_commands": golden_commands, **over}))
    pkg = build_package(spec)
    rendered = render_package(pkg)
    assert (
        package_sha256(rendered),
        pkg["work_seal"],
        spec_sha256(spec),
        pkg["workflow_inputs_sha256"],
    ) == PRE_CHANGE_GOLDEN[key]
    assert pkg["workflow_inputs"]["base_branch"] == "main"
    assert not {"checkout", "parent_task_id", "parent_execution_id", "lineage_root"} & set(pkg)
    assert build_work(spec).parent_task_id is None


def test_f_null_repair_fields_equal_absent_fields_for_implementation() -> None:
    absent = load_spec(json.dumps(PARENT_SPEC))
    nulls = load_spec(
        json.dumps(
            {
                **PARENT_SPEC,
                "parent_task_id": None,
                "parent_execution_id": None,
                "base_branch": None,
            }
        )
    )
    assert absent == nulls and spec_sha256(absent) == spec_sha256(nulls)
    assert render_package(build_package(absent)) == render_package(build_package(nulls))


# -- G. deterministic rendering ------------------------------------------------------------


def test_g_repair_rendering_is_deterministic_and_key_order_independent(repair: Any) -> None:
    spec = repair[2]
    text = json.dumps(spec)
    reordered = json.dumps(dict(reversed(list(spec.items()))))
    assert text != reordered
    r1 = render_package(build_package(load_spec(text)))
    r2 = render_package(build_package(load_spec(text)))
    r3 = render_package(build_package(load_spec(reordered)))
    assert r1 == r2 == r3 and r1.endswith("\n")
    assert package_sha256(r1) == package_sha256(r3)
    # rendered key order is stable (sorted at every level), independent of insertion order
    assert r1 == json.dumps(json.loads(r1), indent=2, sort_keys=True) + "\n"


def test_g_repair_spec_fields_are_bound_into_the_package_hash(repair: Any) -> None:
    spec = repair[2]
    base = build_package(load_spec(json.dumps(spec)))
    moved = build_package(load_spec(json.dumps({**spec, "base_branch": "atlas/agent-1000002-1"})))
    assert base["work_seal"] == moved["work_seal"]  # the branch name is not part of the seal
    assert base["workflow_inputs_sha256"] != moved["workflow_inputs_sha256"]
    assert base["provenance"]["spec_sha256"] != moved["provenance"]["spec_sha256"]
    assert package_sha256(render_package(base)) != package_sha256(render_package(moved))
