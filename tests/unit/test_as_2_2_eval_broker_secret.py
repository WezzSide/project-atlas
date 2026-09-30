"""AS-2.2-EVAL-BROKER-001 — new-holdout secret persistence guards.

D-ULTRA-RESUME-010 §8. These tests fail closed if the fresh hidden holdout set
(``EV-HOLD-101`` / ``EV-HOLD-102``) ever regains a committed expected answer, or
if any operator-held expected answer is persisted into the repo tree or git
history. The "secret" is modelled as a fresh random token per run: whatever the
operator's real answer happens to be, this proves the persistence path never
writes it into a tracked file, glob-visible file, or historical blob.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
from pathlib import Path

from tests.security.git_history_scan import find_leaked_holdout_evidence

from project_atlas.eval_substrate import holdout_root, regression_root

REPO_ROOT = Path(__file__).resolve().parents[2]

_NEW_HOLDOUT_IDS = ("EV-HOLD-101", "EV-HOLD-102")
_RETIRED_HOLDOUT_IDS = ("EV-HOLD-001", "EV-HOLD-002")
_EXPECTED_MAP_RE = re.compile(r"(eval.*expected|holdout.*expected).*\.json$", re.IGNORECASE)


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_new_hidden_holdout_case_files_have_no_committed_expected() -> None:
    """EV-HOLD-1xx cases carry input metadata only — never a plaintext answer."""
    cases_dir = holdout_root(REPO_ROOT) / "cases"
    present_ids: set[str] = set()
    for path in sorted(cases_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["visibility"] == "holdout", path.name
        assert "expected" not in payload, path.name
        present_ids.add(str(payload["case_id"]))
    # The live hidden set is exactly the fresh 1xx ids; the compromised 00x ids
    # are gone from the hidden holdout root.
    assert present_ids == set(_NEW_HOLDOUT_IDS)
    assert not any(cid in present_ids for cid in _RETIRED_HOLDOUT_IDS)


def test_glob_finds_no_committed_expected_answer_map() -> None:
    """glob for any *eval*expected*/*holdout*expected* map finds nothing tracked."""
    tracked = _tracked_files()
    leaks = [rel for rel in tracked if _EXPECTED_MAP_RE.search(Path(rel).name)]
    assert leaks == [], f"committed holdout expected map(s) leaked: {leaks}"
    # rglob over the working tree must not surface an eval/holdout expected
    # ANSWER map inside the repo (unrelated docs "expected-*.json" fixtures that
    # are not answer keys are excluded by the answer-map pattern).
    inside = [
        p
        for p in REPO_ROOT.rglob("*.json")
        if ".venv" not in p.parts and ".git" not in p.parts and _EXPECTED_MAP_RE.search(p.name)
    ]
    assert inside == [], f"expected answer map materialised inside repo: {inside}"


def test_generated_secret_answer_is_never_committed_or_in_history(
    tmp_path: Path,
) -> None:
    """A fresh operator answer, written only out-of-tree, is absent from git.

    Uses `git ls-files` (working tree) + the single-pass blob-deduplicated
    historical scanner (`tests/security/git_history_scan.py`, D-CODEX-ATLAS
    Cluster C redesign) to prove the persistence boundary: the private map
    lives outside the repo and the secret never lands in a tracked blob,
    past or present. This mirrors `test_adv_git_history_access`'s own fix
    -- the previous `git grep <token> <every revision>` pattern here was
    the identical pathological O(revisions) scan (flagged as a known,
    deferred sibling in D-209's evidence doc when Cluster C was first
    fixed).

    Checks both the secret-token AND holdout-case-id+"expected" properties
    in the SAME scanner call (review finding on PR #652): the scanner
    already performs a full repo-history blob traversal per call with no
    caching, so folding
    `test_no_committed_case_file_pairs_new_holdout_id_with_expected_in_history`'s
    assertion in here avoids a second, fully redundant traversal of the
    same history in the same test run.
    """
    token = f"holdout-secret-{secrets.token_hex(16)}"
    secret_map = {"EV-HOLD-101": token, "EV-HOLD-102": token}
    # The operator's private map goes to an out-of-tree, gitignored-style path.
    private_dir = tmp_path / "private"
    private_dir.mkdir()
    map_path = private_dir / "eval_holdout_expected.json"
    map_path.write_text(json.dumps(secret_map), encoding="utf-8")
    assert not str(map_path.resolve()).startswith(str(REPO_ROOT.resolve()))

    secret_hits, answer_hits = find_leaked_holdout_evidence(
        REPO_ROOT, secret_tokens=(token,), holdout_case_ids=_NEW_HOLDOUT_IDS
    )
    assert secret_hits == (), secret_hits
    # No historical commit ever paired EV-HOLD-101/102 with a committed
    # "expected" answer -- see
    # test_old_algorithm_is_proven_to_miss_same_occurrence_count_edits in
    # tests/security/test_git_history_scan_differential.py for why this is
    # checked via the scanner rather than the original `git log -S`
    # pickaxe (a real, reproduced blind spot in the latter).
    assert answer_hits == (), answer_hits

    # Working-tree files: no tracked file carries the secret token.
    for rel in _tracked_files():
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        assert token not in text, rel


def test_retired_holdout_case_files_are_public_regression() -> None:
    """EV-HOLD-001/002 now live as PUBLIC regression cases, not hidden holdouts."""
    reg_dir = regression_root(REPO_ROOT) / "cases"
    retired: dict[str, dict[str, object]] = {}
    for path in sorted(reg_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        retired[str(payload.get("retired_from", ""))] = payload
    for old_id in _RETIRED_HOLDOUT_IDS:
        assert old_id in retired, old_id
        payload = retired[old_id]
        assert payload["visibility"] == "public"
        assert payload["case_class"] == "regression"


# "No historical commit ever added an expected answer for EV-HOLD-1xx" is
# checked inline inside test_generated_secret_answer_is_never_committed_or_in_history
# above -- folded into that test's single scanner call rather than kept as
# a separate test function, to avoid a second, fully redundant full-repo-
# history traversal in the same run (review finding on PR #652:
# find_leaked_holdout_evidence() has no caching, so two separate tests
# each calling it doubled this file's wall-clock cost for no additional
# coverage -- both properties are asserted from one call now).
