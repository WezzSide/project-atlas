"""AS-SPEC-004 OKF v0.2 concept-note conformance tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from project_atlas.domain import (
    ConceptRecord,
    ConceptType,
    KnowledgeState,
    ProvenanceReference,
    ReviewState,
)
from project_atlas.ingestion import _generated_content
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.okf_renderer import render_concept_note
from project_atlas.schema import validate_record


def _concept() -> ConceptRecord:
    return ConceptRecord(
        concept_id="project-1",
        project_id="project-1",
        type=ConceptType.PROJECT,
        title="Project One",
        description="A deterministic project concept.",
        resource="projects/project-1/concepts.md",
        tags=["atlas", "project"],
        knowledge_state=KnowledgeState.EVIDENCE_BACKED,
        review_state=ReviewState.PENDING_HUMAN_REVIEW,
        generated={"by": "agent:project-atlas", "at": None},
        verified={"by": None, "at": None},
        sources=[
            ProvenanceReference(
                source_id="source-1",
                project_id="project-1",
                resource="sources/imported-documents/source-1.md",
            )
        ],
    )


def test_okf_concept_note_has_mandatory_fields_and_valid_schema() -> None:
    concept = _concept()
    rendered = render_concept_note(concept, "projects/project-1/concepts.md")
    frontmatter = yaml.safe_load(rendered.split("---", 2)[1])

    assert frontmatter["type"] == "Project"
    assert frontmatter["title"] == "Project One"
    assert frontmatter["resource"] == "projects/project-1/concepts.md"
    assert frontmatter["tags"] == ["atlas", "project"]
    validate_record(frontmatter, "concept-record")


def test_okf_concept_note_matches_golden_file() -> None:
    expected = Path("tests/fixtures/okf/project-one-concepts.md").read_text(encoding="utf-8")
    assert render_concept_note(_concept(), "projects/project-1/concepts.md") == expected


def test_concept_render_replay_is_byte_identical() -> None:
    entry = {
        "source_id": "source-replay",
        "path": "README.md",
        "classification": "project-overview",
        "source": "sources/imported-documents/source-replay.md",
        "sha256": "b" * 64,
        "text": "# Overview\nPurpose: deterministic output.",
    }
    first = render_bundle(
        compile_knowledge("project-1", [entry], Path("/tmp/as-spec-004-replay")),
        "project-1",
    )
    second = render_bundle(
        compile_knowledge("project-1", [entry], Path("/tmp/as-spec-004-replay")),
        "project-1",
    )
    assert first["projects/project-1/concepts.md"] == second["projects/project-1/concepts.md"]


def test_generated_region_can_be_regenerated_around_human_content(tmp_path: Path) -> None:
    entry = {
        "source_id": "source-human",
        "path": "README.md",
        "classification": "project-overview",
        "source": "sources/imported-documents/source-human.md",
        "sha256": "c" * 64,
        "text": "# Overview\nPurpose: preserve humans.",
    }
    generated = render_bundle(compile_knowledge("project-1", [entry], tmp_path), "project-1")[
        "projects/project-1/concepts.md"
    ]
    path = tmp_path / "projects/project-1/concepts.md"
    path.parent.mkdir(parents=True)
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
    replay = render_bundle(compile_knowledge("project-1", [entry], tmp_path), "project-1")[
        "projects/project-1/concepts.md"
    ]
    preserved = _generated_content(path, replay)
    assert "Human-owned note." in preserved
    assert "Human conclusion." in preserved
