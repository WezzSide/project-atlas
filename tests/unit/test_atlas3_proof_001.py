"""AT3-050 agent proof-of-work."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.atlas3.proof import PROOF_STAGES, evaluate_proof


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def test_model_claim_is_not_proof(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    report = evaluate_proof(
        vault,
        "AT3-050-DEMO",
        project_id="harbor-api",
        model_claims_complete=True,
    )
    assert report["model_claim_is_proof"] is False
    assert report["chain_status"] == "UNPROVEN_MODEL_CLAIM"
    assert report["merge_authorization"] == "NOT_GRANTED"
    assert all(report["stages"][name]["status"] == "UNKNOWN" for name in PROOF_STAGES)


def test_full_chain_is_proven_only_with_evidence(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    evidence = {name: {"evidence_ref": f"ref-{name}"} for name in PROOF_STAGES}
    report = evaluate_proof(
        vault,
        "AT3-050-FULL",
        project_id="harbor-api",
        evidence=evidence,
        model_claims_complete=True,
    )
    assert report["chain_status"] == "PROVEN"
    assert report["model_claim_is_proof"] is False


@pytest.mark.parametrize("spoof", (True, 1, ["ci"], {"ref": "x"}))
def test_non_string_evidence_ref_is_not_proof(tmp_path: Path, spoof: object) -> None:
    vault = _vault(tmp_path)
    evidence = {name: {"evidence_ref": spoof} for name in PROOF_STAGES}
    report = evaluate_proof(
        vault,
        "AT3-050-SPOOF",
        project_id="harbor-api",
        evidence=evidence,
    )
    assert report["chain_status"] == "UNKNOWN"
    assert all(report["stages"][name]["status"] == "UNKNOWN" for name in PROOF_STAGES)
