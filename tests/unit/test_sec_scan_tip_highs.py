"""Regression for tip SEC-SCAN-A HIGH findings (A-002 / A-014 / A-021)."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_contracts.event_package import PackageValidationError, _confined
from atlas_contracts.paths import safe_relative_path
from project_atlas.ingestion import _resolve_authorized_source_root
from project_atlas.secrets import REDACTED_PLACEHOLDER, redact_text, scan_text


@pytest.mark.parametrize(
    "relative",
    (
        "C:Windows",
        "C:foo",
        "foo:bar",
        r"C:\Windows",
        "../escape",
        "/abs",
    ),
)
def test_event_package_confined_matches_safe_relative_path(tmp_path: Path, relative: str) -> None:
    """SEC-SCAN-A-002: _confined must reject what safe_relative_path rejects."""
    with pytest.raises(ValueError):
        safe_relative_path(relative, label="p")
    with pytest.raises(PackageValidationError):
        _confined(tmp_path, relative)


def test_authorized_source_root_rejects_symlink(tmp_path: Path) -> None:
    """SEC-SCAN-A-014: symlink --source must fail closed before resolve."""
    auth = tmp_path / "auth"
    auth.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(auth, target_is_directory=True)
    except OSError as exc:  # pragma: no cover
        pytest.skip(f"symlink unavailable: {exc}")
    assert link.is_symlink()
    with pytest.raises(ValueError, match="non-symlink"):
        _resolve_authorized_source_root(link, str(auth.resolve()))


def test_redact_text_removes_full_pem_block() -> None:
    """SEC-SCAN-A-021: PEM body must not survive redact_text."""
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEfakekeymaterialCANARY\n-----END RSA PRIVATE KEY-----"
    )
    assert [f.pattern for f in scan_text(text)] == ["private-key"]
    red = redact_text(text)
    assert "CANARY" not in red
    assert "MIIE" not in red
    assert "PRIVATE KEY" not in red
    assert REDACTED_PLACEHOLDER in red
