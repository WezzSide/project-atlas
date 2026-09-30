"""AS-AUTHZ-RECEIPT-ID-PATH-001 — confine authz audit receipt_id.

``write_authz_audit_receipt`` interpolates ``receipt_id`` into
``generated/ops/authz/{receipt_id}.json``. On live main a traversal
token wrote Layer B ``projects/<id>/project.json`` with the default
operator. Fail closed; receipt is evidence, not authority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.authz import AuthzError, write_authz_audit_receipt

_TRAVERSAL = [
    "../../../projects/harbor-api/project",
    "..",
    "a/b",
    "A-Upper",
    "",
    "  ",
    "x" * 65,
]


@pytest.mark.parametrize("bad", _TRAVERSAL)
def test_authz_audit_receipt_rejects_unsafe_id(tmp_path: Path, bad: str) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    projects = vault / "projects" / "harbor-api"
    projects.mkdir(parents=True)
    (projects / "project.md").write_text("# harbor\n", encoding="utf-8")
    with pytest.raises(AuthzError, match="authz-receipt-id-invalid"):
        write_authz_audit_receipt(vault, receipt_id=bad)
    assert not (projects / "project.json").exists()
    authz_dir = vault / "generated" / "ops" / "authz"
    assert not authz_dir.exists() or not any(authz_dir.rglob("*"))


def test_authz_audit_receipt_accepts_safe_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    receipt = write_authz_audit_receipt(vault, receipt_id="authz-audit")
    assert receipt["receipt_id"] == "authz-audit"
    assert receipt["authority"] is False
    out = vault / "generated" / "ops" / "authz" / "authz-audit.json"
    assert out.is_file()
    assert out.resolve().is_relative_to((vault / "generated" / "ops" / "authz").resolve())
