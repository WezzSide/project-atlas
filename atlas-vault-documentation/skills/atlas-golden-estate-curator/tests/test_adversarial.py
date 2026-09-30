"""Adversarial fail-closed tests for the golden estate curator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from curator import (  # noqa: E402
    CuratorError,
    _redact_remote_url,
    curate,
    main,
    reject_mutation,
)
from estate import _git, _init_repo, fingerprint  # noqa: E402


@pytest.mark.parametrize(
    "action",
    [
        "DELETE",
        "MOVE",
        "RENAME",
        "GIT_CLEAN",
        "GIT_RESET",
        "AUTO_COMMIT",
        "AUTO_PUSH",
        "SOURCE_MODIFY",
        "HISTORY_REWRITE",
        "AUTO_MERGE",
    ],
)
def test_mutation_actions_fail_closed(action: str) -> None:
    with pytest.raises(CuratorError) as exc:
        reject_mutation(action)
    assert exc.value.code == "MUTATION_FORBIDDEN"


@pytest.mark.parametrize(
    "phase",
    ["COPY", "GOLDENIZE", "BASELINE_FREEZE", "FREEZE_ESTATE", "INDEPENDENT_VERIFY"],
)
def test_owner_gated_phases_fail_closed(tmp_path: Path, fixture_estate: Path, phase: str) -> None:
    source = fixture_estate
    with pytest.raises(CuratorError) as exc:
        curate(source, phase=phase, owner_authorize_copy=True)
    assert exc.value.code == "OWNER_GATE_REQUIRED"
    assert not (source / "malicious-build" / "EXECUTED").exists()


def test_cli_delete_and_git_clean_fail(tmp_path: Path, fixture_estate: Path) -> None:
    source = fixture_estate
    before = fingerprint(source)
    assert main(["--source-root", str(source), "--action", "DELETE"]) == 1
    assert main(["--source-root", str(source), "--action", "GIT_CLEAN"]) == 1
    assert main(["--source-root", str(source), "--phase", "COPY"]) == 1
    assert fingerprint(source) == before
    assert (source / "dirty-worktree" / "scratch.txt").is_file()


def test_unsupported_mode_fails_closed(tmp_path: Path, fixture_estate: Path) -> None:
    source = fixture_estate
    before = fingerprint(source)
    with pytest.raises(CuratorError) as exc:
        curate(source, mode="WRITE")
    assert exc.value.code == "UNSUPPORTED_MODE"
    assert main(["--source-root", str(source), "--mode", "GOLDENIZE"]) == 1
    assert fingerprint(source) == before
    assert not (source / "malicious-build" / "EXECUTED").exists()


def test_path_traversal_and_unc_fail() -> None:
    with pytest.raises(CuratorError) as trav:
        curate("../etc")
    assert trav.value.code == "PATH_TRAVERSAL"
    with pytest.raises(CuratorError) as unc:
        curate(r"\\server\share")
    assert unc.value.code == "UNC_PATH_REJECTED"


def test_symlink_escape_and_secret_and_malice(tmp_path: Path, fixture_estate: Path) -> None:
    source = fixture_estate
    report = curate(source, output=tmp_path / "out.json")
    reasons = {item["reason"] for item in report["exclusions"]}
    assert "SYMLINK_OR_JUNCTION_ESCAPE" in reasons
    secret = next(item for item in report["inventory"] if item["name"] == "fake-secret")
    assert secret["secret_findings"]
    assert "NOT_A_REAL_SECRET_VALUE" not in json_blob(report)
    malice = next(item for item in report["inventory"] if item["name"] == "malicious-build")
    assert malice["malicious_build_script"] is True
    assert malice["executed_build"] is False
    assert not (source / "malicious-build" / "EXECUTED").exists()
    nested = [item for item in report["inventory"] if item["nested_repo"]]
    assert nested
    dirty = next(item for item in report["inventory"] if item["name"] == "dirty-worktree")
    assert dirty["dirty_worktree"] is True
    assert (source / "dirty-worktree" / "scratch.txt").read_text(encoding="utf-8") == "uncommitted\n"


def test_long_windows_path_is_classified(tmp_path: Path) -> None:
    source = tmp_path / "estate"
    source.mkdir()
    deep = source / ("W" * 250)
    # Creating an over-long directory may fail on some filesystems; classify either way.
    try:
        deep.mkdir()
        (deep / "README.md").write_text("# long\n", encoding="utf-8")
    except OSError:
        pytest.skip("filesystem rejected long path creation")
    report = curate(source, output=tmp_path / "long.json")
    assert any(item["reason"] == "LONG_PATH" for item in report["exclusions"]) or any(
        item["name"].startswith("W") for item in report["inventory"]
    )


def json_blob(report: dict) -> str:
    import json

    return json.dumps(report)


TOKEN = "ghp_HUNT2200_NOT_A_REAL_TOKEN_9f3c1e"


def test_remote_userinfo_is_redacted_from_inventory(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    project = estate / "leaky-remote"
    _init_repo(project, readme="# Remote\n")
    _git(
        project,
        "remote",
        "add",
        "origin",
        f"https://owner:{TOKEN}@github.com/example/hunt-estate.git",
    )
    output = tmp_path / "remote-leak.json"
    report = curate(estate, output=output)
    blob = json_blob(report) + output.read_text(encoding="utf-8")
    assert TOKEN not in blob
    item = next(row for row in report["inventory"] if row["name"] == "leaky-remote")
    assert item["git"] is True
    assert item["remote"] == "https://github.com/example/hunt-estate.git"


def test_extra_slash_remote_userinfo_is_redacted(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    project = estate / "slash-remote"
    _init_repo(project, readme="# Slash remote\n")
    _git(
        project,
        "remote",
        "add",
        "origin",
        f"https:///owner:{TOKEN}@github.com/example/slash-estate.git",
    )
    report = curate(estate, output=tmp_path / "slash-remote.json")
    blob = json_blob(report)
    assert TOKEN not in blob
    item = next(row for row in report["inventory"] if row["name"] == "slash-remote")
    assert "owner:" not in (item["remote"] or "")
    assert TOKEN not in (item["remote"] or "")


def test_gitdir_symlink_escape_does_not_read_foreign_remote(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    _init_repo(victim, readme="# Victim\n")
    (victim / "dirty.txt").write_text("outside\n", encoding="utf-8")
    _git(
        victim,
        "remote",
        "add",
        "origin",
        f"https://bot:{TOKEN}@github.com/example/victim.git",
    )
    estate = tmp_path / "estate"
    hollow = estate / "hollow"
    hollow.mkdir(parents=True)
    (hollow / "README.md").write_text("# Hollow\n", encoding="utf-8")
    (hollow / ".git").symlink_to(victim / ".git")
    output = tmp_path / "gitdir-escape.json"
    report = curate(estate, output=output)
    blob = json_blob(report) + output.read_text(encoding="utf-8")
    assert TOKEN not in blob
    assert any(item["reason"] == "GITDIR_ESCAPE" for item in report["exclusions"])
    item = next(row for row in report["inventory"] if row["name"] == "hollow")
    assert item["git"] is False
    assert item["remote"] is None
    assert item["dirty_worktree"] is False
    assert (victim / "dirty.txt").read_text(encoding="utf-8") == "outside\n"


def test_gitdir_file_pointer_escape_does_not_read_foreign_remote(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    _init_repo(victim, readme="# Victim\n")
    _git(
        victim,
        "remote",
        "add",
        "origin",
        f"https://bot:{TOKEN}@github.com/example/victim.git",
    )
    estate = tmp_path / "estate"
    hollow = estate / "hollow2"
    hollow.mkdir(parents=True)
    (hollow / "README.md").write_text("# Hollow2\n", encoding="utf-8")
    (hollow / ".git").write_text(f"gitdir: {victim / '.git'}\n", encoding="utf-8")
    report = curate(estate, output=tmp_path / "gitdir-file.json")
    assert TOKEN not in json_blob(report)
    assert any(item["reason"] == "GITDIR_ESCAPE" for item in report["exclusions"])
    item = next(row for row in report["inventory"] if row["name"] == "hollow2")
    assert item["git"] is False
    assert item["remote"] is None


def test_prescheme_userinfo_is_redacted(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    project = estate / "prescheme"
    _init_repo(project, readme="# Prescheme\n")
    _git(
        project,
        "remote",
        "add",
        "origin",
        f"owner:{TOKEN}@https://github.com/example/pre.git",
    )
    report = curate(estate, output=tmp_path / "prescheme.json")
    assert TOKEN not in json_blob(report)
    item = next(row for row in report["inventory"] if row["name"] == "prescheme")
    assert TOKEN not in (item["remote"] or "")
    assert item["remote"] == "https://github.com/example/pre.git"


def test_fullwidth_at_userinfo_does_not_crash_or_echo(tmp_path: Path) -> None:
    planted = f"https://owner:{TOKEN}\uff20github.com/x.git"
    assert TOKEN not in _redact_remote_url(planted)
    estate = tmp_path / "estate"
    project = estate / "fullwidth"
    _init_repo(project, readme="# Fullwidth\n")
    _git(project, "remote", "add", "origin", planted)
    report = curate(estate, output=tmp_path / "fullwidth.json")
    blob = json_blob(report)
    assert TOKEN not in blob
    item = next(row for row in report["inventory"] if row["name"] == "fullwidth")
    assert TOKEN not in (item["remote"] or "")


def test_empty_user_prescheme_token_is_not_echoed(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    project = estate / "emptyuser"
    _init_repo(project, readme="# Empty user\n")
    _git(
        project,
        "remote",
        "add",
        "origin",
        f":{TOKEN}@https://github.com/example/empty.git",
    )
    report = curate(estate, output=tmp_path / "emptyuser.json")
    blob = json_blob(report)
    assert TOKEN not in blob
    item = next(row for row in report["inventory"] if row["name"] == "emptyuser")
    assert TOKEN not in (item["remote"] or "")
    assert item["remote"] in {
        "https://github.com/example/empty.git",
        "redacted-secret-shaped-remote",
        "redacted-userinfo@https://github.com/example/empty.git",
    }
