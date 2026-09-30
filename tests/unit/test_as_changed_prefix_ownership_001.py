"""AS-CHANGED-PREFIX-OWNERSHIP-001 — folder prefix is not ownership.

``_project_paths`` included any path whose first segment equaled
``project_id``. A sibling-owned ``demo/portal-secret.txt`` therefore
appeared on the ``demo`` what-changed lens.
"""

from __future__ import annotations

from project_atlas.project_changed import _project_paths


def test_sibling_owned_prefix_path_is_excluded() -> None:
    inventory = {
        "sources": [
            {"path": "demo/portal-secret.txt", "project_id": "portal"},
            {"path": "demo/readme.md", "project_id": "demo"},
        ]
    }
    scoped = _project_paths(
        ["demo/portal-secret.txt", "demo/readme.md"],
        "demo",
        inventory,
    )
    assert scoped == ["demo/readme.md"]


def test_unclaimed_prefix_path_still_included() -> None:
    inventory = {"sources": [{"path": "demo/readme.md", "project_id": "demo"}]}
    scoped = _project_paths(
        ["demo/readme.md", "demo/notes.md"],
        "demo",
        inventory,
    )
    assert scoped == ["demo/readme.md", "demo/notes.md"]


def test_owned_path_without_prefix_still_included() -> None:
    inventory = {
        "sources": [{"path": "docs/spec.md", "project_id": "harbor-api"}]
    }
    scoped = _project_paths(["docs/spec.md"], "harbor-api", inventory)
    assert scoped == ["docs/spec.md"]
