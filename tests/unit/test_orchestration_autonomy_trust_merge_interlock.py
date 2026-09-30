"""M2: TRUST_MAIN_INTERLOCK -- require_trust_current_for_merge().

Real incidents this formalizes: PR #653 (merged while the persisted trust
anchor was already stale, nothing machine-enforced to stop it) and PR #669
(a genuine 3-way merge the ordinary advancement path of the time could not
represent). Adversarial matrix for the fail-closed precondition, and for
its one narrow, explicit, non-generic exception (``TrustRepairCarrier``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_atlas.orchestration.autonomy.models import (
    CANONICAL_REPOSITORY_IDENTITY,
    AdvancementReason,
    TrustedAnchorRecord,
)
from project_atlas.orchestration.autonomy.trust import (
    FixtureGitObserver,
    MergeGuardError,
    TrustError,
    TrustRepairCarrier,
    initialize_store,
    require_trust_current_for_merge,
    seal_anchor,
)

CURRENT_MAIN = "a" * 40
CURRENT_TREE = "b" * 40
NEXT_MAIN = "c" * 40
NEXT_TREE = "d" * 40
UNRELATED_ROOT = "e" * 40


def _anchor(
    *,
    main: str = CURRENT_MAIN,
    tree: str = CURRENT_TREE,
    identity: str = CANONICAL_REPOSITORY_IDENTITY,
) -> TrustedAnchorRecord:
    return seal_anchor(
        TrustedAnchorRecord(
            repository_identity=identity,
            trusted_main=main,
            trusted_tree=tree,
            predecessor_main=UNRELATED_ROOT,
            predecessor_tree=tree,
            advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
            source_package="AS-ORCH-AUTONOMY-001-PIN-RETARGET",
            source_directive="D-AUTONOMY-PIN-RETARGET-003",
            source_pr=1,
            merge_commit=main,
            merge_parent_1=UNRELATED_ROOT,
            merge_parent_2=main,
            merge_tree=tree,
            certified_head=main,
            certified_tree=tree,
            certification_status="CERTIFIED",
            independent_verification_status="PASS",
            post_merge_seal="PASS",
            post_merge_ci="PASS",
            evidence_reference="tests/fixtures/interlock.json",
            evidence_digest="ab" * 32,
            sequence=1,
            record_digest="00" * 32,
        )
    )


def _topology(
    *, observed_main: str = CURRENT_MAIN, observed_tree: str = CURRENT_TREE
) -> FixtureGitObserver:
    return FixtureGitObserver(
        observed_main=observed_main,
        observed_tree=observed_tree,
        objects={CURRENT_MAIN: (CURRENT_TREE, ()), NEXT_MAIN: (NEXT_TREE, (CURRENT_MAIN,))},
    )


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------


def test_trust_current_allows_merge(tmp_path: Path) -> None:
    current = _anchor()
    initialize_store(tmp_path, current)
    result = require_trust_current_for_merge(store=tmp_path, topology=_topology())
    assert result.trusted_main == CURRENT_MAIN


def test_returns_the_loaded_anchor_unmutated(tmp_path: Path) -> None:
    """Never a trust-state mutation: the store's current.json is
    byte-identical before and after a successful call."""
    current = _anchor()
    initialize_store(tmp_path, current)
    before = (tmp_path / "current.json").read_text(encoding="utf-8")
    require_trust_current_for_merge(store=tmp_path, topology=_topology())
    after = (tmp_path / "current.json").read_text(encoding="utf-8")
    assert before == after
    assert not (tmp_path / "history").exists()  # compare_and_advance never invoked


# ---------------------------------------------------------------------------
# Adversarial denial matrix
# ---------------------------------------------------------------------------


def test_stale_trust_denies_merge_without_carrier(tmp_path: Path) -> None:
    """Real incident shape, PR #653: main has moved (NEXT_MAIN is now
    live) but trust is still pinned at the old CURRENT_MAIN. No carrier
    supplied -- must deny."""
    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE)
    with pytest.raises(MergeGuardError) as exc:
        require_trust_current_for_merge(store=tmp_path, topology=topology)
    assert exc.value.code == "TRUST_NOT_CURRENT_FOR_MERGE"
    assert isinstance(exc.value, TrustError)  # subclass, not a parallel hierarchy


def test_stale_trust_with_explicit_carrier_is_allowed(tmp_path: Path) -> None:
    """Real incident shape, PR #669/#671: trust is stale specifically
    because the repair carrier itself is what's being integrated. An
    explicit, narrow justification lets this ONE precondition check pass."""
    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE)
    carrier = TrustRepairCarrier(source_pr=671, reason="ordinary-advancement 3-way-merge repair")
    result = require_trust_current_for_merge(
        store=tmp_path, topology=topology, trust_repair_carrier=carrier
    )
    # Still the STALE anchor -- the carrier doesn't mutate it, only lets this check pass.
    assert result.trusted_main == CURRENT_MAIN


def test_carrier_source_pr_must_be_positive() -> None:
    with pytest.raises(TrustError) as exc:
        TrustRepairCarrier(source_pr=0, reason="valid reason")
    assert exc.value.code == "INTERLOCK_MISUSE"
    with pytest.raises(TrustError):
        TrustRepairCarrier(source_pr=-1, reason="valid reason")


def test_carrier_reason_must_be_non_empty() -> None:
    with pytest.raises(TrustError) as exc:
        TrustRepairCarrier(source_pr=671, reason="")
    assert exc.value.code == "INTERLOCK_MISUSE"
    with pytest.raises(TrustError):
        TrustRepairCarrier(source_pr=671, reason="   ")


def test_missing_store_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(TrustError):
        require_trust_current_for_merge(store=tmp_path / "does-not-exist", topology=_topology())


def test_corrupt_store_fails_closed(tmp_path: Path) -> None:
    current = _anchor()
    initialize_store(tmp_path, current)
    (tmp_path / "current.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(store=tmp_path, topology=_topology())
    assert exc.value.code == "TRUST_UNVERIFIABLE"


def test_repository_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    current = _anchor()
    initialize_store(tmp_path, current)
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path,
            topology=_topology(),
            expected_repository_identity="github.com/someone-else/other-repo",
        )
    assert exc.value.code == "REPO_IDENTITY_MISMATCH"


def test_carrier_present_does_not_bypass_identity_mismatch(tmp_path: Path) -> None:
    """A trust_repair_carrier is a narrow exception to the STALENESS
    check only -- it must never let a wrong-repository store slip
    through. Identity verification happens before staleness is even
    evaluated."""
    current = _anchor()
    initialize_store(tmp_path, current)
    carrier = TrustRepairCarrier(source_pr=671, reason="attempted bypass")
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path,
            topology=_topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE),
            expected_repository_identity="github.com/someone-else/other-repo",
            trust_repair_carrier=carrier,
        )
    assert exc.value.code == "REPO_IDENTITY_MISMATCH"


def test_carrier_present_does_not_bypass_corrupt_store(tmp_path: Path) -> None:
    """Same principle: the carrier exception applies only to genuine
    staleness, never to an unverifiable store."""
    current = _anchor()
    initialize_store(tmp_path, current)
    (tmp_path / "current.json").write_text("{not-json", encoding="utf-8")
    carrier = TrustRepairCarrier(source_pr=671, reason="attempted bypass")
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path, topology=_topology(), trust_repair_carrier=carrier
        )
    assert exc.value.code == "TRUST_UNVERIFIABLE"


def test_dict_masquerading_as_carrier_denied(tmp_path: Path) -> None:
    """IV finding, PR #672: the only pre-fix gate was `is None`, so a
    plain dict with plausible-looking keys silently satisfied it --
    mypy's type hint alone is not load-bearing against a loosely-typed
    caller (deserialized JSON, a CLI arg, an `Any`-typed kwargs
    passthrough). A real, exact-type TrustRepairCarrier is now required."""
    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE)
    fake_carrier = {"source_pr": 671, "reason": "looks legitimate"}
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path,
            topology=topology,
            trust_repair_carrier=fake_carrier,  # type: ignore[arg-type]
        )
    assert exc.value.code == "INTERLOCK_MISUSE"


def test_bare_true_masquerading_as_carrier_denied(tmp_path: Path) -> None:
    """The exact bypass the class's own docstring claims is impossible --
    now genuinely impossible, not just discouraged by a type hint."""
    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE)
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path,
            topology=topology,
            trust_repair_carrier=True,  # type: ignore[arg-type]
        )
    assert exc.value.code == "INTERLOCK_MISUSE"


def test_bare_string_masquerading_as_carrier_denied(tmp_path: Path) -> None:
    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE)
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path,
            topology=topology,
            trust_repair_carrier="PR #671 repair",  # type: ignore[arg-type]
        )
    assert exc.value.code == "INTERLOCK_MISUSE"


def test_subclass_overriding_validation_denied(tmp_path: Path) -> None:
    """A subclass that overrides `__post_init__` to skip the source_pr/
    reason validation would still pass a bare `isinstance` check --
    exact-type comparison (`type(x) is not TrustRepairCarrier`) closes
    this, since `TrustRepairCarrier` is frozen and not meant to be
    subclassed for this purpose."""

    class ForgedCarrier(TrustRepairCarrier):
        def __post_init__(self) -> None:  # skips all validation
            pass

    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=NEXT_MAIN, observed_tree=NEXT_TREE)
    forged = ForgedCarrier(source_pr=-999, reason="")
    with pytest.raises(TrustError) as exc:
        require_trust_current_for_merge(
            store=tmp_path, topology=topology, trust_repair_carrier=forged
        )
    assert exc.value.code == "INTERLOCK_MISUSE"


def test_target_tree_mismatch_alone_still_denied(tmp_path: Path) -> None:
    """Staleness is (main, tree) together -- a tree-only mismatch (same
    commit SHA claimed, different tree observed) must still deny, exactly
    like a main mismatch."""
    current = _anchor()
    initialize_store(tmp_path, current)
    topology = _topology(observed_main=CURRENT_MAIN, observed_tree=NEXT_TREE)
    with pytest.raises(MergeGuardError):
        require_trust_current_for_merge(store=tmp_path, topology=topology)


# ---------------------------------------------------------------------------
# Real operator surface: `trust-check-before-merge` (reviewer finding,
# Codex, PR #672 -- "the new precondition is never invoked by production
# code"). Exercised against a REAL disposable git repo, not FixtureGitObserver,
# so this proves the actual CLI wiring end-to-end.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _make_real_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", "https://github.com/b0lk13/project-atlas.git")
    (repo / "f.txt").write_text("root\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "root")
    return repo


def test_cli_reports_merge_permitted_when_trust_current(tmp_path: Path) -> None:
    from project_atlas.orchestration.autonomy.cli import EXIT_OK, run_trust_check_before_merge

    repo = _make_real_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    store = tmp_path / "store"
    initialize_store(store, _anchor(main=head, tree=tree))
    report, exit_code = run_trust_check_before_merge(root=repo, trust_store=store)
    assert exit_code == EXIT_OK
    assert report["merge_permitted"] is True
    assert report["trusted_main"] == head
    assert report["repair_carrier_used"] is False
    assert report["merge_authorized"] is False


def test_cli_denies_when_trust_stale(tmp_path: Path) -> None:
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_trust_check_before_merge

    repo = _make_real_repo(tmp_path)
    old_head = _git(repo, "rev-parse", "HEAD")
    old_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    (repo / "f.txt").write_text("next\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "next")
    new_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", new_head)
    store = tmp_path / "store"
    initialize_store(store, _anchor(main=old_head, tree=old_tree))
    report, exit_code = run_trust_check_before_merge(root=repo, trust_store=store)
    assert exit_code == EXIT_ERROR
    assert report["merge_permitted"] is False
    assert report["blocker"] == "TRUST_NOT_CURRENT_FOR_MERGE"


def test_cli_allows_with_explicit_repair_carrier(tmp_path: Path) -> None:
    from project_atlas.orchestration.autonomy.cli import EXIT_OK, run_trust_check_before_merge

    repo = _make_real_repo(tmp_path)
    old_head = _git(repo, "rev-parse", "HEAD")
    old_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    (repo / "f.txt").write_text("next\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "next")
    new_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", new_head)
    store = tmp_path / "store"
    initialize_store(store, _anchor(main=old_head, tree=old_tree))
    report, exit_code = run_trust_check_before_merge(
        root=repo, trust_store=store, repair_source_pr=671, repair_reason="test repair"
    )
    assert exit_code == EXIT_OK
    assert report["merge_permitted"] is True
    assert report["repair_carrier_used"] is True
    # Still the stale anchor -- the CLI never mutates trust state.
    assert report["trusted_main"] == old_head


def test_cli_rejects_repair_pr_without_reason(tmp_path: Path) -> None:
    """--repair-pr and --repair-reason are required together -- caught
    before any git/store I/O is even attempted."""
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_trust_check_before_merge

    report, exit_code = run_trust_check_before_merge(
        root=tmp_path, trust_store=tmp_path / "store", repair_source_pr=671, repair_reason=None
    )
    assert exit_code == EXIT_ERROR
    assert report["blocker"] == "INTERLOCK_MISUSE"


def test_cli_rejects_repair_reason_without_pr(tmp_path: Path) -> None:
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_trust_check_before_merge

    report, exit_code = run_trust_check_before_merge(
        root=tmp_path,
        trust_store=tmp_path / "store",
        repair_source_pr=None,
        repair_reason="only reason, no pr",
    )
    assert exit_code == EXIT_ERROR
    assert report["blocker"] == "INTERLOCK_MISUSE"


def test_cli_parser_wires_trust_check_before_merge(tmp_path: Path) -> None:
    """Argv-level wiring: `main()` dispatches to `run_trust_check_before_merge`
    with the right arguments, exactly like `trust-checkpoint` already does."""
    from project_atlas.orchestration.autonomy.cli import main

    repo = _make_real_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    store = tmp_path / "store"
    initialize_store(store, _anchor(main=head, tree=tree))
    exit_code = main(
        [
            "trust-check-before-merge",
            "--root",
            str(repo),
            "--trust-store",
            str(store),
        ]
    )
    assert exit_code == 0


def test_cli_corrupt_store_fails_closed(tmp_path: Path) -> None:
    """Acceptance condition F: a corrupt store must fail closed through
    the real CLI path, same as through the library function directly."""
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_trust_check_before_merge

    repo = _make_real_repo(tmp_path)
    store = tmp_path / "store"
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    initialize_store(store, _anchor(main=head, tree=tree))
    (store / "current.json").write_text("{not-json", encoding="utf-8")
    report, exit_code = run_trust_check_before_merge(root=repo, trust_store=store)
    assert exit_code == EXIT_ERROR
    assert report["merge_permitted"] is False
    assert report["blocker"] == "TRUST_UNVERIFIABLE"


def test_cli_identity_mismatch_fails_closed_even_with_carrier(tmp_path: Path) -> None:
    """Acceptance condition G: the CLI observes the REAL repository
    identity via LiveGitObserver (git remote get-url origin) -- a store
    from a different repository must fail closed through the real
    command path, even with an explicit repair carrier."""
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_trust_check_before_merge

    repo = _make_real_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    store = tmp_path / "store"
    initialize_store(
        store, _anchor(main=head, tree=tree, identity="github.com/someone-else/other-repo")
    )
    report, exit_code = run_trust_check_before_merge(
        root=repo, trust_store=store, repair_source_pr=671, repair_reason="attempted bypass"
    )
    assert exit_code == EXIT_ERROR
    assert report["merge_permitted"] is False
    assert report["blocker"] == "REPO_IDENTITY_MISMATCH"


def test_cli_tree_only_mismatch_denied(tmp_path: Path) -> None:
    """Acceptance condition H: same commit SHA claimed as trusted, but a
    different live tree -- must be denied through the real CLI path."""
    from project_atlas.orchestration.autonomy.cli import EXIT_ERROR, run_trust_check_before_merge

    repo = _make_real_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    real_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    store = tmp_path / "store"
    # Trust claims the same commit but a DIFFERENT (wrong) tree.
    initialize_store(store, _anchor(main=head, tree=NEXT_TREE))
    report, exit_code = run_trust_check_before_merge(root=repo, trust_store=store)
    assert exit_code == EXIT_ERROR
    assert report["merge_permitted"] is False
    assert report["blocker"] == "TRUST_NOT_CURRENT_FOR_MERGE"
    assert real_tree != NEXT_TREE  # sanity: the fixture genuinely differs
