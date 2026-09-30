"""AS-ORCH-AUTONOMY-001 STALE_SUCCESSOR_BRANCH_CLASSIFICATION.

Real IV finding during the first supervised-autonomy retry (D-CLAUDE-
ATLAS-RESUME-AUTONOMY-PREREQUISITES-AND-RETRY): a historical successor
branch (``feat/as-orch-001e-autonomous-loop``, merged via PR #401,
commit ``806218ae29792db63416a654e6a8390268764a1``) simply left on the
remote after merging permanently hard-blocked discovery via
``SUCCESSOR_ALREADY_STARTED`` -- ``collect_live_inventory()`` detected
"active successor" activity purely from a matching BRANCH NAME
existing, never checking whether that branch's content was already
integrated into main. ``BRANCH_REF_EXISTS != ACTIVE_SUCCESSOR``.

These tests use fully self-built, hermetic temp git repos (real ``git``
subprocess calls, real commits, ``git update-ref refs/remotes/origin/
main`` to simulate a fetched remote-tracking ref without any network
access -- the same technique this session's trust-checkpoint work
already established) rather than depending on this repository's own
live, historical branch state, which is not something a portable CI
checkout can be relied on to reproduce identically (shallow/limited
remote fetch scope). The exact real-repository regression (the
``feat/as-orch-001e-autonomous-loop`` branch specifically) was
independently, manually re-verified against this repository's actual
live state as part of closing this finding -- see WORKLOG.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.discovery import (
    DiscoveryError,
    _is_merged_into,
    _is_shallow_repository,
    collect_live_inventory,
    discover,
)
from project_atlas.orchestration.autonomy.models import AdvancementReason, TrustedAnchorRecord
from project_atlas.orchestration.autonomy.trust import seal_anchor


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _make_repo(tmp_path: Path, *, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    # --initial-branch=main (git >= 2.28, universal on any supported
    # runner): the hosted CI failure this fixes exactly demonstrates why
    # this can't be left to ambient config -- `git init`'s default
    # branch name depends on the runner's `init.defaultBranch` (or
    # git's own compiled-in fallback, "master" on older/some
    # distributions), never something these tests should depend on.
    # Every test in this file assumes a branch literally named "main".
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("root\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "root")
    return repo


def _commit(repo: Path, message: str) -> str:
    (repo / "f.txt").write_text(f"{message}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _set_origin_main(repo: Path, sha: str) -> None:
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)


def _set_remote_branch(repo: Path, refname: str, sha: str) -> None:
    _git(repo, "update-ref", refname, sha)


def _set_local_branch(repo: Path, name: str, sha: str) -> None:
    _git(repo, "branch", name, sha)


def _anchor_at(main: str, tree: str) -> TrustedAnchorRecord:
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity="github.com/b0lk13/project-atlas",
            trusted_main=main,
            trusted_tree=tree,
            predecessor_main="0" * 40,
            predecessor_tree="0" * 40,
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORCH-AUTONOMY-001-PIN-RETARGET",
            source_directive="D-AUTONOMY-PIN-RETARGET-003",
            source_pr=1,
            merge_commit=main,
            merge_parent_1="0" * 40,
            merge_parent_2=main,
            merge_tree=tree,
            certified_head=main,
            certified_tree=tree,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/fixtures/discovery-successor.json",
            evidence_digest="ab" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


# ---------------------------------------------------------------------------
# A/C -- a matching ref whose tip IS an ancestor of origin/main (including
# the trivial case of being main itself) is NOT active.
# ---------------------------------------------------------------------------


def test_a_merged_remote_successor_branch_is_not_active(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    main_tip = _commit(repo, "second")
    _set_origin_main(repo, main_tip)
    # The historical branch's tip (root) is an ancestor of current main.
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-old", root)

    inventory = collect_live_inventory(repo)
    assert inventory.active_successor_packages == ()
    assert inventory.as_orch_001e_started == "NO"

    report = discover(inventory, trusted=_anchor_at(main_tip, inventory.current_tree))
    assert report.blocker != "SUCCESSOR_ALREADY_STARTED"


def test_c_successor_branch_tip_equals_current_main_is_not_active(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-same", main_tip)

    inventory = collect_live_inventory(repo)
    assert inventory.active_successor_packages == ()


# ---------------------------------------------------------------------------
# B -- a matching LOCAL branch that is already merged is NOT active.
# ---------------------------------------------------------------------------


def test_b_merged_local_successor_branch_is_not_active(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    main_tip = _commit(repo, "second")
    _set_origin_main(repo, main_tip)
    _set_local_branch(repo, "as-orch-001d-r2-legacy", root)

    inventory = collect_live_inventory(repo)
    assert inventory.active_successor_packages == ()
    assert inventory.r2_created == "NO"


# ---------------------------------------------------------------------------
# D -- a matching ref with a genuinely UNMERGED tip IS active, and
# discover() correctly reports SUCCESSOR_ALREADY_STARTED for it.
# ---------------------------------------------------------------------------


def test_d_unmerged_successor_branch_is_active_and_blocks(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    _git(repo, "checkout", "-q", "-b", "feat/as-orch-001e-in-flight")
    unmerged_tip = _commit(repo, "still in flight")
    _git(repo, "checkout", "-q", "-B", "main-detached", main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-in-flight", unmerged_tip)

    inventory = collect_live_inventory(repo)
    # The local branch (still present from the checkout above) AND the
    # remote-tracking ref both match and are both genuinely unmerged --
    # correctly both counted (local-branch detection works too, not
    # just remote refs).
    assert set(inventory.active_successor_packages) == {
        "refs/heads/feat/as-orch-001e-in-flight",
        "refs/remotes/origin/feat/as-orch-001e-in-flight",
    }
    assert inventory.as_orch_001e_started == "YES"

    report = discover(inventory, trusted=_anchor_at(main_tip, inventory.current_tree))
    assert report.case == "A-B"
    assert report.blocker == "SUCCESSOR_ALREADY_STARTED"


# ---------------------------------------------------------------------------
# E -- one stale merged ref + one genuinely active ref -> still blocks
# (the merged one doesn't mask the real one, and vice versa).
# ---------------------------------------------------------------------------


def test_e_mixed_merged_and_unmerged_successor_refs_still_blocks(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    main_tip = _commit(repo, "second")
    _set_origin_main(repo, main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-old", root)
    _git(repo, "checkout", "-q", "-b", "feat/as-orch-001e-new")
    unmerged_tip = _commit(repo, "genuinely new work")
    _git(repo, "checkout", "-q", "-B", "main-detached", main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-new", unmerged_tip)

    inventory = collect_live_inventory(repo)
    assert "refs/remotes/origin/feat/as-orch-001e-old" not in inventory.active_successor_packages
    assert "refs/remotes/origin/feat/as-orch-001e-new" in inventory.active_successor_packages
    assert inventory.as_orch_001e_started == "YES"


# ---------------------------------------------------------------------------
# F -- a non-matching branch name, even with an unmerged tip, is irrelevant.
# ---------------------------------------------------------------------------


def test_f_nonmatching_branch_name_is_irrelevant(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    _git(repo, "checkout", "-q", "-b", "feat/totally-unrelated-work")
    unmerged_tip = _commit(repo, "unrelated")
    _git(repo, "checkout", "-q", "-B", "main-detached", main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/totally-unrelated-work", unmerged_tip)

    inventory = collect_live_inventory(repo)
    assert inventory.active_successor_packages == ()


# ---------------------------------------------------------------------------
# G -- multiple refs (simulating multiple remotes) pointing at the same
# already-integrated tip -> no false blocker from either.
# ---------------------------------------------------------------------------


def test_g_multiple_remote_refs_at_same_merged_tip_no_false_blocker(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    main_tip = _commit(repo, "second")
    _set_origin_main(repo, main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-old", root)
    _set_remote_branch(repo, "refs/remotes/upstream/feat/as-orch-001e-old", root)

    inventory = collect_live_inventory(repo)
    assert inventory.active_successor_packages == ()


# ---------------------------------------------------------------------------
# H -- topology query failure fails closed (DiscoveryError), never silently
# treated as "not merged" or "merged".
# ---------------------------------------------------------------------------


def test_h_topology_query_failure_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(DiscoveryError):
        # A syntactically-invalid ref/object -- forces a real git error,
        # not one of the two genuine ancestry outcomes (0 or 1).
        _is_merged_into(repo, "not-a-real-object", main_tip)


# ---------------------------------------------------------------------------
# I -- symbolic remote HEAD refs are excluded (never mistaken for a real
# branch matching the successor patterns).
# ---------------------------------------------------------------------------


def test_symbolic_remote_head_ref_is_excluded(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    _git(repo, "checkout", "-q", "-b", "feat/as-orch-001e-branch")
    unmerged_tip = _commit(repo, "in flight")
    _git(repo, "checkout", "-q", "-B", "main-detached", main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/feat/as-orch-001e-branch", unmerged_tip)
    # A symbolic HEAD ref for the same remote must never itself be
    # misread as a second matching branch (it wouldn't match the name
    # pattern anyway here, but confirms for-each-ref's HEAD exclusion
    # doesn't error or duplicate-count).
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    inventory = collect_live_inventory(repo)
    assert set(inventory.active_successor_packages) == {
        "refs/heads/feat/as-orch-001e-branch",
        "refs/remotes/origin/feat/as-orch-001e-branch",
    }
    # The symbolic HEAD ref itself never appears as a third entry.
    assert "refs/remotes/origin/HEAD" not in inventory.active_successor_packages


# ---------------------------------------------------------------------------
# K -- pre-existing successor-blocking behavior (genuinely unmerged, no
# name-pattern match confusion) still works after this change.
# ---------------------------------------------------------------------------


def test_k_existing_target_moved_precedence_unchanged(tmp_path: Path) -> None:
    """TARGET_MOVED still takes precedence over successor-activity
    classification -- this codepath is untouched by this fix."""
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    inventory = collect_live_inventory(repo)
    # A trusted anchor pointed at a DIFFERENT commit than observed main.
    other = "9" * 40
    report = discover(inventory, trusted=_anchor_at(other, "8" * 40))
    assert report.case == "A-B"
    assert report.blocker == "TARGET_MOVED"


# ---------------------------------------------------------------------------
# Coverage gap closed (fresh IV round on this PR, not a functional defect --
# both behaviors were independently verified by the IV agent but not yet
# exercised by this file's own tests).
# ---------------------------------------------------------------------------


def test_squash_merged_branch_stays_conservatively_active(tmp_path: Path) -> None:
    """The docstring's claim: a squash-merged branch (content landed on
    main via a NEW commit, the original branch's own tip commit never
    itself becomes an ancestor of main) is NOT recognized as merged --
    it stays conservatively active. This is the correct, safe failure
    direction (a real successor branch's tip could similarly differ
    from what actually reached main), not a functional gap in THIS fix
    -- confirmed the real PR #401 this fix targets was a genuine 2-
    parent merge commit, not a squash, so this residual case does not
    affect the real-world regression this PR closes; it's disclosed in
    the module docstring for future maintainers instead."""
    repo = _make_repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "as-orch-001d-r2-squash-work")
    branch_tip = _commit(repo, "real work, never lands on main by this exact SHA")
    _git(repo, "checkout", "-q", "-B", "main-detached", root)
    # Squash-merge: main gets a NEW commit with the branch's content,
    # never the branch's own tip commit object.
    _git(repo, "merge", "--squash", "as-orch-001d-r2-squash-work")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "squash merge")
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    _set_remote_branch(repo, "refs/remotes/origin/as-orch-001d-r2-squash-work", branch_tip)

    assert _is_merged_into(repo, branch_tip, main_tip) is False
    inventory = collect_live_inventory(repo)
    assert "refs/remotes/origin/as-orch-001d-r2-squash-work" in inventory.active_successor_packages
    assert inventory.r2_created == "YES"


def test_dangling_ref_object_fails_closed_through_full_pipeline(tmp_path: Path) -> None:
    """The fail-closed path exercised through the REAL collect_live_
    inventory() pipeline (not just _is_merged_into() in isolation): a
    ref enumerated by `git for-each-ref` whose object is not actually a
    valid/reachable commit (a corrupt or dangling ref) must raise
    DiscoveryError, never be silently treated as merged or unmerged."""
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    # A loose ref file written directly, bypassing git update-ref's own
    # object-existence validation -- for-each-ref still enumerates it
    # (the ref file exists), but no such commit object exists in the repo.
    dangling_sha = "d" * 40
    ref_path = repo / ".git" / "refs" / "heads" / "as-orch-001e-dangling"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(dangling_sha + "\n", encoding="utf-8")

    with pytest.raises(DiscoveryError):
        collect_live_inventory(repo)


# ---------------------------------------------------------------------------
# Second review round on this PR (chatgpt-codex-connector P2 + P1): shallow-
# clone ancestry blindness, and a dirty CURRENT successor branch being
# silently ignored because its last committed tip is already an ancestor.
# ---------------------------------------------------------------------------


def test_shallow_clone_with_matching_ref_fails_closed(tmp_path: Path) -> None:
    """A shallow clone can make `git merge-base --is-ancestor` report `1`
    (not an ancestor) for a commit that genuinely IS merged in the full
    history -- git treats a shallow commit's parents as nonexistent, so
    the shallow boundary can hide the real merge edge. This would
    silently reintroduce this very fix's own bug in any shallow
    checkout. Must fail closed instead of guessing."""
    source = _make_repo(tmp_path, name="source")
    root = _git(source, "rev-parse", "HEAD")
    main_tip = _commit(source, "second")
    _git(source, "checkout", "-q", "-b", "feat/as-orch-001e-old", root)
    _git(source, "checkout", "-q", "main")

    clone = tmp_path / "shallow-clone"
    _git(
        tmp_path,
        "clone",
        "-q",
        "--depth",
        "1",
        "--no-single-branch",
        "--no-local",
        str(source),
        str(clone),
    )
    assert _is_shallow_repository(clone) is True
    _set_origin_main(clone, main_tip)
    _set_remote_branch(clone, "refs/remotes/origin/feat/as-orch-001e-old", root)

    with pytest.raises(DiscoveryError, match="shallow"):
        collect_live_inventory(clone)


def test_shallow_clone_with_no_matching_refs_is_fine(tmp_path: Path) -> None:
    """A shallow clone with no successor-pattern refs at all has nothing
    ambiguous to fail closed on -- must not spuriously error."""
    source = _make_repo(tmp_path, name="source2")
    main_tip = _git(source, "rev-parse", "HEAD")
    clone = tmp_path / "shallow-clone2"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--no-local", str(source), str(clone))
    assert _is_shallow_repository(clone) is True
    _set_origin_main(clone, main_tip)

    inventory = collect_live_inventory(clone)
    assert inventory.active_successor_packages == ()


def test_non_shallow_repo_is_not_shallow(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert _is_shallow_repository(repo) is False


def test_shallow_repo_on_own_dirty_successor_branch_does_not_false_block(
    tmp_path: Path,
) -> None:
    """Second IV round's real finding: the shallow gate must only fire
    for refs that actually NEED an ancestry query. The single most
    realistic real-world case this whole fix targets -- an agent
    running discovery from its OWN shallow CI-style checkout, on its
    OWN dirty in-progress successor branch, with no OTHER matching ref
    anywhere in the repo -- was being spuriously denied by the shallow
    check even though the dirty-current-branch rule resolves that ref
    unconditionally and never calls _is_merged_into() for it at all."""
    source = _make_repo(tmp_path, name="source3")
    main_tip = _git(source, "rev-parse", "HEAD")

    clone = tmp_path / "shallow-clone3"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--no-local", str(source), str(clone))
    assert _is_shallow_repository(clone) is True
    _set_origin_main(clone, main_tip)
    # The clone's own checked-out branch (main, by default after clone)
    # is not itself a match; create and check out a NEW matching branch
    # with real uncommitted work, the only matching ref in the repo.
    _git(clone, "checkout", "-q", "-b", "feat/as-orch-001e-my-in-progress-work")
    (clone / "f.txt").write_text("real uncommitted successor work\n", encoding="utf-8")

    # Must NOT raise -- no ancestry query is ever needed for this ref.
    inventory = collect_live_inventory(clone)
    assert "refs/heads/feat/as-orch-001e-my-in-progress-work" in inventory.active_successor_packages
    assert inventory.as_orch_001e_started == "YES"


def test_shallow_repo_still_fails_closed_when_a_different_ref_needs_ancestry(
    tmp_path: Path,
) -> None:
    """The narrowing in the prior test must not overreach: if a
    DIFFERENT matching ref (not the current dirty branch) exists and
    genuinely needs an ancestry query, the shallow gate must still
    fire."""
    source = _make_repo(tmp_path, name="source4")
    root = _git(source, "rev-parse", "HEAD")
    main_tip = _commit(source, "second")
    _git(source, "checkout", "-q", "-b", "feat/as-orch-001e-old", root)
    _git(source, "checkout", "-q", "main")

    clone = tmp_path / "shallow-clone4"
    _git(
        tmp_path,
        "clone",
        "-q",
        "--depth",
        "1",
        "--no-single-branch",
        "--no-local",
        str(source),
        str(clone),
    )
    assert _is_shallow_repository(clone) is True
    _set_origin_main(clone, main_tip)
    _set_remote_branch(clone, "refs/remotes/origin/feat/as-orch-001e-old", root)
    # Current checkout is a DIFFERENT, non-matching, clean branch.
    _git(clone, "checkout", "-q", "-b", "chore/unrelated")

    with pytest.raises(DiscoveryError, match="shallow"):
        collect_live_inventory(clone)


def test_dirty_current_successor_branch_stays_active_even_if_tip_is_merged(
    tmp_path: Path,
) -> None:
    """A matching branch that IS the current checkout, with real
    uncommitted work, must stay active even though its last COMMITTED
    tip is already an ancestor of main (e.g. just branched from main,
    nothing committed yet) -- discover() never separately consults
    worktree_status, so ancestry alone would otherwise silently drop
    genuinely in-progress successor work."""
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    # Branch from main with NO new commit -- its tip trivially equals
    # (is an ancestor of) main.
    _git(repo, "checkout", "-q", "-b", "feat/as-orch-001e-just-started")
    # Real, uncommitted work sitting on disk.
    (repo / "f.txt").write_text("uncommitted in-progress work\n", encoding="utf-8")

    inventory = collect_live_inventory(repo)
    assert inventory.worktree_status == "DIRTY_UNTRACKED_OR_MODIFIED"
    assert "refs/heads/feat/as-orch-001e-just-started" in inventory.active_successor_packages
    assert inventory.as_orch_001e_started == "YES"


def test_clean_current_successor_branch_with_merged_tip_is_not_active(tmp_path: Path) -> None:
    """The counterpart to the dirty case above: a matching branch that
    IS the current checkout but has NO uncommitted work, and whose tip
    is already merged, correctly stays inactive -- confirms the dirty-
    branch exception doesn't overreach into the clean case."""
    repo = _make_repo(tmp_path)
    main_tip = _git(repo, "rev-parse", "HEAD")
    _set_origin_main(repo, main_tip)
    _git(repo, "checkout", "-q", "-b", "feat/as-orch-001e-just-started-clean")

    inventory = collect_live_inventory(repo)
    assert inventory.worktree_status == "CLEAN"
    assert inventory.active_successor_packages == ()
