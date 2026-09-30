"""AS-D-006: static parser registry (FR + ADV)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, get_args

import pytest

from project_atlas.classification import ParserSelection, classify_source
from project_atlas.compilation import CompilationOutcome
from project_atlas.evidence_compiler import extract_source
from project_atlas.parser_registry import (
    STATIC_PARSER_IDS,
    UnknownParserIdError,
    bind_static_parsers,
    get_parser,
    is_registered_parser_id,
    registered_parser_ids,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PARSER_REGISTRY = REPO_ROOT / "src" / "project_atlas" / "parser_registry.py"
EVIDENCE_COMPILER = REPO_ROOT / "src" / "project_atlas" / "evidence_compiler.py"
INGESTION = REPO_ROOT / "src" / "project_atlas" / "ingestion.py"


# --- D006-FR ---


def test_d006_fr001_registry_module_exists() -> None:
    assert PARSER_REGISTRY.is_file()
    from project_atlas import parser_registry as mod

    assert hasattr(mod, "get_parser")
    assert hasattr(mod, "bind_static_parsers")


def test_d006_fr002_closed_set_matches_parser_selection() -> None:
    assert frozenset(get_args(ParserSelection)) == STATIC_PARSER_IDS
    assert registered_parser_ids() == STATIC_PARSER_IDS
    for parser_id in STATIC_PARSER_IDS:
        assert is_registered_parser_id(parser_id)


def test_d006_fr002_every_static_id_resolves() -> None:
    # evidence_compiler bind runs at import (fixture for production table).
    for parser_id in sorted(STATIC_PARSER_IDS):
        handler = get_parser(parser_id)
        assert callable(handler)


def test_d006_fr003_unknown_id_fails_closed() -> None:
    with pytest.raises(UnknownParserIdError, match="unknown parser_id"):
        get_parser("plugin-invented")
    with pytest.raises(UnknownParserIdError):
        get_parser("")
    assert not is_registered_parser_id("plugin-invented")


def test_d006_fr004_bind_rejects_incomplete_and_extra() -> None:
    def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    with pytest.raises(ValueError, match="missing"):
        bind_static_parsers({"none": _noop})
    with pytest.raises(ValueError, match="extra"):
        full = {parser_id: _noop for parser_id in STATIC_PARSER_IDS}
        full["plugin-x"] = _noop
        bind_static_parsers(full)
    # Restore production table after destructive bind attempts.
    import project_atlas.evidence_compiler as ec

    bind_static_parsers(
        {
            "evidence-yaml": ec._parse_evidence_yaml,
            "verify-profile": ec._parse_verify_profile,
            "none": ec._parse_unsupported,
            "project-manifest": ec._parse_kv_lines,
            "adr": ec._parse_kv_lines,
            "kv-markdown": ec._parse_kv_lines,
        }
    )


def test_d006_fr005_one_callable_per_id_exclusivity() -> None:
    # Distinct exclusive handlers for structured vs unsupported vs kv family.
    assert get_parser("evidence-yaml") is not get_parser("verify-profile")
    assert get_parser("evidence-yaml") is not get_parser("none")
    assert get_parser("kv-markdown") is get_parser("adr")
    assert get_parser("kv-markdown") is get_parser("project-manifest")


def test_d006_fr006_dispatch_evidence_yaml() -> None:
    text = (
        "schema_version: 1\n"
        "receipt_type: atlas-work-package\n"
        "work_package: AS-D-006\n"
        "status: in-progress\n"
    )
    result = extract_source(
        "proj",
        {
            "path": "docs/evidence/as-d-006.yaml",
            "text": text,
            "source_id": "src-1",
        },
    )
    assert classify_source("docs/evidence/as-d-006.yaml", text).parser_id == ("evidence-yaml")
    assert result.candidate.outcome in {
        CompilationOutcome.COMPLETE_CANDIDATE,
        CompilationOutcome.PARTIAL_CANDIDATE,
        CompilationOutcome.FAILED,
    }


def test_d006_fr006_dispatch_none_unsupported() -> None:
    result = extract_source(
        "proj",
        {"path": "bin/tool.exe", "text": "\x00\x01", "source_id": "src-x"},
    )
    assert result.candidate.outcome == CompilationOutcome.FAILED
    assert any(d.parser == "none" for d in result.diagnostics)


def test_d006_fr006_dispatch_kv_markdown() -> None:
    result = extract_source(
        "proj",
        {
            "path": "docs/backlog.md",
            "text": "# Backlog\n\n- [ ] item\n",
            "source_id": "src-b",
        },
    )
    assert classify_source("docs/backlog.md", "# Backlog\n").parser_id == ("kv-markdown")
    assert result.candidate.outcome in {
        CompilationOutcome.COMPLETE_CANDIDATE,
        CompilationOutcome.PARTIAL_CANDIDATE,
        CompilationOutcome.FAILED,
    }


def test_d006_fr006_deterministic_repeat() -> None:
    entry = {
        "path": "docs/backlog.md",
        "text": "# Backlog\n\n- [ ] item\n",
        "source_id": "src-b",
    }
    a = extract_source("proj", entry)
    b = extract_source("proj", entry)
    assert a.candidate.outcome == b.candidate.outcome
    assert a.candidate.classification == b.candidate.classification


# --- ADV ---


def test_d006_adv_no_dynamic_plugin_load() -> None:
    source = PARSER_REGISTRY.read_text(encoding="utf-8")
    assert "importlib" not in source
    assert "pkg_resources" not in source
    assert "entry_points(" not in source
    assert "__import__(" not in source
    assert "import_module(" not in source


def test_d006_adv_no_ingestion_edit() -> None:
    # Parallel CORE-OPS-001 owns ingestion._promote — D-006 must not touch it.
    # Compare against absence of AS-D-006 markers in ingestion.py.
    text = INGESTION.read_text(encoding="utf-8")
    assert "AS-D-006" not in text
    assert "parser_registry" not in text


def test_d006_adv_extract_source_uses_get_parser() -> None:
    tree = ast.parse(EVIDENCE_COMPILER.read_text(encoding="utf-8"))
    uses_get_parser = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_parser"
        ):
            uses_get_parser = True
            break
    assert uses_get_parser


def test_d006_adv_no_inline_parser_id_if_chain_in_extract_source() -> None:
    """Dispatch must not reintroduce tip-era if/elif parser_id chain."""
    source = EVIDENCE_COMPILER.read_text(encoding="utf-8")
    # Locate extract_source body after the function def.
    start = source.index("def extract_source(")
    # Next top-level def or EOF — extract_source is last in module.
    body = source[start:]
    assert "get_parser(" in body
    assert 'classification.parser_id == "evidence-yaml"' not in body
    assert 'classification.parser_id == "verify-profile"' not in body
    assert 'classification.parser_id == "none"' not in body


def test_d006_adv_parser_selection_exported() -> None:
    from project_atlas import classification as cls

    assert "ParserSelection" in cls.__all__
    assert cls.ParserSelection is ParserSelection
