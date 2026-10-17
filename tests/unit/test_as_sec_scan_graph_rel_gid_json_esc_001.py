"""AS-SEC-SCAN-GRAPH-REL-GID-JSON-ESC-001 — decoded Graphify ids must not persist."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from project_atlas.graph_relationships import (
    store_from_acceptance,
    write_relationship_outputs,
)
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_source_graphify_id_is_not_persisted(tmp_path: Path) -> None:
    src = tmp_path / "proj"
    vault = tmp_path / "vault"
    gdir = src / "graphify-out"
    gdir.mkdir(parents=True)
    vault.mkdir()
    (src / ".atlas-project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: harbor-api\n",
        encoding="utf-8",
    )
    nodes = (
        f'{{"id":"{ESC}","type":"component","label":"svc"}}\n'
        '{"id":"n2","type":"component","label":"db"}\n'
    )
    edges = (
        f'{{"id":"e1","source":"{ESC}","target":"n2","type":"depends-on",'
        '"confidence":"high","source_documents":["docs/a.md"]}\n'
    )
    (gdir / "nodes.jsonl").write_text(nodes, encoding="utf-8")
    (gdir / "edges.jsonl").write_text(edges, encoding="utf-8")
    assert scan_text(nodes) == []
    assert scan_text(edges) == []
    assert json.loads(edges)["source"] == TOKEN
    manifest = {
        "schema_version": 1,
        "project_id": "harbor-api",
        "sources": [
            {
                "source_id": "nodes",
                "path": "graphify-out/nodes.jsonl",
                "sha256": hashlib.sha256(nodes.encode()).hexdigest(),
                "authority": {"level": "derived"},
            },
            {
                "source_id": "edges",
                "path": "graphify-out/edges.jsonl",
                "sha256": hashlib.sha256(edges.encode()).hexdigest(),
                "authority": {"level": "derived"},
            },
        ],
    }
    _receipt, _resolution, store = store_from_acceptance(
        project_root=src,
        manifest=manifest,
        mapping_table={
            TOKEN: "harbor-api:component:svc",
            "n2": "harbor-api:component:db",
        },
    )
    written_rels = write_relationship_outputs(store, vault=vault)
    assert written_rels
    written = (vault / written_rels[0]).read_text(encoding="utf-8")
    assert TOKEN not in written
    assert scan_text(written) == []
    payload = json.loads(written)
    assert payload["source_graphify_id"] != TOKEN
    assert TOKEN not in json.dumps(payload.get("provenance") or {})
