"""AS-2.0-CTX-002 / AS-2.0-AGENTOS-002 deepen tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.agentos_transitions import (
    AgentOsTransitionError,
    record_phase_transition,
)
from project_atlas.context_pack_composition import (
    ContextCompositionError,
    build_context_pack_composition,
)
from project_atlas.schema import available_schemas, validate_record


def test_context_composition(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = build_context_pack_composition(vault, composition_id="comp-a", pack_id="pack-a")
    assert report["estate_facts_invented"] is False
    validate_record(report, "context-pack-composition")


def test_context_rejects_estate_invention(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ContextCompositionError, match="estate-facts-forbidden"):
        build_context_pack_composition(
            vault,
            composition_id="comp-a",
            pack_id="pack-a",
            invent_estate_facts=True,
        )


def test_agentos_transition_happy(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    report = record_phase_transition(
        vault,
        transition_id="t1",
        session_id="s1",
        from_phase="bootstrap",
        to_phase="preflight",
    )
    assert report["authority_promoted"] is False
    validate_record(report, "agentos-phase-transition")


def test_agentos_transition_forbidden(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(AgentOsTransitionError, match="transition-forbidden"):
        record_phase_transition(
            vault,
            transition_id="t1",
            session_id="s1",
            from_phase="bootstrap",
            to_phase="closed",
        )


def test_docs_and_schemas() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "docs" / "AS-2.0-CTX-002.md").is_file()
    assert (root / "docs" / "AS-2.0-AGENTOS-002.md").is_file()
    assert "context-pack-composition" in available_schemas()
    assert "agentos-phase-transition" in available_schemas()
