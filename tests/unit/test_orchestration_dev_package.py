from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.autonomy import dev_first_run
from project_atlas.orchestration.autonomy.dev_package import (
    FORBIDDEN_FLOOR,
    PackageSpecError,
    build_package,
    build_work,
    load_spec,
    package_sha256,
    render_package,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Synthetic task identity only: this suite never builds a real DEVQ package.
BASE_SPEC: dict[str, Any] = {
    "task_id": "SYNTH-PKG-0001",
    "execution_ordinal": 1,
    "lineage_root": "SYNTH-PKG-0001",
    "repository": "example-owner/example-repo",
    "base_revision": "0123456789abcdef0123456789abcdef01234567",
    "authority_ref": "TEST-AUTHORITY-SYNTHETIC",
    "allowed_paths": ["src/project_atlas/example.py", "tests/unit/"],
    "forbidden_paths": [*FORBIDDEN_FLOOR, "src/project_atlas/ingestion.py"],
    "expected_outputs": ["dedicated atlas/agent-* branch with exact HEAD/TREE"],
    "acceptance_contract": ["example behaviour fixed", "ruff and mypy clean on changed files"],
    "statement": "Fix the synthetic example defect in src/project_atlas/example.py.",
    "acceptance_commands": [
        "pytest tests/unit/x.py -q",
        "ruff check src tests",
        "ruff format --check src tests",
        "mypy src",
    ],
    "attempt": 1,
    "max_attempts": 3,
}


def _spec_text(**over: Any) -> str:
    return json.dumps({**BASE_SPEC, **over})


def _render(text: str) -> str:
    return render_package(build_package(load_spec(text)))


def _reason(text: str) -> str:
    with pytest.raises(PackageSpecError) as ei:
        build_package(load_spec(text))
    assert isinstance(ei.value, ValueError)
    return ei.value.reason


def _keys(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _keys(v) for k, v in obj.items()}
    return type(obj).__name__


# -- determinism / identity ----------------------------------------------------------------


def test_same_spec_text_yields_byte_identical_package() -> None:
    text = _spec_text()
    r1, r2 = _render(text), _render(text)
    assert r1 == r2 and r1.endswith("\n")
    assert package_sha256(r1) == package_sha256(r2)
    p1, p2 = json.loads(r1), json.loads(r2)
    assert p1["work_seal"] == p2["work_seal"]
    assert p1["workflow_inputs_sha256"] == p2["workflow_inputs_sha256"]
    assert p1["execution_id"] == "SYNTH-PKG-0001-E1"


def test_key_order_of_spec_does_not_change_output() -> None:
    reordered = json.dumps(dict(reversed(list(BASE_SPEC.items()))))
    assert reordered != _spec_text()
    assert _render(reordered) == _render(_spec_text())


@pytest.mark.parametrize(
    "over",
    [
        {"execution_ordinal": 2},
        {"base_revision": "fedcba9876543210fedcba9876543210fedcba98"},
    ],
)
def test_identity_inputs_change_seal_inputs_and_package_hash(over: dict[str, Any]) -> None:
    a = json.loads(_render(_spec_text()))
    b_text = _render(_spec_text(**over))
    b = json.loads(b_text)
    assert a["work_seal"] != b["work_seal"]
    assert a["workflow_inputs_sha256"] != b["workflow_inputs_sha256"]
    assert package_sha256(_render(_spec_text())) != package_sha256(b_text)
    assert a["provenance"]["spec_sha256"] != b["provenance"]["spec_sha256"]


def test_execution_id_is_derived_and_cannot_be_supplied() -> None:
    assert load_spec(_spec_text(execution_ordinal=7)).execution_id == "SYNTH-PKG-0001-E7"
    assert _reason(_spec_text(execution_id="SYNTH-PKG-0001-E9")) == "SPEC_UNKNOWN_KEY"


# -- structure / provenance / secrets ------------------------------------------------------


def test_package_structure_matches_first_run_plus_provenance() -> None:
    pkg = build_package(load_spec(_spec_text()))
    ref = dev_first_run.build_package()
    assert set(pkg) == set(ref) | {"provenance"}
    for k in ref:
        if k != "secrets":
            assert _keys(pkg[k]) == _keys(ref[k]), k
    assert pkg["workflow"] == "atlas-agent-execute.yml" and pkg["workflow_ref"] == "main"
    assert pkg["workflow_inputs"]["base_branch"] == "main"
    assert pkg["acceptance"]["commands"] == BASE_SPEC["acceptance_commands"]


def test_package_has_provenance_and_never_asserts_secret_presence() -> None:
    spec = load_spec(_spec_text())
    pkg = build_package(spec)
    text = render_package(pkg)
    assert "CONFIRMED_PRESENT" not in text
    assert pkg["secrets"]["ANTHROPIC_API_KEY"].startswith("NOT_ASSERTED")
    assert pkg["provenance"]["builder"] == "dev_package/1"
    canon = json.dumps(
        {k: list(v) if isinstance(v, list) else v for k, v in BASE_SPEC.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert pkg["provenance"]["spec_sha256"] == hashlib.sha256(canon.encode()).hexdigest()
    assert "sk-ant" not in text and "ghp_" not in text


def test_build_work_is_sealed_and_repair_attempt_marks_statement() -> None:
    w = build_work(load_spec(_spec_text()))
    w.verify_seal()
    assert w.execution_id == "SYNTH-PKG-0001-E1" and w.attempt == 1
    repair = build_package(load_spec(_spec_text(attempt=2)))
    assert "REPAIR attempt" in repair["workflow_inputs"]["task_prompt"]
    first = build_package(load_spec(_spec_text()))
    assert "REPAIR attempt" not in first["workflow_inputs"]["task_prompt"]


# -- acceptance commands -------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "pytest tests/unit/x.py -q",
        "ruff check src tests",
        "ruff format --check src tests",
        "mypy src",
        "pytest tests/unit -k 'f14 or graph_projection' -q --no-cov",
    ],
)
def test_canonical_commands_accepted(cmd: str) -> None:
    pkg = build_package(load_spec(_spec_text(acceptance_commands=[cmd])))
    assert pkg["acceptance"]["commands"] == [cmd]


@pytest.mark.parametrize(
    ("cmd", "reason"),
    [
        ("python -m pytest tests/unit -q", "COMMAND_PYTHON_M"),
        ("python3 -m mypy src", "COMMAND_PYTHON_M"),
        ("PYTHONPATH=src pytest tests/unit -q", "COMMAND_ENV_PREFIX"),
        ("pytest tests/unit; rm -rf /", "COMMAND_METACHAR"),
        ("pytest tests/unit && rm -rf /", "COMMAND_METACHAR"),
        ("pytest tests/unit | tee out", "COMMAND_METACHAR"),
        ("pytest `whoami`", "COMMAND_METACHAR"),
        ("pytest $HOME", "COMMAND_METACHAR"),
        ("pytest > out.txt", "COMMAND_METACHAR"),
        ("pytest < in.txt", "COMMAND_METACHAR"),
        ("pytest tests\nrm -rf /", "COMMAND_METACHAR"),
        ("pytest tests\\x", "COMMAND_METACHAR"),
        ("bash -c 'pytest'", "COMMAND_NOT_ALLOWED"),
        ("/usr/bin/pytest tests", "COMMAND_NOT_ALLOWED"),
        ("rm -rf /", "COMMAND_NOT_ALLOWED"),
        ("'pytest' tests", "COMMAND_NOT_ALLOWED"),
        (" pytest tests", "COMMAND_NOT_ALLOWED"),
        ("pytest 'unterminated", "COMMAND_UNPARSEABLE"),
        ("   ", "COMMAND_EMPTY"),
        ("pytest " + "x" * 450, "COMMAND_TOO_LONG"),
    ],
)
def test_rejected_commands(cmd: str, reason: str) -> None:
    assert _reason(_spec_text(acceptance_commands=[cmd])) == reason


def test_commands_empty_and_too_many() -> None:
    assert _reason(_spec_text(acceptance_commands=[])) == "COMMANDS_EMPTY"
    many = [f"pytest tests/unit/t{i}.py" for i in range(17)]
    assert _reason(_spec_text(acceptance_commands=many)) == "COMMANDS_TOO_MANY"


# -- strict JSON ---------------------------------------------------------------------------


def test_duplicate_json_keys_rejected() -> None:
    text = _spec_text()[:-1] + ', "attempt": 1}'
    assert _reason(text) == "SPEC_DUPLICATE_KEY"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "SPEC_JSON_INVALID"),
        ("[]", "SPEC_NOT_OBJECT"),
        (json.dumps({**BASE_SPEC, "extra": 1}), "SPEC_UNKNOWN_KEY"),
        (json.dumps({k: v for k, v in BASE_SPEC.items() if k != "statement"}), "SPEC_MISSING_KEY"),
        (_spec_text(execution_ordinal=True), "SPEC_TYPE"),
        (_spec_text(attempt=True), "SPEC_TYPE"),
        (_spec_text(max_attempts=3.0), "SPEC_TYPE"),
        (_spec_text(execution_ordinal="1"), "SPEC_TYPE"),
        (_spec_text(allowed_paths="tests/unit/"), "SPEC_TYPE"),
        (_spec_text(allowed_paths=["tests/unit/", 3]), "SPEC_TYPE"),
        (_spec_text(statement=None), "SPEC_TYPE"),
        (_spec_text()[:-1] + ', "x": NaN}', "SPEC_JSON_INVALID"),
    ],
)
def test_strict_json_rejections(text: str, reason: str) -> None:
    assert _reason(text) == reason


# -- identity fields -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"base_revision": "0123456789ABCDEF0123456789ABCDEF01234567"}, "BASE_REVISION_INVALID"),
        ({"base_revision": "0123456"}, "BASE_REVISION_INVALID"),
        ({"base_revision": "main"}, "BASE_REVISION_INVALID"),
        ({"repository": "project-atlas"}, "REPOSITORY_INVALID"),
        ({"repository": "a/b/c"}, "REPOSITORY_INVALID"),
        ({"repository": "owner/.."}, "REPOSITORY_INVALID"),
        ({"task_id": ""}, "TASK_ID_INVALID"),
        ({"task_id": "bad id"}, "TASK_ID_INVALID"),
        ({"lineage_root": "../x"}, "TASK_ID_INVALID"),
        ({"authority_ref": ""}, "AUTHORITY_INVALID"),
        ({"execution_ordinal": 0}, "ORDINAL_INVALID"),
        ({"execution_ordinal": -1}, "ORDINAL_INVALID"),
        ({"attempt": 0}, "ATTEMPT_INVALID"),
        ({"attempt": 4, "max_attempts": 3}, "ATTEMPT_INVALID"),
        ({"max_attempts": 0}, "ATTEMPT_INVALID"),
        ({"max_attempts": 11, "attempt": 1}, "ATTEMPT_INVALID"),
        ({"statement": ""}, "STATEMENT_EMPTY"),
        ({"statement": "   \n "}, "STATEMENT_EMPTY"),
        ({"statement": "x" * 8001}, "STATEMENT_TOO_LONG"),
        ({"statement": "fix \x00 this"}, "STATEMENT_INVALID"),
        ({"acceptance_contract": []}, "LIST_EMPTY"),
        ({"expected_outputs": []}, "LIST_EMPTY"),
    ],
)
def test_identity_and_bounds_rejections(over: dict[str, Any], reason: str) -> None:
    assert _reason(_spec_text(**over)) == reason


# -- scope ---------------------------------------------------------------------------------


@pytest.mark.parametrize("floor", FORBIDDEN_FLOOR)
def test_missing_forbidden_floor_entry_rejected(floor: str) -> None:
    forbidden = [f for f in BASE_SPEC["forbidden_paths"] if f != floor]
    assert _reason(_spec_text(forbidden_paths=forbidden)) == "FORBIDDEN_FLOOR_MISSING"


def test_floor_entry_matches_with_or_without_trailing_slash() -> None:
    forbidden = [f.rstrip("/") for f in BASE_SPEC["forbidden_paths"]]
    build_package(load_spec(_spec_text(forbidden_paths=forbidden)))


@pytest.mark.parametrize(
    ("allowed", "reason"),
    [
        ([], "ALLOWED_PATHS_EMPTY"),
        (["/etc/passwd"], "PATH_INVALID"),
        (["../outside"], "PATH_INVALID"),
        (["tests/../.github/workflows"], "PATH_INVALID"),
        (["src/*.py"], "PATH_INVALID"),
        ([".github/workflows/x.yml"], "SCOPE_OVERLAP"),  # allowed under forbidden
        (["src/"], "SCOPE_OVERLAP"),  # allowed is a parent of forbidden trust.py
        (["infra/atlas-runner/"], "SCOPE_OVERLAP"),
        (["autonomy"], "SCOPE_OVERLAP"),
        (["tests/unit/", "tests/unit/"], "LIST_DUPLICATE"),
    ],
)
def test_allowed_path_rejections(allowed: list[str], reason: str) -> None:
    assert _reason(_spec_text(allowed_paths=allowed)) == reason


def test_forbidden_path_must_be_well_formed() -> None:
    forbidden = [*BASE_SPEC["forbidden_paths"], "../x"]
    assert _reason(_spec_text(forbidden_paths=forbidden)) == "PATH_INVALID"


def test_prefix_overlap_is_segment_wise_not_string_wise() -> None:
    # "autonomy-docs/" is not under the floor "autonomy/".
    build_package(load_spec(_spec_text(allowed_paths=["autonomy-docs/"])))


# -- DEVQ-0001 regression ------------------------------------------------------------------


def test_dev_first_run_package_unchanged_and_module_untouched() -> None:
    committed = REPO_ROOT / "docs/autonomy/first-run/ATLAS-DEVQ-0001.package.json"
    rendered = dev_first_run.render_package(dev_first_run.build_package())
    assert json.loads(rendered) == json.loads(committed.read_text())
    assert "CONFIRMED_PRESENT" in rendered  # DEVQ-0001 keeps its owner-statement wording
    proc = subprocess.run(
        ["git", "diff", "--quiet", "origin/main", "--", dev_first_run.__file__],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if proc.returncode not in (0, 1):  # no git / no origin/main: the byte check above suffices
        pytest.skip("git origin/main unavailable")
    assert proc.returncode == 0, "dev_first_run.py must stay byte-identical to origin/main"
