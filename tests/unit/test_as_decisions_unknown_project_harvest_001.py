"""AS-DECISIONS-UNKNOWN-PROJECT-HARVEST-001 — unowned ADRs are not governing.

``_decision_headings_from_imports`` treated missing / ``unknown-project``
``likely_project`` as owned by every requested project. An unowned ADR
heading became ``ACTIVE_GOVERNING`` on an unrelated lens.
"""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.project_decisions import (
    _decision_headings_from_imports,
    build_decisions_lens,
)


def _write_connect(vault: Path, sources: list[dict[str, str]]) -> None:
    path = vault / "generated" / "ops" / "connect-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": sources}), encoding="utf-8")


def test_unowned_adr_is_not_active_governing_on_foreign_lens(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    (imported / "foreign-adr.md").write_text(
        "# ADR-0001 We will adopt PostgreSQL 16\n\nWe will adopt PostgreSQL 16.\n",
        encoding="utf-8",
    )
    _write_connect(
        vault,
        [
            {
                "source_id": "foreign-adr",
                "path": "docs/adr/0001-postgres.md",
            }
        ],
    )
    assert _decision_headings_from_imports(vault, "harbor-api") == []
    lens = build_decisions_lens(vault, "harbor-api")
    assert lens["active_governing_count"] == 0
    assert lens["decisions"] == []


def test_explicit_unknown_project_is_not_harvested(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (imported / "orphan-adr.md").write_text(
        "# ADR-0002 We will prefer Redis 7\n",
        encoding="utf-8",
    )
    _write_connect(
        vault,
        [
            {
                "source_id": "orphan-adr",
                "path": "docs/adr/0002-redis.md",
                "likely_project": "unknown-project",
            }
        ],
    )
    assert _decision_headings_from_imports(vault, "harbor-api") == []


def test_owned_adr_still_harvested(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    imported = vault / "sources" / "imported-documents"
    imported.mkdir(parents=True)
    (imported / "owned-adr.md").write_text(
        "# ADR-0003 We will adopt PostgreSQL 16\n",
        encoding="utf-8",
    )
    _write_connect(
        vault,
        [
            {
                "source_id": "owned-adr",
                "path": "docs/adr/0003-postgres.md",
                "likely_project": "harbor-api",
            }
        ],
    )
    found = _decision_headings_from_imports(vault, "harbor-api")
    assert found
    assert found[0]["status"] == "ACTIVE_GOVERNING"
    assert found[0]["title"].startswith("ADR-0003")
