"""Fleet node contracts (infra/fleet-runtime/contracts): the imported texts are pinned by
digest, parse, and are the files the supervisor names."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "infra" / "fleet-runtime" / "contracts"
SUPERVISOR = ROOT / "infra" / "fleet-runtime" / "node" / "atlas_mission_supervisor.py"

DIGESTS = {
    "GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md": (
        "b4e9027006fead151ecd606fe74bccf43bf28fc68d1e3056ed78dfa1d32a5373"
    ),
    "GLOBAL-GOALS.json": "dceb2b376b76fec7ef2049238ce952d44c69aec5dc9b72d83c7975fd2bff49bf",
    "GLOBAL-CONTINUOUS-GOALS.json": (
        "408fa6862fc0cf7bf6baf2b9c96c0a8ddae6d9ccd150324c321c52094cff9db8"
    ),
    "AUTONOMY-POLICY.json": "b4ab61e448cd81aa77ca3d4310c9cdfd7f10617fe88297bd5fe9ff100375cd79",
}
READ_BY_SUPERVISOR = (
    "GLOBAL-AUTONOMOUS-OPERATING-CONTRACT.md",
    "GLOBAL-GOALS.json",
    "GLOBAL-CONTINUOUS-GOALS.json",
)


@pytest.mark.parametrize("name", sorted(DIGESTS))
def test_contract_text_is_the_imported_text(name: str) -> None:
    assert hashlib.sha256((CONTRACTS / name).read_bytes()).hexdigest() == DIGESTS[name]


def test_only_the_recorded_contracts_and_their_record_are_present() -> None:
    present = {path.name for path in CONTRACTS.iterdir() if path.is_file()}
    assert present == {*DIGESTS, "PROVENANCE.md"}


@pytest.mark.parametrize("name", sorted(n for n in DIGESTS if n.endswith(".json")))
def test_json_contracts_parse(name: str) -> None:
    assert isinstance(json.loads((CONTRACTS / name).read_text(encoding="utf-8")), dict)


def test_provenance_record_names_every_digest() -> None:
    record = (CONTRACTS / "PROVENANCE.md").read_text(encoding="utf-8")
    for name, digest in DIGESTS.items():
        assert name in record and digest in record, name


def test_supervisor_names_the_contracts_it_reads() -> None:
    source = SUPERVISOR.read_text(encoding="utf-8")
    for name in READ_BY_SUPERVISOR:
        assert f"contracts/{name}" in source, name
    assert "AUTONOMY-POLICY.json" not in source
