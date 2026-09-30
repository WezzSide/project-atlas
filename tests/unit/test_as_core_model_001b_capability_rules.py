"""AS-CORE-MODEL-001B — explicit Capability emission rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.domain import ConceptType, Maturity
from project_atlas.knowledge_compiler import (
    _capability_concepts,
    _concept,
    _normalize_capability_declarations,
    capability_concept_id,
    compile_knowledge,
    derive_project_maturity,
)


def _entry(
    *,
    source_id: str = "src-1",
    path: str = "README.md",
    classification: str = "readme",
    text: str = "# Hello\n",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": source_id,
        "path": path,
        "classification": classification,
        "text": text,
        "source": f"../../sources/imported-documents/{source_id}.md",
        "sha256": "0" * 64,
    }
    payload.update(extra)
    return payload


def test_capability_concept_id_stable_and_not_project() -> None:
    first = capability_concept_id("demo", "search")
    second = capability_concept_id("demo", "search")
    assert first == second
    assert first.startswith("cap-")
    assert first != "demo"
    assert len(first) == 4 + 32


def test_negative_readme_emits_zero_capabilities() -> None:
    entries = [_entry(text="# Capabilities\n\n- Search\n- Index\n")]
    caps = _capability_concepts("demo", [], entries)
    assert caps == []


def test_marker_list_emits_one_capability_with_stable_id() -> None:
    entries = [
        _entry(
            capabilities=[{"id": "search", "title": "Search"}],
        )
    ]
    caps = _capability_concepts("demo", [], entries)
    assert len(caps) == 1
    assert caps[0].type is ConceptType.CAPABILITY
    assert caps[0].concept_id == capability_concept_id("demo", "search")
    assert caps[0].title == "Search"
    assert caps[0].concept_id != "demo"


def test_concept_type_source_title_from_path_stem_is_stable() -> None:
    entries = [
        _entry(
            source_id="a",
            path="docs/Search Service.md",
            concept_type="Capability",
        ),
        _entry(
            source_id="b",
            path="README.md",
        ),
    ]
    reshuffled = list(reversed(entries))
    first = _capability_concepts("demo", [], entries)
    second = _capability_concepts("demo", [], reshuffled)
    assert len(first) == 1
    assert first[0].concept_id == second[0].concept_id
    assert first[0].title == "Search Service"


def test_slug_collision_without_distinct_ids_fails_closed() -> None:
    entries = [
        _entry(
            capabilities=[
                {"title": "Search API"},
                {"title": "Search-API"},
            ]
        )
    ]
    with pytest.raises(ValueError, match="capability slug collision"):
        _normalize_capability_declarations("demo", entries)


def test_malformed_capabilities_marker_fails_closed() -> None:
    with pytest.raises(ValueError, match="capabilities must be a list"):
        _normalize_capability_declarations(
            "demo",
            [_entry(capabilities="search")],
        )


def test_duplicate_identical_key_collapses_deterministically() -> None:
    entries = [
        _entry(
            capabilities=[
                {"id": "search", "title": "Search"},
                {"id": "search", "title": "Search"},
            ]
        )
    ]
    caps = _capability_concepts("demo", [], entries)
    assert len(caps) == 1
    assert caps[0].concept_id == capability_concept_id("demo", "search")


def test_provides_relationship_from_marker() -> None:
    entries = [
        _entry(
            capabilities=[
                {"id": "search", "title": "Search", "provides": "index-service"},
            ]
        )
    ]
    caps = _capability_concepts("demo", [], entries)
    assert len(caps[0].relationships) == 1
    assert caps[0].relationships[0].type.value == "provides"
    assert caps[0].relationships[0].target == "index-service"


def test_singleton_and_maturity_unaffected(tmp_path: Path) -> None:
    entries = [
        _entry(
            classification="project-overview",
            path="docs/overview.md",
            text="# Overview\n",
            capabilities=[{"id": "search", "title": "Search"}],
        ),
        _entry(
            source_id="src-2",
            classification="architecture",
            path="docs/architecture.md",
            text="# Architecture\n",
            capabilities=[{"id": "search", "title": "Search"}],
        ),
        _entry(
            source_id="src-3",
            classification="security",
            path="docs/security.md",
            text="# Security\n",
            capabilities=[{"id": "search", "title": "Search"}],
        ),
    ]
    maturity = derive_project_maturity(declared_maturity=None, open_conflicts=0, entries=entries)
    assert maturity is Maturity.MVP
    singleton = _concept("demo", [], entries, open_conflicts=0)
    assert singleton.concept_id == "demo"
    assert singleton.type is not ConceptType.CAPABILITY
    assert singleton.maturity is Maturity.MVP
    caps = _capability_concepts("demo", [], entries)
    assert len(caps) == 1
    assert caps[0].maturity is None
    bundle = compile_knowledge("demo", entries, tmp_path)
    assert bundle.concepts[0].concept_id == "demo"
    assert bundle.concepts[0].maturity is Maturity.MVP
    assert any(c.type is ConceptType.CAPABILITY for c in bundle.concepts)


def test_concept_type_capability_does_not_hijack_singleton() -> None:
    entries = [_entry(concept_type="Capability", path="docs/Search.md")]
    singleton = _concept("demo", [], entries, open_conflicts=0)
    assert singleton.concept_id == "demo"
    assert singleton.type is ConceptType.PROJECT
    caps = _capability_concepts("demo", [], entries)
    assert len(caps) == 1
    assert caps[0].type is ConceptType.CAPABILITY


def test_marker_capability_title_with_secret_fails_closed() -> None:
    """F2 / NFR-004: AKIA-like capability titles must not become concepts."""
    entries = [
        _entry(
            path=".atlas-project.yaml",
            capabilities=[
                {"id": "search", "title": "AKIAIOSFODNN7EXAMPLE"},
            ],
        )
    ]
    with pytest.raises(ValueError, match="secret-bearing capability"):
        _normalize_capability_declarations("demo", entries)


def test_concept_type_title_with_secret_fails_closed() -> None:
    entries = [
        _entry(
            path="docs/AKIAIOSFODNN7EXAMPLE.md",
            concept_type="Capability",
        )
    ]
    with pytest.raises(ValueError, match="secret-bearing capability"):
        _normalize_capability_declarations("demo", entries)


def test_marker_capability_provenance_cites_marker_only() -> None:
    """F3: marker-declared caps cite marker evidence, not every document."""
    entries = [
        _entry(
            source_id="marker",
            path=".atlas-project.yaml",
            classification="project-marker",
            capabilities=[{"id": "search", "title": "Search"}],
        ),
        _entry(
            source_id="readme",
            path="README.md",
            capabilities=[{"id": "search", "title": "Search"}],
        ),
    ]
    caps = _capability_concepts("demo", [], entries)
    assert len(caps) == 1
    assert len(caps[0].sources) == 1
    assert caps[0].sources[0].source_id == "marker"
