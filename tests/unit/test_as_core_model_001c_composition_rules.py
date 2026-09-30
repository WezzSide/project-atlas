"""AS-CORE-MODEL-001C — allow-list v1 composition rules (explicitness + identity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.domain import ConceptType, Maturity
from project_atlas.knowledge_compiler import (
    _allowlist_concepts,
    _capability_concepts,
    _concept,
    _normalize_architecture_declarations,
    _normalize_component_declarations,
    _normalize_decision_declarations,
    _normalize_status_declarations,
    _parse_emit_concepts,
    _render_concepts,
    allowlist_concept_id,
    capability_concept_id,
    compile_knowledge,
    derive_project_maturity,
    render_bundle,
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


def test_allowlist_concept_id_stable_and_not_project() -> None:
    first = allowlist_concept_id("demo", ConceptType.COMPONENT.value, "auth")
    second = allowlist_concept_id("demo", ConceptType.COMPONENT.value, "auth")
    assert first == second
    assert first.startswith("comp-")
    assert first != "demo"
    assert len(first) == 5 + 32


def test_a1_readme_only_emits_zero_allowlist_concepts() -> None:
    entries = [
        _entry(
            text="# Components\n\n- Auth\n\n# Decisions\n\n- Use Postgres\n",
        )
    ]
    assert _allowlist_concepts("demo", [], entries) == []


def test_a2_architecture_classification_without_opt_in_emits_nothing() -> None:
    entries = [
        _entry(
            classification="architecture",
            path="docs/architecture.md",
            text="# Architecture\n",
        )
    ]
    assert _allowlist_concepts("demo", [], entries) == []


def test_a2_decision_classification_without_opt_in_emits_nothing() -> None:
    entries = [
        _entry(
            classification="decision",
            path="docs/adr/ADR-001-storage.md",
            text="# ADR 001\n",
        )
    ]
    assert _allowlist_concepts("demo", [], entries) == []


def test_a3_project_status_marker_emits_stable_id() -> None:
    entries = [
        _entry(
            path=".atlas-project.yaml",
            project_status={"id": "current", "title": "Current Status"},
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert len(concepts) == 1
    assert concepts[0].type is ConceptType.PROJECT_STATUS
    assert concepts[0].concept_id == allowlist_concept_id(
        "demo", ConceptType.PROJECT_STATUS.value, "current"
    )
    assert concepts[0].concept_id != "demo"


def test_a4_components_marker_emits_sorted_stable_ids() -> None:
    entries = [
        _entry(
            path=".atlas-project.yaml",
            components=[
                {"id": "search", "title": "Search"},
                {"id": "auth", "title": "Auth"},
            ],
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert [c.type for c in concepts] == [ConceptType.COMPONENT, ConceptType.COMPONENT]
    ids = [c.concept_id for c in concepts]
    assert ids == sorted(ids)
    assert allowlist_concept_id("demo", ConceptType.COMPONENT.value, "auth") in ids
    assert allowlist_concept_id("demo", ConceptType.COMPONENT.value, "search") in ids


def test_a5_architecture_opt_in_emits_one() -> None:
    entries = [
        _entry(
            classification="architecture",
            path="docs/architecture.md",
            text="# Architecture\n",
            emit_concepts=["architecture"],
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert len(concepts) == 1
    assert concepts[0].type is ConceptType.ARCHITECTURE
    assert concepts[0].concept_id.startswith("arch-")


def test_a6_decision_adr_stem_with_opt_in_emits_one() -> None:
    entries = [
        _entry(
            classification="decision",
            path="docs/adr/ADR-001-storage.md",
            text="# ADR 001 Storage\n",
            emit_concepts=["decision"],
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert len(concepts) == 1
    assert concepts[0].type is ConceptType.DECISION
    assert concepts[0].concept_id.startswith("decision-")


def test_a6_decision_explicit_id_with_opt_in() -> None:
    entries = [
        _entry(
            classification="decision",
            path="docs/notes/storage.md",
            decision_id="storage-choice",
            emit_concepts=["decision"],
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert len(concepts) == 1
    assert concepts[0].concept_id == allowlist_concept_id(
        "demo", ConceptType.DECISION.value, "storage-choice"
    )


def test_a7_evidenced_relationships_only() -> None:
    entries = [
        _entry(
            path=".atlas-project.yaml",
            components=[
                {
                    "id": "auth",
                    "title": "Auth",
                    "relationships": [{"type": "part_of", "target": "demo"}],
                }
            ],
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert len(concepts[0].relationships) == 1
    assert concepts[0].relationships[0].type.value == "part_of"
    assert concepts[0].relationships[0].target == "demo"


def test_a7_undeclared_relationships_absent() -> None:
    entries = [
        _entry(
            path=".atlas-project.yaml",
            components=[{"id": "auth", "title": "Auth"}],
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert concepts[0].relationships == []


def test_a8_singleton_maturity_and_capability_stable(tmp_path: Path) -> None:
    entries = [
        _entry(
            classification="project-overview",
            path="docs/overview.md",
            capabilities=[{"id": "search", "title": "Search"}],
            components=[{"id": "auth", "title": "Auth"}],
            emit_concepts=["architecture"],
        ),
        _entry(
            source_id="src-2",
            classification="architecture",
            path="docs/architecture.md",
            capabilities=[{"id": "search", "title": "Search"}],
            components=[{"id": "auth", "title": "Auth"}],
            emit_concepts=["architecture"],
        ),
        _entry(
            source_id="src-3",
            classification="security",
            path="docs/security.md",
            capabilities=[{"id": "search", "title": "Search"}],
            components=[{"id": "auth", "title": "Auth"}],
            emit_concepts=["architecture"],
        ),
    ]
    maturity = derive_project_maturity(declared_maturity=None, open_conflicts=0, entries=entries)
    assert maturity is Maturity.MVP
    singleton = _concept("demo", [], entries, open_conflicts=0)
    assert singleton.concept_id == "demo"
    assert singleton.maturity is Maturity.MVP
    caps = _capability_concepts("demo", [], entries)
    assert len(caps) == 1
    assert caps[0].concept_id == capability_concept_id("demo", "search")
    allowlist = _allowlist_concepts("demo", [], entries)
    types = {c.type for c in allowlist}
    assert ConceptType.COMPONENT in types
    assert ConceptType.ARCHITECTURE in types
    assert ConceptType.CAPABILITY not in types
    bundle = compile_knowledge("demo", entries, tmp_path)
    assert bundle.concepts[0].concept_id == "demo"
    assert bundle.concepts[0].maturity is Maturity.MVP
    assert any(c.type is ConceptType.CAPABILITY for c in bundle.concepts)
    assert any(c.type is ConceptType.COMPONENT for c in bundle.concepts)
    # Non-singleton concepts sorted by concept_id
    extra_ids = [c.concept_id for c in bundle.concepts[1:]]
    assert extra_ids == sorted(extra_ids)


def test_a12_allowlist_enclosure_no_requirement_emission() -> None:
    entries = [
        _entry(
            emit_concepts=["requirement", "risk", "capability", "architecture"],
            classification="architecture",
            path="docs/architecture.md",
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert all(c.type is ConceptType.ARCHITECTURE for c in concepts)
    assert not any(c.type is ConceptType.CAPABILITY for c in concepts)
    assert not any(c.type.value == "Requirement" for c in concepts)


def test_slug_collision_fails_closed() -> None:
    entries = [
        _entry(
            components=[
                {"title": "Auth API"},
                {"title": "Auth-API"},
            ]
        )
    ]
    with pytest.raises(ValueError, match="component slug collision"):
        _normalize_component_declarations("demo", entries)


def test_malformed_components_fails_closed() -> None:
    with pytest.raises(ValueError, match="components must be a list"):
        _normalize_component_declarations(
            "demo",
            [_entry(components="auth")],
        )


def test_malformed_emit_concepts_fails_closed() -> None:
    with pytest.raises(ValueError, match="emit_concepts must be a list"):
        _parse_emit_concepts([_entry(emit_concepts="architecture")], "demo")


def test_duplicate_component_key_collapses() -> None:
    entries = [
        _entry(
            components=[
                {"id": "auth", "title": "Auth"},
                {"id": "auth", "title": "Auth"},
            ]
        )
    ]
    concepts = _allowlist_concepts("demo", [], entries)
    assert len(concepts) == 1


def test_new_relation_type_fails_closed() -> None:
    entries = [
        _entry(
            components=[
                {
                    "id": "auth",
                    "title": "Auth",
                    "relationships": [{"type": "invented_edge", "target": "demo"}],
                }
            ]
        )
    ]
    with pytest.raises(ValueError, match="RelationType"):
        _allowlist_concepts("demo", [], entries)


def test_decision_without_id_or_adr_stem_withheld() -> None:
    entries = [
        _entry(
            classification="decision",
            path="docs/notes/random.md",
            emit_concepts=["decision"],
        )
    ]
    assert _allowlist_concepts("demo", [], entries) == []


def test_identity_never_equals_project_id() -> None:
    for concept_type in (
        ConceptType.PROJECT_STATUS,
        ConceptType.ARCHITECTURE,
        ConceptType.COMPONENT,
        ConceptType.DECISION,
    ):
        concept_id = allowlist_concept_id("demo", concept_type.value, "x")
        assert concept_id != "demo"
        assert concept_id.split("-", 1)[0] in {"status", "arch", "comp", "decision"}


def test_adv_c12_component_secret_title_fails_closed() -> None:
    """ADV-C-12 / NFR-004: secret-bearing allow-list titles must be rejected."""
    entries = [
        _entry(
            components=[{"id": "auth", "title": "AKIAIOSFODNN7EXAMPLE"}],
        )
    ]
    with pytest.raises(ValueError, match="secret-bearing allow-list"):
        _normalize_component_declarations("demo", entries)


def test_adv_c12_project_status_secret_title_fails_closed() -> None:
    entries = [
        _entry(
            path=".atlas-project.yaml",
            project_status={"id": "current", "title": "AKIAIOSFODNN7EXAMPLE"},
        )
    ]
    with pytest.raises(ValueError, match="secret-bearing allow-list"):
        _normalize_status_declarations("demo", entries, frozenset())


def test_adv_c12_architecture_secret_title_fails_closed() -> None:
    entries = [
        _entry(
            classification="architecture",
            path="docs/AKIAIOSFODNN7EXAMPLE.md",
            emit_concepts=["architecture"],
            text="# Architecture\n",
        )
    ]
    with pytest.raises(ValueError, match="secret-bearing allow-list"):
        _normalize_architecture_declarations("demo", entries, frozenset({"architecture"}))


def test_adv_c12_decision_secret_title_fails_closed() -> None:
    entries = [
        _entry(
            classification="decision",
            path="docs/adr/ADR-001-storage.md",
            emit_concepts=["decision"],
            decision_id="AKIAIOSFODNN7EXAMPLE",
            text="# Storage\n",
        )
    ]
    with pytest.raises(ValueError, match="secret-bearing allow-list"):
        _normalize_decision_declarations("demo", entries, frozenset({"decision"}))


def test_render_concepts_okf_frontmatter_leads_with_single_marker_pair(
    tmp_path: Path,
) -> None:
    """F1/F2 remediation: leading ``---`` + exactly one generated marker pair."""
    entries = [
        _entry(
            path=".atlas-project.yaml",
            components=[{"id": "auth", "title": "Auth"}],
            capabilities=[{"id": "search", "title": "Search"}],
        )
    ]
    bundle = compile_knowledge("demo", entries, tmp_path)
    rendered = render_bundle(bundle, "demo")["projects/demo/concepts.md"]
    assert rendered.startswith("---\n")
    assert rendered.count("<!-- atlas:generated:start -->") == 1
    assert rendered.count("<!-- atlas:generated:end -->") == 1
    # Frontmatter precedes the generated region (not H1-before-YAML).
    assert rendered.index("---\n") < rendered.index("<!-- atlas:generated:start -->")
    assert not rendered.lstrip().startswith("# Concepts")
    # Multi-concept bodies share the single generated region.
    assert "# Auth" in rendered
    assert "# Search" in rendered
    # Direct helper stays consistent with bundle render.
    assert _render_concepts("demo", bundle.concepts) == rendered


def test_at011_human_regions_preserved_with_multi_concept(tmp_path: Path) -> None:
    """A-10 / ADV-C-13: human regions outside markers survive multi-concept replay."""
    from project_atlas.ingestion import _generated_content

    entries = [
        _entry(
            path=".atlas-project.yaml",
            components=[{"id": "auth", "title": "Auth"}],
            text="# Overview\nPurpose: preserve humans.",
        )
    ]
    generated = render_bundle(compile_knowledge("demo", entries, tmp_path), "demo")[
        "projects/demo/concepts.md"
    ]
    path = tmp_path / "projects/demo/concepts.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        generated.replace(
            "---\n\n<!-- atlas:generated:start -->",
            "---\n\nHuman-owned note.\n\n<!-- atlas:generated:start -->",
        ).replace(
            "<!-- atlas:generated:end -->\n",
            "<!-- atlas:generated:end -->\n\nHuman conclusion.\n",
        ),
        encoding="utf-8",
    )
    replay = render_bundle(compile_knowledge("demo", entries, tmp_path), "demo")[
        "projects/demo/concepts.md"
    ]
    preserved = _generated_content(path, replay)
    assert preserved.startswith("---\n")
    assert "Human-owned note." in preserved
    assert "Human conclusion." in preserved
    assert preserved.count("<!-- atlas:generated:start -->") == 1
    assert preserved.count("<!-- atlas:generated:end -->") == 1
