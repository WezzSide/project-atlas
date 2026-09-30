"""AS-ACCEPT-002 Band B — post-graph regression oracles (AX-GRF).

Frozen IDs from AS-ACCEPTANCE-EXPANSION.md §9.2 / wave design §6:
AX-GRF-001, AX-GRF-002, AX-GRF-007, AX-GRF-008.

Public Graph resolve contracts only — no GRAPH-003 edge-store product
scenarios; no Band A reopen; no src/ mutation under ACCEPT-002.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.unit._as_accept_002_helpers import (
    hash_tree,
    materialize_knowledge_vault,
    truth_plane_paths,
)

from project_atlas.graph_resolution import (
    ALLOWED_WRITE_PREFIXES,
    AUTHORITY_LEVEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    GraphResolutionError,
    resolve_nodes,
    write_resolution_outputs,
)
from project_atlas.knowledge_query import answer_to_json, query_knowledge

# Truth/query/ops consumers that must remain graph-optional (AX-GRF-007).
# Note: ingestion may wire GRAPH-001 acceptance — out of this oracle's consumer set.
_CORE_CONSUMER_MODULES = (
    "project_atlas.knowledge_query",
    "project_atlas.knowledge_compiler",
    "project_atlas.scaffold",
    "project_atlas.discovery",
    "project_atlas.indexes",
    "project_atlas.validation",
    "project_atlas.ops_health",
)

_GRAPH_MODULE_NAMES = frozenset(
    {
        "project_atlas.graph_resolution",
        "project_atlas.graph_relationships",
        "project_atlas.graph_acceptance",
        "project_atlas.xproj_registry",
    }
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refs() -> list[dict[str, str]]:
    return [{"relative_path": "graphify-out/nodes.jsonl", "sha256": "a" * 64}]


def _resolve_fixture_nodes() -> list[dict[str, object]]:
    """Deterministic mixed resolve batch (resolved + quarantine) for Band B."""
    return [
        {
            "id": "ax-grf-dec",
            "type": "decision",
            "label": "accept-002 band-b fixture",
            "atlas_entity_id": "project-atlas:decision:ax-grf-dec",
        },
        {
            "id": "ax-grf-doc",
            "type": "document",
            "label": "derived-only document",
        },
        {
            "id": "ax-grf-foreign",
            "type": "document",
            "project_id": "other-project",
        },
    ]


def _forbidden_write_census(vault: Path) -> dict[str, tuple[int, str]]:
    """Plant + snapshot truth/query paths resolve must never touch (AX-GRF-002)."""
    relatives = (
        "state/authoritative-state/ax-grf-sentinel.json",
        "state/current-state/ax-grf-sentinel.json",
        "state/claims/ax-grf-sentinel.json",
        "claims/ax-grf-sentinel.json",
        "generated/query/cache.json",
        "generated/indexes/ax-grf-sentinel.json",
        "relationships/nodes.json",
    )
    census: dict[str, tuple[int, str]] = {}
    for relative in relatives:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_text('{"sentinel":true,"package":"AS-ACCEPT-002"}\n', encoding="utf-8")
        census[relative] = (path.stat().st_mtime_ns, _sha256(path))
    return census


def _assert_census_stable(vault: Path, before: dict[str, tuple[int, str]]) -> None:
    for relative, (mtime_ns, digest) in before.items():
        path = vault / relative
        assert path.is_file(), f"missing forbidden-path probe: {relative}"
        assert path.stat().st_mtime_ns == mtime_ns, f"mtime changed: {relative}"
        assert _sha256(path) == digest, f"bytes changed: {relative}"


def _module_source_imports_graph(module_name: str) -> list[str]:
    """Return graph-related import names found in a Core module's source AST."""
    module = importlib.import_module(module_name)
    source_path = getattr(module, "__file__", None)
    assert source_path is not None
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"), filename=source_path)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _GRAPH_MODULE_NAMES or alias.name.startswith(
                    "project_atlas.graph_"
                ):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in _GRAPH_MODULE_NAMES or node.module.startswith("project_atlas.graph_"):
                hits.append(node.module)
            elif node.module == "project_atlas":
                for alias in node.names:
                    if alias.name.startswith("graph_") or alias.name == "xproj_registry":
                        hits.append(f"project_atlas.{alias.name}")
    return hits


def test_ax_grf_001_graph_not_authority_query_and_bytes_stable(tmp_path: Path) -> None:
    """AX-GRF-001: GRAPH≠AUTHORITY — authority fixture bytes + query answers unchanged.

    After graph resolve (+ optional derived emits), Core authoritative query JSON
    and truth-plane bytes must be byte-identical to the pre-resolve snapshot.
    """
    vault = materialize_knowledge_vault(tmp_path)
    assert AUTHORITY_LEVEL == "derived"
    assert PACKAGE_ID == "AS-GRAPH-002"
    assert "AUTHORITY" in TRUTH_BOUNDARY

    before_answer = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    before_truth = {p.as_posix(): _sha256(p) for p in truth_plane_paths(vault)}
    before_tree = hash_tree(vault / "state")

    result = resolve_nodes(
        _resolve_fixture_nodes(),
        project_id="project-atlas",
        source_artifact_refs=_refs(),
    )
    assert result.resolved_count >= 1
    written = write_resolution_outputs(result, vault=vault)
    assert written
    for relative in written:
        assert any(relative.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES)
        assert relative.startswith("generated/graph/")

    after_answer = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    assert after_answer == before_answer
    after_truth = {p.as_posix(): _sha256(p) for p in truth_plane_paths(vault)}
    assert after_truth == before_truth
    assert hash_tree(vault / "state") == before_tree

    # Derived emits must declare derived authority — never Layer-B winners.
    for relative in written:
        if relative.endswith(".json") and "/explanations/" not in relative:
            payload = (vault / relative).read_text(encoding="utf-8")
            assert '"level": "derived"' in payload or '"authority"' in payload


def test_ax_grf_002_resolve_write_census_forbidden_trees(tmp_path: Path) -> None:
    """AX-GRF-002: resolve write census — no writes to auth/claims/query caches.

    Forbidden-tree mtime+sha must stay stable across resolve + write_resolution_outputs.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    before = _forbidden_write_census(vault)
    time.sleep(0.01)

    result = resolve_nodes(
        _resolve_fixture_nodes(),
        project_id="project-atlas",
        source_artifact_refs=_refs(),
    )
    written = write_resolution_outputs(result, vault=vault)
    assert written
    _assert_census_stable(vault, before)

    for relative in written:
        assert any(relative.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES)
        for forbidden in (
            "state/authoritative-state/",
            "state/current-state/",
            "state/claims/",
            "claims/",
            "generated/query/",
            "generated/indexes/",
            "relationships/",
        ):
            assert not relative.startswith(forbidden)

    # Path policy itself rejects truth-plane targets (public helper).
    from project_atlas.graph_resolution import promote_resolution_path_forbidden

    for relative in (
        "state/authoritative-state/x.json",
        "generated/query/cache.json",
        "claims/x.json",
        "relationships/nodes.json",
    ):
        with pytest.raises(GraphResolutionError, match="path-policy-forbidden"):
            promote_resolution_path_forbidden(relative)


def test_ax_grf_007_consumer_isolation_core_graph_optional(tmp_path: Path) -> None:
    """AX-GRF-007: consumer isolation — Core green with graph optional / unused.

    Core consume-path modules must not hard-import graph packages; knowledge
    query must succeed without invoking resolve or requiring graph emits.
    """
    for module_name in _CORE_CONSUMER_MODULES:
        hits = _module_source_imports_graph(module_name)
        assert hits == [], f"{module_name} hard-imports graph surface: {hits}"

    # Fresh vault — no generated/graph artifacts — Core query still works.
    vault = materialize_knowledge_vault(tmp_path)
    assert not (vault / "generated" / "graph").exists()
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    payload = answer_to_json(answer)
    assert '"package": "AS-CORE-007"' in payload or '"package":"AS-CORE-007"' in payload
    assert '"status": "ok"' in payload

    # Subprocess isolation: import knowledge_query without ever loading graph_*.
    # Avoids in-process importlib.reload identity skew that breaks sibling suites.
    script = f"""
import sys
from pathlib import Path
from project_atlas.knowledge_query import answer_to_json, query_knowledge
vault = Path(r"{vault}")
payload = answer_to_json(
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
)
assert '"status": "ok"' in payload
graph_hits = [n for n in sys.modules if n.startswith("project_atlas.graph_")]
assert graph_hits == [], graph_hits
print("AX-GRF-007-OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 0, proc.stderr
    assert "AX-GRF-007-OK" in proc.stdout


def test_ax_grf_008_resolve_replay_byte_identical(tmp_path: Path) -> None:
    """AX-GRF-008: resolve replay — byte-identical emits x2 (NFR-001)."""
    nodes = _resolve_fixture_nodes()
    first = resolve_nodes(nodes, project_id="project-atlas", source_artifact_refs=_refs())
    second = resolve_nodes(
        list(reversed(nodes)),
        project_id="project-atlas",
        source_artifact_refs=_refs(),
    )
    assert first.to_json() == second.to_json()

    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    vault_a.mkdir()
    vault_b.mkdir()
    written_a = write_resolution_outputs(first, vault=vault_a)
    written_b = write_resolution_outputs(second, vault=vault_b)
    assert written_a == written_b
    for relative in written_a:
        assert (vault_a / relative).read_bytes() == (vault_b / relative).read_bytes()

    # Same vault: second write is byte-stable (deterministic atomic replace).
    before = {rel: _sha256(vault_a / rel) for rel in written_a}
    time.sleep(0.01)
    write_resolution_outputs(first, vault=vault_a)
    after = {rel: _sha256(vault_a / rel) for rel in written_a}
    assert after == before
