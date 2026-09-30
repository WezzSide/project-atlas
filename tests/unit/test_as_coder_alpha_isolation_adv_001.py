"""AS-CODER-ALPHA-ISOLATION-ADV-HARNESS-001 — adversarial isolation on current main.

Tests-first harness against landed inventory_drift / overview / architecture
APIs. Does not import or duplicate #405 ``source_drift_scope``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.inventory_drift import (
    PACKAGE_ID,
    InventoryDriftError,
    evaluate_connect_inventory_drift,
)
from project_atlas.overview import OverviewError, build_overview_lens
from project_atlas.project_architecture import (
    ProjectArchitectureError,
    _architecture_rank,
    build_architecture_lens,
)
from project_atlas.source_identity import canonical_source_sha256

HARBOR = "harbor"
PORTAL = "portal"
UNKNOWN = "unknown-project"
PORTAL_SECRET = "PORTAL-AKIAIOSFODNN7EXAMPLE"
HARBOR_SECRET = "HARBOR-AKIAIOSFODNN7EXAMPLE"
OUTSIDE_SECRET = "SECRET-OUTSIDE-ESCAPE"
PORTAL_ARCH_MARK = "PORTAL-ONLY-LAYER-C-BLEED"
HARBOR_ARCH_MARK = "Harbor three-layer vault"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_manifest(
    vault: Path,
    root: Path | None,
    sources: list[dict[str, object]],
) -> None:
    payload: dict[str, object] = {"sources": sources}
    if root is not None:
        payload["source_root"] = str(root)
    _write_json(vault / "generated" / "ops" / "connect-manifest.json", payload)


def _write_receipt(vault: Path, project_root: Path) -> None:
    _write_json(
        vault / "generated" / "ops" / "connect-receipt.json",
        {"project_root": str(project_root)},
    )


def _seed_project_note(
    vault: Path,
    project_id: str,
    *,
    title: str,
    sources: list[dict[str, object]],
) -> None:
    note = vault / "projects" / project_id / "project.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    semantic = {"project_id": project_id, "sources": sources, "coverage": []}
    note.write_text(
        f"---\ntype: Project\ntitle: {title}\n---\n\n# {title}\n\n"
        "<!-- atlas:generated:start -->\n## Semantic record\n\n```json\n"
        + json.dumps(semantic)
        + "\n```\n",
        encoding="utf-8",
    )


def _write_imported(vault: Path, source_id: str, text: str) -> None:
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True, exist_ok=True)
    (imported / f"{source_id}.md").write_text(text, encoding="utf-8")


def _dump(*payloads: object) -> str:
    return json.dumps(payloads, default=str)


def _honesty(payload: dict[str, Any]) -> dict[str, Any]:
    honesty = payload.get("honesty")
    assert isinstance(honesty, dict)
    return honesty


def assert_honesty_closed(payload: dict[str, Any]) -> None:
    honesty = _honesty(payload)
    assert honesty.get("unknown_is_healthy") is False
    assert honesty.get("unknown_is_fresh") is False
    assert honesty.get("stale_is_current") is False
    assert honesty.get("lens_is_authority") is False


def leak_count(payload: object, *tokens: str) -> int:
    blob = json.dumps(payload, default=str)
    return sum(1 for token in tokens if token in blob)


def _estate(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two-project estate with sibling secrets, unscoped, and excluded rows."""
    root = tmp_path / "src"
    docs = root / "docs"
    portal_dir = root / "portal"
    docs.mkdir(parents=True)
    portal_dir.mkdir()
    harbor_readme = root / "README.md"
    harbor_plan = docs / "plan.md"
    portal_readme = portal_dir / "README.md"
    portal_plan = portal_dir / "plan.md"
    unowned = root / "unowned.md"
    excluded = root / "secrets.env"
    harbor_readme.write_text("# Harbor\n\nPersistent brain fixture.\n", encoding="utf-8")
    harbor_plan.write_text(
        "# Plan\n\n## Core architectural decision\n\n"
        f"{HARBOR_ARCH_MARK}.\n\n"
        "## Layer A - Source evidence\n\nOriginal harbor docs.\n",
        encoding="utf-8",
    )
    portal_readme.write_text(f"# Portal\n\n{PORTAL_SECRET}\n", encoding="utf-8")
    portal_plan.write_text(
        f"# Portal Plan\n\n## Core architectural decision\n\n{PORTAL_ARCH_MARK}.\n",
        encoding="utf-8",
    )
    unowned.write_text("# Unowned\n\nnot claimed\n", encoding="utf-8")
    excluded.write_text(f"{HARBOR_SECRET}\n", encoding="utf-8")
    vault = tmp_path / "vault"
    _write_manifest(
        vault,
        root,
        [
            {
                "path": "README.md",
                "sha256": canonical_source_sha256(harbor_readme),
                "likely_project": HARBOR,
                "source_id": "source-harbor-readme",
            },
            {
                "path": "docs/plan.md",
                "sha256": canonical_source_sha256(harbor_plan),
                "likely_project": HARBOR,
                "source_id": "source-harbor-plan",
            },
            {
                "path": "docs\\plan.md",
                "sha256": canonical_source_sha256(harbor_plan),
                "likely_project": HARBOR,
                "source_id": "source-harbor-plan-win",
            },
            {
                "path": "portal/README.md",
                "sha256": canonical_source_sha256(portal_readme),
                "likely_project": PORTAL,
                "source_id": "source-portal-readme",
            },
            {
                "path": "portal/plan.md",
                "sha256": canonical_source_sha256(portal_plan),
                "project_id": PORTAL,
                "source_id": "source-portal-plan",
            },
            {
                "path": "unowned.md",
                "sha256": canonical_source_sha256(unowned),
            },
            {
                "path": "sentinel.md",
                "sha256": "00",
                "likely_project": UNKNOWN,
            },
            {
                "path": "secrets.env",
                "sha256": "cafebabe",
                "exclusion_reason": "sensitive-metadata-only",
            },
        ],
    )
    _seed_project_note(
        vault,
        HARBOR,
        title="harbor",
        sources=[
            {"path": "README.md", "source_id": "source-harbor-readme"},
            {"path": "docs/plan.md", "source_id": "source-harbor-plan"},
        ],
    )
    _seed_project_note(
        vault,
        PORTAL,
        title="portal",
        sources=[
            {"path": "portal/README.md", "source_id": "source-portal-readme"},
            {"path": "portal/plan.md", "source_id": "source-portal-plan"},
        ],
    )
    _write_imported(
        vault,
        "source-harbor-readme",
        "# Harbor\n\nPersistent brain fixture.\n",
    )
    _write_imported(
        vault,
        "source-harbor-plan",
        "# Plan\n\n## Core architectural decision\n\n"
        f"{HARBOR_ARCH_MARK}.\n\n"
        "## Layer A - Source evidence\n\nOriginal harbor docs.\n",
    )
    _write_imported(
        vault,
        "source-portal-readme",
        f"# Portal\n\n{PORTAL_SECRET}\n",
    )
    _write_imported(
        vault,
        "source-portal-plan",
        f"# Portal Plan\n\n## Core architectural decision\n\n{PORTAL_ARCH_MARK}.\n",
    )
    return root, vault, portal_readme


def test_source_drift_scope_helper_is_not_required() -> None:
    """Harness must not depend on #405's optional helper module being absent."""
    # Behavior under test is inventory_drift / architecture ownership, not
    # whether source_drift_scope exists on the branch under test.
    assert PACKAGE_ID == "AS-CODER-ALPHA-INVENTORY-DRIFT-001"
    _ = importlib.util.find_spec("project_atlas.source_drift_scope")


def test_sibling_project_bleed_is_zero(tmp_path: Path) -> None:
    _root, vault, portal_readme = _estate(tmp_path)
    portal_readme.write_text(f"# Portal\n\n{PORTAL_SECRET}-rotated\n", encoding="utf-8")

    harbor_drift = evaluate_connect_inventory_drift(vault, HARBOR)
    portal_drift = evaluate_connect_inventory_drift(vault, PORTAL)
    harbor_overview = build_overview_lens(vault, HARBOR)
    portal_overview = build_overview_lens(vault, PORTAL)
    harbor_arch = build_architecture_lens(vault, HARBOR)
    portal_arch = build_architecture_lens(vault, PORTAL)

    assert harbor_drift["status"] == "FRESH"
    assert portal_drift["status"] == "STALE"
    assert "portal/README.md" not in harbor_drift["changed_paths"]
    assert "portal/plan.md" not in harbor_drift["changed_paths"]
    assert "unowned.md" not in harbor_drift["changed_paths"]
    assert "README.md" not in portal_drift["changed_paths"]
    assert "docs/plan.md" not in portal_drift["changed_paths"]

    for payload in (
        harbor_drift,
        portal_drift,
        harbor_overview,
        portal_overview,
        harbor_arch,
        portal_arch,
    ):
        assert_honesty_closed(payload)
        if "source_drift" in payload:
            assert_honesty_closed(payload["source_drift"])

    harbor_blob = _dump(harbor_drift, harbor_overview, harbor_arch)
    assert PORTAL_SECRET not in harbor_blob
    assert PORTAL_ARCH_MARK not in harbor_blob
    assert HARBOR_SECRET not in harbor_blob
    assert HARBOR_ARCH_MARK in json.dumps(harbor_arch)

    assert harbor_overview["source_drift"]["status"] == "FRESH"
    assert portal_overview["source_drift"]["status"] == "STALE"
    assert portal_overview["honesty"]["stale_is_current"] is False
    assert harbor_arch["source_drift"]["status"] == "FRESH"
    assert "portal/plan.md" not in harbor_arch.get("evidence", [])
    assert PORTAL_ARCH_MARK not in json.dumps(harbor_arch.get("slots") or {})

    leaks = leak_count(
        {"harbor": harbor_drift, "overview": harbor_overview, "arch": harbor_arch},
        PORTAL_SECRET,
        PORTAL_ARCH_MARK,
        "portal/README.md",
        "portal/plan.md",
    )
    assert leaks == 0, f"CROSS_PROJECT_LEAK_COUNT={leaks}"


def test_unknown_project_fail_closed_never_healthy(tmp_path: Path) -> None:
    _root, vault, _portal = _estate(tmp_path)
    sentinel = evaluate_connect_inventory_drift(vault, UNKNOWN)
    overview = build_overview_lens(vault, UNKNOWN)
    architecture = build_architecture_lens(vault, UNKNOWN)

    assert sentinel["status"] == "UNKNOWN"
    assert sentinel["reason_code"] == "NO_ACTIVE_SOURCES"
    assert sentinel["changed_paths"] == []
    assert_honesty_closed(sentinel)
    assert sentinel["honesty"]["unknown_is_healthy"] is False

    assert overview["status"] == "unknown"
    assert overview["value"] is None
    assert overview["source_drift"]["status"] == "UNKNOWN"
    assert_honesty_closed(overview)
    assert_honesty_closed(overview["source_drift"])
    assert overview["honesty"]["unknown_is_healthy"] is False

    assert architecture["status"] == "unknown"
    assert architecture["summary"] is None
    assert architecture["source_drift"]["status"] == "UNKNOWN"
    assert_honesty_closed(architecture)
    assert architecture["honesty"]["lens_is_authority"] is False
    assert architecture["honesty"]["unknown_is_healthy"] is False
    arch_blob = json.dumps(architecture)
    assert PORTAL_ARCH_MARK not in arch_blob
    assert HARBOR_ARCH_MARK not in arch_blob
    assert "portal/plan.md" not in arch_blob
    assert all(
        entry.get("path") != "portal/plan.md"
        for entry in (architecture.get("evidence") or [])
        if isinstance(entry, dict)
    )


def test_missing_project_does_not_invent_authority(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    overview = build_overview_lens(vault, HARBOR)
    architecture = build_architecture_lens(vault, HARBOR)
    assert overview["status"] == "unknown"
    assert overview["value"] is None
    assert overview["summary"] is None
    assert overview["authority"] == "derived-lens"
    assert_honesty_closed(overview)
    assert architecture["status"] == "unknown"
    assert architecture["authority"] == "derived-lens"
    assert architecture["honesty"]["lens_is_authority"] is False
    assert architecture["honesty"]["fabricated_fields"] is False


def test_forged_project_ids_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    forged = (
        "../harbor",
        "harbor/../portal",
        r"harbor\portal",
        "C:harbor",
        "CON",
        "harbor.",
        "harbor ",
        "..",
        ".",
    )
    for project_id in forged:
        with pytest.raises(InventoryDriftError):
            evaluate_connect_inventory_drift(vault, project_id)

    slash_forged = ("../harbor", "harbor/portal", r"harbor\portal")
    for project_id in slash_forged:
        with pytest.raises(OverviewError, match="unsafe project id"):
            build_overview_lens(vault, project_id)
        with pytest.raises(ProjectArchitectureError, match="unsafe project id"):
            build_architecture_lens(vault, project_id)

    absent = evaluate_connect_inventory_drift(vault, "")
    assert absent["status"] == "UNKNOWN"
    assert absent["reason_code"] == "PROJECT_REQUIRED"
    assert_honesty_closed(absent)


def test_missing_connect_receipt_does_not_treat_hashes_as_live(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    readme = root / "README.md"
    readme.write_text("# Harbor\n\nstable\n", encoding="utf-8")
    vault = tmp_path / "vault"
    _write_manifest(
        vault,
        None,
        [
            {
                "path": "README.md",
                "sha256": canonical_source_sha256(readme),
                "likely_project": HARBOR,
            }
        ],
    )
    assert not (vault / "generated" / "ops" / "connect-receipt.json").is_file()
    drift = evaluate_connect_inventory_drift(vault, HARBOR)
    assert drift["status"] == "UNKNOWN"
    assert drift["reason_code"] == "SOURCE_ROOT_UNVERIFIED"
    assert_honesty_closed(drift)
    assert drift["honesty"]["unknown_is_fresh"] is False
    assert drift["honesty"]["unknown_is_healthy"] is False

    _seed_project_note(
        vault,
        HARBOR,
        title="harbor",
        sources=[{"path": "README.md", "source_id": "source-readme"}],
    )
    _write_imported(vault, "source-readme", "# Harbor\n\nstable\n")
    overview = build_overview_lens(vault, HARBOR)
    architecture = build_architecture_lens(vault, HARBOR)
    assert overview["source_drift"]["status"] == "UNKNOWN"
    assert architecture["source_drift"]["status"] == "UNKNOWN"
    assert_honesty_closed(overview)
    assert_honesty_closed(architecture)
    assert overview["honesty"]["stale_is_current"] is False


def test_receipt_fallback_stays_project_scoped(tmp_path: Path) -> None:
    root, vault, portal_readme = _estate(tmp_path)
    manifest = json.loads(
        (vault / "generated" / "ops" / "connect-manifest.json").read_text(encoding="utf-8")
    )
    del manifest["source_root"]
    _write_json(vault / "generated" / "ops" / "connect-manifest.json", manifest)
    _write_receipt(vault, root)
    portal_readme.write_text(f"# Portal\n\n{PORTAL_SECRET}-rotated\n", encoding="utf-8")

    harbor_drift = evaluate_connect_inventory_drift(vault, HARBOR)
    portal_drift = evaluate_connect_inventory_drift(vault, PORTAL)
    assert harbor_drift["status"] == "FRESH"
    assert portal_drift["status"] == "STALE"
    assert leak_count(harbor_drift, PORTAL_SECRET, "portal/README.md") == 0
    assert_honesty_closed(harbor_drift)
    assert_honesty_closed(portal_drift)


def test_windows_path_forms_normalize_and_reject_escapes(tmp_path: Path) -> None:
    root = tmp_path / "src"
    docs = root / "docs"
    docs.mkdir(parents=True)
    plan = docs / "plan.md"
    plan.write_text("# Plan\n\nstable\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text(f"{OUTSIDE_SECRET}\n", encoding="utf-8")
    vault = tmp_path / "vault"
    digest = canonical_source_sha256(plan)
    _write_manifest(
        vault,
        root,
        [
            {
                "path": r"docs\plan.md",
                "sha256": digest,
                "likely_project": HARBOR,
            },
            {
                "path": r"..\outside.txt",
                "sha256": "00",
                "likely_project": HARBOR,
            },
            {
                "path": r"C:\Windows\system32\config",
                "sha256": "00",
                "likely_project": HARBOR,
            },
            {
                "path": "C:/Windows/system32/config",
                "sha256": "00",
                "likely_project": HARBOR,
            },
        ],
    )
    drift = evaluate_connect_inventory_drift(vault, HARBOR)
    assert drift["status"] == "STALE"
    assert_honesty_closed(drift)
    assert drift["honesty"]["stale_is_current"] is False
    payload = json.dumps(drift)
    assert OUTSIDE_SECRET not in payload
    assert "../outside.txt" in drift["changed_paths"]
    assert "C:/Windows/system32/config" in drift["changed_paths"]
    assert r"docs\plan.md" not in drift["changed_paths"]
    assert "docs/plan.md" not in drift["changed_paths"]

    filtered = evaluate_connect_inventory_drift(
        vault,
        HARBOR,
        path_filter=lambda path: _architecture_rank(path) is not None,
    )
    assert filtered["status"] == "FRESH"
    assert_honesty_closed(filtered)


def test_unc_source_root_is_unknown_not_fresh(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_manifest(
        vault,
        None,
        [
            {
                "path": "README.md",
                "sha256": "abc",
                "likely_project": HARBOR,
            }
        ],
    )
    manifest_path = vault / "generated" / "ops" / "connect-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["source_root"] = r"\\server\share\atlas"
    _write_json(manifest_path, payload)
    drift = evaluate_connect_inventory_drift(vault, HARBOR)
    assert drift["status"] == "UNKNOWN"
    assert drift["reason_code"] == "SOURCE_ROOT_UNVERIFIED"
    assert_honesty_closed(drift)


def test_architecture_rank_filter_ignores_sibling_and_demo_churn(tmp_path: Path) -> None:
    root, vault, portal_readme = _estate(tmp_path)
    demo = root / "docs" / "demo"
    demo.mkdir()
    demo_arch = demo / "ARCHITECTURE.md"
    demo_arch.write_text("# Demo\n\nv1\n", encoding="utf-8")
    # Seed a hashed demo row so churn is visible to inventory_drift if ranking
    # ever stops excluding docs/demo/ paths (P2 coverage).
    manifest_path = vault / "generated" / "ops" / "connect-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = list(payload.get("sources") or [])
    sources.append(
        {
            "path": "docs/demo/ARCHITECTURE.md",
            "sha256": canonical_source_sha256(demo_arch),
            "likely_project": HARBOR,
            "source_id": "source-demo-arch",
        }
    )
    payload["sources"] = sources
    _write_json(manifest_path, payload)
    readme = root / "README.md"
    readme.write_text("# Harbor\n\nv2\n", encoding="utf-8")
    portal_readme.write_text(f"# Portal\n\n{PORTAL_SECRET}-rotated\n", encoding="utf-8")
    demo_arch.write_text("# Demo\n\nv2\n", encoding="utf-8")

    filtered = evaluate_connect_inventory_drift(
        vault,
        HARBOR,
        path_filter=lambda path: _architecture_rank(path) is not None,
    )
    assert filtered["status"] == "FRESH"
    assert "README.md" not in filtered["changed_paths"]
    assert "docs/demo/ARCHITECTURE.md" not in filtered["changed_paths"]
    assert "portal/plan.md" not in filtered["changed_paths"]
    assert leak_count(filtered, PORTAL_SECRET) == 0

    (root / "docs" / "plan.md").write_text("# Plan\n\ndrifted\n", encoding="utf-8")
    stale = evaluate_connect_inventory_drift(
        vault,
        HARBOR,
        path_filter=lambda path: _architecture_rank(path) is not None,
    )
    assert stale["status"] == "STALE"
    assert stale["honesty"]["stale_is_current"] is False
    assert "docs/plan.md" in stale["changed_paths"] or r"docs\plan.md" in stale["changed_paths"]


def test_stale_overview_is_not_current_and_does_not_echo_secret(tmp_path: Path) -> None:
    root, vault, _portal = _estate(tmp_path)
    readme = root / "README.md"
    readme.write_text(f"# Harbor\n\n{HARBOR_SECRET}\n", encoding="utf-8")
    overview = build_overview_lens(vault, HARBOR)
    architecture = build_architecture_lens(vault, HARBOR)
    assert overview["source_drift"]["status"] == "STALE"
    assert overview["honesty"]["stale_is_current"] is False
    assert architecture["honesty"]["stale_is_current"] is False
    blob = _dump(overview, architecture)
    assert HARBOR_SECRET not in blob
    assert PORTAL_SECRET not in blob
    assert_honesty_closed(overview)
    assert_honesty_closed(architecture)
