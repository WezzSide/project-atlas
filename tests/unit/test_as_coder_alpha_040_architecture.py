"""AS-CODER-ALPHA-ARCH-002 structured architecture lens tests."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.connect import connect_project
from project_atlas.project_architecture import ARCHITECTURE_SLOTS
from project_atlas.project_brief import build_project_brief


def test_plan_agents_claude_fill_structured_architecture_slots(tmp_path: Path) -> None:
    root = tmp_path / "atlas-arch"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(
        "# Atlas Arch\n\nPersistent brain for AI-native projects.\n",
        encoding="utf-8",
    )
    (root / "docs" / "plan.md").write_text(
        "# Plan\n\n"
        "Project Atlas converts fragmented project documentation into a source-backed "
        "Open Knowledge Format portfolio.\n\n"
        "# 2. Core architectural decision\n\n"
        "I recommend a **three-layer vault**.\n\n"
        "## Layer A - Source evidence\n\nOriginal project documentation.\n\n"
        "## Layer B - Canonical knowledge\n\nStructured OKF concept documents.\n\n"
        "## Layer C - Portfolio intelligence\n\nCross-project views synthesized from "
        "the canonical layer.\n\n"
        "# 8. Evidence-first ingestion pipeline\n\n"
        "## Stage 1 - Discovery\n\nScan Git repositories and Google Drive folders.\n\n"
        "## Stage 2 - Classification\n\nClassify documents by project and authority.\n\n"
        "## Stage 3 - Extraction\n\nExtract architecture, components, and decisions.\n\n"
        "## Stage 4 - Normalization\n\nConvert extracted information into OKF concepts.\n\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "# AGENTS\n\n"
        "Project Atlas is a local-first, source-backed project knowledge compiler. "
        "The output is both a human-readable portfolio operating system and an "
        "agent-readable knowledge substrate.\n\n"
        "## Code organization\n\n"
        "| Module | Purpose |\n"
        "|--------|---------|\n"
        "| `cli.py` | `atlas` argparse entry point. |\n"
        "| `ingestion.py` | manifest validation, source ingestion, and quarantine. |\n"
        "| `mcp_server.py` | read-only MCP bridge surface with no vault writes. |\n\n"
        "**CLI:** atlas commands expose connect and validation. "
        "A Web surface, read-only MCP bridge, and Obsidian projection are explicitly "
        "truth-bounded.\n\n"
        "Preserve truth boundaries exactly: MODEL_OUTPUT != AUTHORITY. "
        "Out of scope: Atlas-OPT remains closed.\n",
        encoding="utf-8",
    )
    (root / "CLAUDE.md").write_text(
        "# CLAUDE\n\n"
        "The Core pipeline is implemented: discover -> ingest -> build-indexes -> "
        "build-portfolio -> validate, with read-only query lenses layered on top.\n\n"
        "## Architecture\n\n"
        "**Package layout** (`src/project_atlas/`, src-layout):\n\n"
        "- `scaffold.py` - atlas init creates the vault skeleton.\n"
        "- `discovery.py` - recursive SHA-256 source inventory.\n"
        "- `validation.py` - link resolution and provenance hash validation.\n",
        encoding="utf-8",
    )

    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    lens = json.loads(
        (vault / "generated" / "answers" / f"ans-architecture-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    brief = build_project_brief(vault, project_id, refresh=False)

    assert lens["status"] == "derived"
    assert lens["package"] == "AS-CODER-ALPHA-ARCH-002"
    assert lens["generated"]["by"] == "atlas-coder-alpha-architecture-002"
    assert lens["honesty"]["atlas_opt_wake_gate"] == "CLOSED"
    assert lens["honesty"]["lens_is_authority"] is False
    assert lens["evidence"] == ["docs/plan.md", "AGENTS.md", "CLAUDE.md"]
    assert set(lens["slots"]) == set(ARCHITECTURE_SLOTS)
    assert lens["slots"]["knowledge_pipeline"] != "UNKNOWN"
    assert lens["slots"]["data_flow"] != "UNKNOWN"
    assert lens["slots"]["major_components"] != "UNKNOWN"
    assert lens["slots"]["component_responsibilities"] != "UNKNOWN"
    assert lens["slots"]["control_flow"] != "UNKNOWN"
    assert lens["slots"]["runtime_surfaces"] != "UNKNOWN"
    assert lens["slots"]["web_cli_mcp_obsidian"] != "UNKNOWN"
    assert lens["slots"]["known_gaps"] != "UNKNOWN"
    assert lens["summary"] is not None
    assert len(lens["summary"]) <= 720
    assert "KNOWLEDGE_PIPELINE:" in lens["summary"]
    assert brief["architecture_summary"] != brief["purpose"]
    assert brief["architecture_summary"] == lens["summary"]
    assert "docs/plan.md" in brief["evidence_links"]
    assert "AGENTS.md" in brief["evidence_links"]
    assert "CLAUDE.md" in brief["evidence_links"]


def test_layer_meanings_come_from_source_not_hardcoded_atlas(tmp_path: Path) -> None:
    root = tmp_path / "layer-other"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("# Layer Other\n\nPurpose.\n", encoding="utf-8")
    (root / "docs" / "plan.md").write_text(
        "# Plan\n\n## Core architectural decision\n\n"
        "We use Layer A, Layer B, and Layer C.\n\n"
        "## Layer A - Presentation UI\n\nBrowser clients and forms.\n\n"
        "## Layer B - API services\n\nHTTP handlers and auth.\n\n"
        "## Layer C - Persistence\n\nDatabase and object storage.\n",
        encoding="utf-8",
    )
    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    lens = json.loads(
        (vault / "generated" / "answers" / f"ans-architecture-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    pipeline = lens["slots"]["knowledge_pipeline"]
    assert pipeline != "UNKNOWN"
    assert "Presentation UI" in pipeline or "Browser clients" in pipeline
    assert "source evidence" not in pipeline.lower()
    assert "canonical okf" not in pipeline.lower()


def test_clean_line_preserves_underscores() -> None:
    from project_atlas.project_architecture import _clean_line

    assert "MODEL_OUTPUT" in _clean_line("Preserve MODEL_OUTPUT != AUTHORITY")
    assert "mcp_server.py" in _clean_line("See `mcp_server.py` bridge")


def test_demo_architecture_and_filename_cli_not_surface_authority(tmp_path: Path) -> None:
    root = tmp_path / "arch-demo-noise"
    (root / "docs" / "demo").mkdir(parents=True)
    (root / "README.md").write_text("# Arch Demo Noise\n\nPurpose.\n", encoding="utf-8")
    (root / "docs" / "plan.md").write_text(
        "# Plan\n\n## 2. Core architectural decision\n\nI recommend a **three-layer vault**.\n",
        encoding="utf-8",
    )
    (root / "docs" / "demo" / "ARCHITECTURE.md").write_text(
        "# Demo Architecture\n\nMentioned surfaces: CLI, Web, MCP, Obsidian (demo theatre).\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "# Agents\n\n- `cli.py` — argparse entry only; no surface prose here.\n",
        encoding="utf-8",
    )
    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    lens = json.loads(
        (vault / "generated" / "answers" / f"ans-architecture-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "docs/demo/ARCHITECTURE.md" not in (lens.get("evidence") or [])
    # Filename-only cli.py must not invent CLI/Web/MCP/Obsidian surfaces.
    assert lens["slots"]["web_cli_mcp_obsidian"] == "UNKNOWN"


def test_readme_only_keeps_architecture_unknown(tmp_path: Path) -> None:
    root = tmp_path / "readme-only-arch"
    root.mkdir()
    (root / "README.md").write_text(
        "# README Only Arch\n\n"
        "This README says architecture, components, MCP, CLI, and Obsidian, but README "
        "is not an architecture authority for ARCH-002.\n",
        encoding="utf-8",
    )

    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    lens = json.loads(
        (vault / "generated" / "answers" / f"ans-architecture-{project_id}.json").read_text(
            encoding="utf-8"
        )
    )
    brief = build_project_brief(vault, project_id, refresh=False)

    assert lens["status"] == "unknown"
    assert lens["summary"] is None
    assert lens["evidence"] == []
    assert set(lens["slots"]) == set(ARCHITECTURE_SLOTS)
    assert all(value == "UNKNOWN" for value in lens["slots"].values())
    assert brief["architecture_summary"] == "UNKNOWN"
