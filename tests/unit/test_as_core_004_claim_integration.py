"""AS-CORE-004 S4: refined subjects reach canonical claims; identity v2 untouched."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

from project_atlas import claim_identity
from project_atlas.claim_identity import canonical_identity_key, claim_id_from_key
from project_atlas.knowledge_compiler import compile_knowledge

HASH = "c" * 64


def test_claim_identity_v2_algorithm_source_unchanged() -> None:
    """Identity implementation diff must remain zero for the hash algorithm."""
    source = Path(inspect.getfile(claim_identity)).read_text(encoding="utf-8")
    # Frozen AS-CORE-003 identity formula markers.
    assert '["v2", project_identity, source_identity, claim_type, field, locator]' in source
    assert "def canonical_identity_key(" in source
    digest = hashlib.sha256(
        canonical_identity_key(
            "proj", "sline-1", "roadmap-status", "package_status", "yamlpath:status"
        ).encode()
    ).hexdigest()
    assert (
        claim_id_from_key(
            canonical_identity_key(
                "proj", "sline-1", "roadmap-status", "package_status", "yamlpath:status"
            )
        )
        == f"claim-{digest[:20]}"
    )


def test_canonical_claims_carry_refined_subjects(tmp_path: Path) -> None:
    entries = [
        {
            "source_id": "source-wp-a",
            "path": "docs/work-packages/AS-EXT-001A.md",
            "classification": "work-package",
            "source": "../../sources/imported-documents/source-wp-a.md",
            "sha256": HASH,
            "text": "# AS-EXT-001A\nStatus: certified\n",
        },
        {
            "source_id": "source-wp-b",
            "path": "docs/work-packages/AS-CORE-004.md",
            "classification": "work-package",
            "source": "../../sources/imported-documents/source-wp-b.md",
            "sha256": "d" * 64,
            "text": "# AS-CORE-004\nStatus: active\n",
        },
    ]
    bundle = compile_knowledge("project-atlas", entries, tmp_path)
    subjects = {claim.subject for claim in bundle.claims}
    assert "wp:AS-EXT-001A" in subjects
    assert "wp:AS-CORE-004" in subjects
    assert "project-atlas" not in subjects
    # Different WP subjects with status dimensions must not false-conflict.
    assert not bundle.conflicts


def test_no_second_identity_engine() -> None:
    # claim_identity remains the sole claim-id derivation module.
    assert hasattr(claim_identity, "canonical_identity_key")
    assert hasattr(claim_identity, "claim_id_from_key")
    assert hasattr(claim_identity, "v2_claim_id")
