"""AS-SEC-SCAN-XPROJ-INDEX-DISPLAY-JSON-ESC-001 — decoded display names must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.secrets import scan_text
from project_atlas.xproj_indexes import build_xproj_indexes, write_xproj_index_outputs

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"
SHA = "a" * 64


def _entity_json(
    *,
    gid: str,
    display: str,
    entity_class: str = "service",
    version: str | None = None,
) -> str:
    attrs = f',\n  "attributes": {{"version": "{version}"}}' if version else ""
    return (
        "{\n"
        '  "schema_version": 1,\n'
        '  "package_id": "AS-XPROJ-001",\n'
        f'  "global_entity_id": "{gid}",\n'
        f'  "entity_class": "{entity_class}",\n'
        f'  "display_name": "{display}",\n'
        '  "authority": {"level": "derived"},\n'
        '  "registration_kind": "explicit",\n'
        '  "status": "registered"'
        f"{attrs}\n"
        "}\n"
    )


def _join_json(
    *,
    project_id: str,
    local: str,
    gid: str,
    evidence_path: str = "docs/a.md",
) -> str:
    return (
        "{\n"
        '  "schema_version": 1,\n'
        '  "package_id": "AS-XPROJ-001",\n'
        f'  "project_id": "{project_id}",\n'
        f'  "project_local_entity_id": "{local}",\n'
        f'  "global_entity_id": "{gid}",\n'
        f'  "evidence_refs": [{{"relative_path": "{evidence_path}", "sha256": "'
        + SHA
        + '"}],\n'
        '  "authority": {"level": "derived"},\n'
        '  "status": "joined"\n'
        "}\n"
    )


def test_json_unicode_escape_display_name_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    entity = vault / "state" / "global-entities" / "svc-harbor.json"
    join = vault / "state" / "global-entities" / "joins" / "harbor-join.json"
    entity.parent.mkdir(parents=True)
    join.parent.mkdir(parents=True)
    raw_entity = _entity_json(gid="svc-harbor", display=ESC)
    raw_join = _join_json(project_id="harbor-api", local="svc-local", gid="svc-harbor")
    entity.write_text(raw_entity, encoding="utf-8")
    join.write_text(raw_join, encoding="utf-8")
    assert scan_text(raw_entity) == []
    result = build_xproj_indexes(vault=vault)
    write_xproj_index_outputs(result, vault=vault)
    written = (vault / "generated" / "xproj" / "indexes" / "services" / "index.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_conflict_project_ids_are_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    entities = vault / "state" / "global-entities"
    joins = entities / "joins"
    entities.mkdir(parents=True)
    joins.mkdir(parents=True)
    (entities / "lib-v1.json").write_text(
        _entity_json(
            gid="ge-lib-shared-v1",
            display="SharedLib",
            entity_class="library",
            version="1.0.0",
        ),
        encoding="utf-8",
    )
    (entities / "lib-v2.json").write_text(
        _entity_json(
            gid="ge-lib-shared-v2",
            display="SharedLib",
            entity_class="library",
            version="2.0.0",
        ),
        encoding="utf-8",
    )
    raw_join = _join_json(project_id=ESC, local="proj-a:lib:shared", gid="ge-lib-shared-v1")
    (joins / "join-a.json").write_text(raw_join, encoding="utf-8")
    (joins / "join-b.json").write_text(
        _join_json(project_id="proj-b", local="proj-b:lib:shared", gid="ge-lib-shared-v2"),
        encoding="utf-8",
    )
    assert scan_text(raw_join) == []
    result = build_xproj_indexes(vault=vault)
    write_xproj_index_outputs(result, vault=vault)
    conflicts = vault / "generated" / "xproj" / "conflicts"
    blob = ""
    if conflicts.is_dir():
        for path in conflicts.glob("*.json"):
            blob += path.read_text(encoding="utf-8")
    projects = vault / "generated" / "xproj" / "indexes" / "projects" / "index.json"
    if projects.is_file():
        blob += projects.read_text(encoding="utf-8")
    assert TOKEN not in blob
    assert scan_text(blob) == []


def test_json_unicode_escape_index_version_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    entity = vault / "state" / "global-entities" / "svc-harbor.json"
    join = vault / "state" / "global-entities" / "joins" / "harbor-join.json"
    entity.parent.mkdir(parents=True)
    join.parent.mkdir(parents=True)
    raw_entity = _entity_json(gid="svc-harbor", display="Harbor", version=ESC)
    raw_join = _join_json(project_id="harbor-api", local="svc-local", gid="svc-harbor")
    entity.write_text(raw_entity, encoding="utf-8")
    join.write_text(raw_join, encoding="utf-8")
    assert scan_text(raw_entity) == []
    result = build_xproj_indexes(vault=vault)
    write_xproj_index_outputs(result, vault=vault)
    written = (vault / "generated" / "xproj" / "indexes" / "services" / "index.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_evidence_path_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    entities = vault / "state" / "global-entities"
    joins = entities / "joins"
    entities.mkdir(parents=True)
    joins.mkdir(parents=True)
    (entities / "lib-v1.json").write_text(
        _entity_json(
            gid="ge-lib-shared-v1",
            display="SharedLib",
            entity_class="library",
            version="1.0.0",
        ),
        encoding="utf-8",
    )
    (entities / "lib-v2.json").write_text(
        _entity_json(
            gid="ge-lib-shared-v2",
            display="SharedLib",
            entity_class="library",
            version="2.0.0",
        ),
        encoding="utf-8",
    )
    raw_join = _join_json(
        project_id="proj-a",
        local="proj-a:lib:shared",
        gid="ge-lib-shared-v1",
        evidence_path=ESC,
    )
    (joins / "join-a.json").write_text(raw_join, encoding="utf-8")
    (joins / "join-b.json").write_text(
        _join_json(project_id="proj-b", local="proj-b:lib:shared", gid="ge-lib-shared-v2"),
        encoding="utf-8",
    )
    assert scan_text(raw_join) == []
    result = build_xproj_indexes(vault=vault)
    write_xproj_index_outputs(result, vault=vault)
    conflicts = vault / "generated" / "xproj" / "conflicts"
    blob = ""
    if conflicts.is_dir():
        for path in conflicts.glob("*.json"):
            blob += path.read_text(encoding="utf-8")
    assert TOKEN not in blob
    assert scan_text(blob) == []


def test_json_unicode_escape_conflict_edge_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    entities = vault / "state" / "global-entities"
    joins = entities / "joins"
    edges = entities / "edges"
    entities.mkdir(parents=True)
    joins.mkdir(parents=True)
    edges.mkdir(parents=True)
    (entities / "lib-v1.json").write_text(
        _entity_json(gid="ge-lib-a", display="LibA", entity_class="library"),
        encoding="utf-8",
    )
    (entities / "lib-v2.json").write_text(
        _entity_json(gid="ge-lib-b", display="LibB", entity_class="library"),
        encoding="utf-8",
    )
    (joins / "join-a.json").write_text(
        _join_json(project_id="proj-a", local="proj-a:lib:a", gid="ge-lib-a"),
        encoding="utf-8",
    )
    (joins / "join-b.json").write_text(
        _join_json(project_id="proj-b", local="proj-b:lib:b", gid="ge-lib-b"),
        encoding="utf-8",
    )
    raw_edge = (
        "{\n"
        '  "schema_version": 1,\n'
        '  "package_id": "AS-XPROJ-002",\n'
        f'  "edge_id": "{ESC}",\n'
        '  "relationship_type": "conflicts-with",\n'
        '  "source_global_entity_id": "ge-lib-a",\n'
        '  "target_global_entity_id": "ge-lib-b",\n'
        '  "link_quality": "inferred",\n'
        f'  "edge_fingerprint": "{SHA}",\n'
        '  "evidence_refs": [{"relative_path": "docs/a.md", "sha256": "'
        + SHA
        + '"}],\n'
        '  "authority": {"level": "derived"},\n'
        '  "status": "registered",\n'
        '  "registration_kind": "explicit",\n'
        '  "truth_boundary": "CROSS-PROJECT EDGE \\u2260 AUTOMATIC AUTHORITY"\n'
        "}\n"
    )
    (edges / "edge.json").write_text(raw_edge, encoding="utf-8")
    assert scan_text(raw_edge) == []
    result = build_xproj_indexes(vault=vault)
    write_xproj_index_outputs(result, vault=vault)
    conflicts = vault / "generated" / "xproj" / "conflicts"
    blob = ""
    if conflicts.is_dir():
        for path in conflicts.glob("*.json"):
            blob += path.read_text(encoding="utf-8")
    rel = vault / "generated" / "xproj" / "indexes" / "relationships" / "index.json"
    if rel.is_file():
        blob += rel.read_text(encoding="utf-8")
    assert TOKEN not in blob
    assert scan_text(blob) == []
