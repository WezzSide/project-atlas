"""D-078 owner-authorized Windows non-system volume root.

Trace: D-PROJECT-ATLAS-CLOUD-D049-DEV-VOLUME-ROOT-078
Preserves historical Local Run A FAIL on 198350319 (D:\\ refused by default).
Does not weaken default filesystem-root / home / C:\\ refusal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.estate_discovery import (
    ROOT_MODE_OWNER_AUTHORIZED_VOLUME,
    EstateDiscoveryError,
    authorize_discovery_root,
    discover_estate,
    format_discovery_human,
    is_unc_root,
    is_windows_drive_volume_root,
    is_windows_system_volume_root,
    normalize_root_mode,
    refuse_dangerous_authorized_root,
    windows_system_drive_letter,
    write_discovery_report,
)
from project_atlas.web_api.discovery import load_estate_discovery_view


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_proj(root: Path) -> Path:
    _write(root / "README.md", f"# {root.name}\n")
    _write(root / "package.json", f'{{"name":"{root.name}"}}\n')
    (root / ".git").mkdir(parents=True, exist_ok=True)
    _write(
        root / ".git" / "config",
        '[remote "origin"]\n\turl = https://example.invalid/org/app.git\n',
    )
    return root


def _fake_windows_volume(
    monkeypatch: pytest.MonkeyPatch,
    volume: Path,
    *,
    system: bool = False,
    unc: bool = False,
) -> None:
    import project_atlas.estate_discovery as ed

    vol_key = ed.canonical_path_key(volume)

    def _is_vol(path: Path) -> bool:
        return ed.canonical_path_key(Path(path)) == vol_key

    monkeypatch.setattr(ed, "is_filesystem_root", lambda path: _is_vol(path))
    monkeypatch.setattr(
        ed,
        "is_windows_drive_volume_root",
        lambda path, host_os=None: (not unc) and _is_vol(path),
    )
    monkeypatch.setattr(
        ed,
        "is_windows_system_volume_root",
        lambda path, host_os=None, environ=None: system and _is_vol(path),
    )
    monkeypatch.setattr(ed, "is_unc_root", lambda path: unc and _is_vol(path))


def test_a_normal_bounded_directory_unchanged(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _make_proj(estate / "alpha")
    report = discover_estate(estate)
    assert report["authorized_root_mode"] == "BOUNDED_DIRECTORY"
    assert report["volume_root_authorized"] is False
    assert report["volume_root_kind"] == "NONE"
    paths = {Path(p["path"]).name for p in report["candidates"]["projects"]}
    assert "alpha" in paths


def test_b_windows_volume_without_mode_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    volume.mkdir()
    _fake_windows_volume(monkeypatch, volume)
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        refuse_dangerous_authorized_root(volume)
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        discover_estate(volume)


def test_c_windows_volume_with_explicit_mode_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    _make_proj(volume / "alpha")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    assert report["authorized_root_mode"] == "OWNER_AUTHORIZED_VOLUME_ROOT"
    assert report["volume_root_authorized"] is True
    assert report["volume_root_kind"] == "NON_SYSTEM_WINDOWS_VOLUME"
    assert report["security"]["volume_root_authorized"] is True
    assert report["security"]["whole_disk_scan"] is False
    names = {Path(p["path"]).name for p in report["candidates"]["projects"]}
    assert "alpha" in names
    human = format_discovery_human(report)
    assert "OWNER_AUTHORIZED_VOLUME_ROOT" in human
    assert "volume_root_authorized: true" in human
    assert "not an ordinary bounded-directory scan" in human


def test_d_system_volume_with_explicit_mode_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "C"
    volume.mkdir()
    _fake_windows_volume(monkeypatch, volume, system=True)
    with pytest.raises(EstateDiscoveryError, match="SYSTEM_VOLUME_ROOT_NOT_ALLOWED"):
        discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_e_home_refuses_even_with_volume_mode() -> None:
    with pytest.raises(EstateDiscoveryError, match="HOME_DIRECTORY_NOT_ALLOWED"):
        authorize_discovery_root(Path.home(), root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Linux/macOS filesystem-root contract uses Path('/'); "
        "Path.cwd().anchor on Windows is a drive volume (often D:\\), "
        "which D-078 may accept under owner-authorized-volume. "
        "Windows volume policy is covered by the D-078 Windows-specific tests."
    ),
)
def test_f_linux_filesystem_root_refuses() -> None:
    """POSIX filesystem root stays refused, including explicit volume mode.

    D-083: do not derive this root from Path.cwd().anchor. On GitHub
    windows-latest that anchor is typically a non-system volume (D:\\),
    which is the D-078 capability — not a Linux '/' stand-in.
    """
    fs_root = Path("/")
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        refuse_dangerous_authorized_root(fs_root)
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        authorize_discovery_root(fs_root, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows default drive-root refusal; POSIX root is test_f",
)
def test_f_windows_default_drive_root_refuses_without_volume_mode() -> None:
    """Real Windows drive root stays refused unless explicitly authorized.

    Policy check only — does not walk the volume (Local D-081 remains the
    authentic-estate scan). Explicit non-system acceptance stays in test_c.
    """
    drive_root = Path(Path.cwd().anchor)
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        refuse_dangerous_authorized_root(drive_root)


def test_g_unc_root_refuses_as_volume_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "unc"
    volume.mkdir()
    _fake_windows_volume(monkeypatch, volume, unc=True)
    with pytest.raises(EstateDiscoveryError, match="UNC_VOLUME_ROOT_NOT_ALLOWED"):
        discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_h_external_reparse_escape_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    outside = tmp_path / "outside-secret"
    _make_proj(outside)
    volume.mkdir()
    (volume / "escape").symlink_to(outside, target_is_directory=True)
    _make_proj(volume / "inside")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    paths = {p["path"] for p in report["candidates"]["projects"]}
    assert any(Path(p).name == "inside" for p in paths)
    assert not any("outside-secret" in p for p in paths)
    assert report["security"]["unsafe_path_escapes_allowed"] == 0


def test_i_symlink_loop_no_crash_no_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "D"
    a = volume / "loop-a"
    b = volume / "loop-b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "to-b").symlink_to(b, target_is_directory=True)
    (b / "to-a").symlink_to(a, target_is_directory=True)
    _make_proj(volume / "real")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    names = {Path(p["path"]).name for p in report["candidates"]["projects"]}
    assert "real" in names


def test_j_volume_letter_case_aliases_are_deterministic() -> None:
    from project_atlas.estate_discovery import _windows_volume_letter

    assert _windows_volume_letter(Path("D:/")) == _windows_volume_letter(Path("d:\\"))
    assert _windows_volume_letter(Path("D:/")) == "D"


def test_k_non_root_directory_with_volume_mode_refuses(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    estate.mkdir()
    with pytest.raises(EstateDiscoveryError, match="VOLUME_MODE_REQUIRES_WINDOWS_VOLUME_ROOT"):
        discover_estate(estate, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_l_cli_api_web_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    volume = tmp_path / "D"
    vault = tmp_path / "vault"
    _make_proj(volume / "alpha")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, vault=vault, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    write_discovery_report(report, vault / "generated" / "ops" / "estate-discovery-report.json")
    view = load_estate_discovery_view(vault)
    assert view["authorized_root"] == report["authorized_root"]
    assert view["authorized_root_mode"] == "OWNER_AUTHORIZED_VOLUME_ROOT"
    assert view["volume_root_authorized"] is True
    assert view["volume_root_kind"] == "NON_SYSTEM_WINDOWS_VOLUME"
    assert view["scan"]["scan_complete"] == report["scan"]["scan_complete"]

    rc = main(
        [
            "discover",
            "--root",
            str(volume),
            "--root-mode",
            ROOT_MODE_OWNER_AUTHORIZED_VOLUME,
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authorized_root_mode"] == "OWNER_AUTHORIZED_VOLUME_ROOT"
    assert payload["volume_root_authorized"] is True


def test_cli_default_volume_root_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    volume.mkdir()
    _fake_windows_volume(monkeypatch, volume)
    assert main(["discover", "--root", str(volume)]) == 1


def test_normalize_root_mode_rejects_force_aliases() -> None:
    with pytest.raises(EstateDiscoveryError, match="UNKNOWN_ROOT_MODE"):
        normalize_root_mode("unsafe")
    with pytest.raises(EstateDiscoveryError, match="UNKNOWN_ROOT_MODE"):
        normalize_root_mode("force")
    assert normalize_root_mode("owner-authorized-volume") == (ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_unc_classifier_detects_unc_strings() -> None:
    assert is_unc_root(Path("\\\\server\\share"))
    assert not is_windows_drive_volume_root(Path("D:/"), host_os="posix")


def test_system_drive_letter_from_stable_env() -> None:
    assert windows_system_drive_letter(environ={"SystemDrive": "C:"}) == "C"
    assert windows_system_drive_letter(environ={"SYSTEMROOT": "C:\\Windows"}) == "C"
    assert windows_system_drive_letter(environ={}) is None


def test_unknown_system_drive_on_windows_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If SystemDrive cannot be determined on nt, treat the volume as system."""
    import project_atlas.estate_discovery as ed

    volume = tmp_path / "D"
    volume.mkdir()
    monkeypatch.setattr(ed, "is_windows_drive_volume_root", lambda path, host_os=None: True)
    monkeypatch.setattr(ed, "_windows_volume_letter", lambda path: "D")
    assert is_windows_system_volume_root(volume, host_os="nt", environ={})
    assert not is_windows_system_volume_root(volume, host_os="posix", environ={})


def test_discover_help_names_volume_policy(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["discover", "--help"])
    text = capsys.readouterr().out
    assert "owner-authorized-volume" in text
    assert "bounded-directory" in text
    assert "system volume" in text.lower() or "C:\\" in text
