"""AS-2.2-EVAL-001 — eval substrate holdout isolation + scoring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

from project_atlas.eval_substrate import (
    EVAL_HOLDOUT_EXPECTED_PATH_ENV,
    EVAL_SCORING_CAPABILITY_ENV,
    EvalSubstrateError,
    assert_path_readable,
    build_eval_score_receipt,
    holdout_root,
    list_case_files,
    load_cases,
    load_role_config,
    score_prediction,
    scoring_capability_granted,
)
from project_atlas.schema import available_schemas, validate_record

REPO_ROOT = Path(__file__).resolve().parents[2]


def _generate_holdout_expected(repo_root: Path) -> dict[str, str]:
    """Synthesize a private expected map at runtime from holdout metadata.

    The real holdout answers are operator-held secrets and must never live in the
    repo tree — not as a committed fixture and not as a hardcoded test literal
    (W2: HIDDEN_HOLDOUT_ISOLATION). The substrate is generic: it scores
    predictions against whatever private map the capability points at, so the
    tests invent their own deterministic expected values keyed by case id. No
    real answer string is embedded anywhere.
    """
    expected: dict[str, str] = {}
    for path in sorted((holdout_root(repo_root) / "cases").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        case_id = str(payload["case_id"])
        expected[case_id] = f"holdout-answer-{case_id.lower()}"
    return expected


@pytest.fixture
def scoring_capability(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """Arm scoring capability against a runtime-generated, gitignored map.

    Returns the generated expected map so tests assert via the map, not literals.
    """
    expected = _generate_holdout_expected(REPO_ROOT)
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    map_path = private_dir / "eval_holdout_expected.json"
    map_path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setenv(EVAL_SCORING_CAPABILITY_ENV, "1")
    monkeypatch.setenv(EVAL_HOLDOUT_EXPECTED_PATH_ENV, str(map_path))
    return expected


@pytest.fixture(autouse=True)
def clear_scoring_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EVAL_SCORING_CAPABILITY_ENV, raising=False)
    monkeypatch.delenv(EVAL_HOLDOUT_EXPECTED_PATH_ENV, raising=False)


def test_docs_and_schema_registered() -> None:
    assert (REPO_ROOT / "docs" / "AS-2.2-EVAL-001.md").is_file()
    assert "eval-score-receipt" in available_schemas()


#: Non-hidden cases every role may read (public + retired-holdout regression).
NON_HIDDEN_IDS = {"EV-PUB-001", "EV-PUB-002", "EV-REG-001", "EV-REG-002"}


def test_training_and_autolab_see_non_hidden_only() -> None:
    training = load_cases(REPO_ROOT, "training")
    autolab = load_cases(REPO_ROOT, "autolab")
    assert {c["case_id"] for c in training} == NON_HIDDEN_IDS
    assert {c["case_id"] for c in autolab} == NON_HIDDEN_IDS
    roles: tuple[Literal["training", "autolab"], ...] = ("training", "autolab")
    for role in roles:
        ids = {p.name for p in list_case_files(REPO_ROOT, role)}
        assert not any(name.startswith("EV-HOLD-") for name in ids)


def test_scoring_without_capability_is_non_hidden_only() -> None:
    assert not scoring_capability_granted()
    cases = load_cases(REPO_ROOT, "scoring")
    assert {c["case_id"] for c in cases} == NON_HIDDEN_IDS
    assert not any(str(c["case_id"]).startswith("EV-HOLD-") for c in cases)


def test_retired_holdouts_are_public_regression_not_hidden() -> None:
    """D-ULTRA-RESUME-010 §8: EV-HOLD-001/002 retired to PUBLIC regression."""
    for role in ("training", "autolab", "scoring"):
        cases = load_cases(REPO_ROOT, role)  # type: ignore[arg-type]
        retired = {c["case_id"]: c for c in cases if c.get("case_class") == "regression"}
        assert set(retired) == {"EV-REG-001", "EV-REG-002"}
        for case in retired.values():
            assert case["visibility"] == "public"
            assert case["visibility"] != "holdout"
            assert "retired_from" in case
    # The retired case files must not live under the hidden holdout root.
    hidden_names = {p.name for p in (holdout_root(REPO_ROOT) / "cases").glob("*.json")}
    assert not any(name.startswith("EV-HOLD-00") for name in hidden_names)


def test_scoring_sees_holdouts_with_capability(
    scoring_capability: dict[str, str],
) -> None:
    cases = load_cases(REPO_ROOT, "scoring")
    ids = {c["case_id"] for c in cases}
    assert "EV-PUB-001" in ids
    assert "EV-HOLD-101" in ids
    assert "EV-HOLD-102" in ids
    hold = next(c for c in cases if c["case_id"] == "EV-HOLD-101")
    assert hold["expected"] == scoring_capability["EV-HOLD-101"]


def test_holdout_git_case_files_have_no_plaintext_expected() -> None:
    for path in sorted((holdout_root(REPO_ROOT) / "cases").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "expected" not in payload, path.name


def test_holdout_path_blocked_for_training_autolab() -> None:
    hidden = holdout_root(REPO_ROOT) / "cases" / "EV-HOLD-101-exact.json"
    assert hidden.is_file()
    with pytest.raises(EvalSubstrateError, match="holdout-isolated:training"):
        assert_path_readable(REPO_ROOT, "training", hidden)
    with pytest.raises(EvalSubstrateError, match="holdout-isolated:autolab"):
        assert_path_readable(REPO_ROOT, "autolab", hidden)


def test_holdout_path_requires_capability_for_scoring() -> None:
    hidden = holdout_root(REPO_ROOT) / "cases" / "EV-HOLD-101-exact.json"
    with pytest.raises(EvalSubstrateError, match="holdout-capability-required"):
        assert_path_readable(REPO_ROOT, "scoring", hidden)


def test_training_config_rejects_holdout_root(tmp_path: Path) -> None:
    eval_root = tmp_path / "fixtures" / "eval"
    (eval_root / "public" / "cases").mkdir(parents=True)
    (eval_root / "holdouts" / "hidden" / "cases").mkdir(parents=True)
    (eval_root / "configs").mkdir(parents=True)
    (eval_root / "public" / "cases" / "EV-PUB-001-exact.json").write_text(
        json.dumps(
            {
                "case_id": "EV-PUB-001",
                "visibility": "public",
                "score_mode": "exact",
                "expected": "ok",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (eval_root / "configs" / "training.paths.json").write_text(
        json.dumps(
            {
                "role": "training",
                "package_id": "AS-2.2-EVAL-001",
                "case_roots": ["fixtures/eval/holdouts/hidden"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(EvalSubstrateError, match="holdout-isolated:training-config"):
        load_role_config(tmp_path, "training")


def test_deterministic_scoring_hooks() -> None:
    exact = score_prediction(expected="Validate-OK", predicted="validate-ok", mode="exact")
    assert exact["matched"] is True
    prefix = score_prediction(expected="discover-", predicted="discover-ok", mode="prefix")
    assert prefix["matched"] is True
    miss = score_prediction(expected="a", predicted="b", mode="exact")
    assert miss["matched"] is False


def test_score_receipt_public_only(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    receipt = build_eval_score_receipt(
        vault,
        record_id="eval-pub",
        repo_root=REPO_ROOT,
        predictions={
            "EV-PUB-001": "validate-ok",
            "EV-PUB-002": "discover-manifest",
        },
        include_holdouts=False,
    )
    assert receipt["holdouts_scored"] is False
    assert receipt["opt_woken"] is False
    assert receipt["cases_matched"] == 2
    validate_record(receipt, "eval-score-receipt")
    assert (vault / "generated" / "ops" / "eval" / "eval-pub.json").is_file()


def test_score_receipt_with_holdouts(
    tmp_path: Path,
    scoring_capability: dict[str, str],
) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    # Build predictions from the runtime-generated expected map so no real answer
    # literal appears in the test. Exact -> exact match; prefix -> extend it.
    cases = load_cases(REPO_ROOT, "scoring")
    predictions: dict[str, str] = {}
    for case in cases:
        cid = str(case["case_id"])
        exp = str(case["expected"])
        predictions[cid] = exp if case.get("score_mode") == "exact" else exp + "-ext"
    receipt = build_eval_score_receipt(
        vault,
        record_id="eval-hold",
        repo_root=REPO_ROOT,
        predictions=predictions,
        include_holdouts=True,
    )
    assert receipt["holdouts_scored"] is True
    assert receipt["holdout_case_count"] == 2
    # public(2) + retired-holdout regression(2) + hidden holdout(2) = 6.
    assert receipt["public_case_count"] == 4
    assert receipt["cases_scored"] == 6
    assert receipt["cases_matched"] == 6
    assert receipt["holdout_cases_scored"] == 2
    assert receipt["holdout_cases_matched"] == 2
    holdout_rows = [r for r in receipt["results"] if r["visibility"] == "holdout"]
    assert holdout_rows
    for row in holdout_rows:
        # Per-row holdout answer signal must be fully dropped (W2 hardening):
        # predicted_norm/matched/expected_norm reconstruct the answer key.
        assert "predicted_norm" not in row
        assert "matched" not in row
        assert "expected_norm" not in row
        assert row["expected_redacted"] is True
    validate_record(receipt, "eval-score-receipt")


def test_include_holdouts_requires_capability(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(EvalSubstrateError, match="holdout-capability-required"):
        build_eval_score_receipt(
            vault,
            record_id="eval-no-cap",
            repo_root=REPO_ROOT,
            predictions={},
            include_holdouts=True,
        )


def test_opt_wake_refused(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(EvalSubstrateError, match="opt-gated"):
        build_eval_score_receipt(
            vault,
            record_id="eval-opt",
            repo_root=REPO_ROOT,
            predictions={},
            wake_opt=True,
        )


def test_rl_prime_invent_refused(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    with pytest.raises(EvalSubstrateError, match="forbidden-claim:rl"):
        build_eval_score_receipt(
            vault,
            record_id="eval-rl",
            repo_root=REPO_ROOT,
            predictions={},
            rl=True,
        )
    with pytest.raises(EvalSubstrateError, match="forbidden-claim:prime"):
        build_eval_score_receipt(
            vault,
            record_id="eval-prime",
            repo_root=REPO_ROOT,
            predictions={},
            prime=True,
        )
    with pytest.raises(EvalSubstrateError, match="forbidden-claim:invent_pilot"):
        build_eval_score_receipt(
            vault,
            record_id="eval-pilot",
            repo_root=REPO_ROOT,
            predictions={},
            invent_pilot=True,
        )
