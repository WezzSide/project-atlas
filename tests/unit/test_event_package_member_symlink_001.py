"""AT3-014 / event-package member symlink must fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas_contracts.event_package import PackageValidationError, _raw_inventory


def test_raw_inventory_refuses_symlinked_member(tmp_path: Path) -> None:
    root = tmp_path / "src"
    pkg = root / "events" / "AE-1"
    outside = tmp_path / "stolen"
    pkg.mkdir(parents=True)
    outside.mkdir()
    (outside / "event.md").write_text("FOREIGN BODY SECRET\n", encoding="utf-8")
    for name in ("event.md", "event.json", "provenance.json", "receipt.yaml"):
        (pkg / name).write_text("placeholder\n", encoding="utf-8")
    (pkg / "event.md").unlink()
    (pkg / "event.md").symlink_to(outside / "event.md")
    with pytest.raises(PackageValidationError, match="symlink"):
        _raw_inventory(root, "events/AE-1")
