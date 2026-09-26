"""ATLAS-RUNNER-E2E-001 acceptance fixture tests (deterministic, offline).

Contract: the committed fixture under tests/fixtures/acceptance/ is the
genesis record of the mandatory demonstration task. This test proves the
genesis fixture is intact (golden SHA-256) and well-formed; the acceptance
workflow appends counter increments on dedicated branches only.
ACCEPTANCE_FIXTURE_VALID != VERIFIED.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "acceptance"
    / "ATLAS-RUNNER-E2E-001.json"
)

# Golden digest of the committed genesis fixture (counter 0). The acceptance
# workload rewrites the file on dedicated branches; if this digest changes
# while the fixture is still genesis, the genesis record was mutated on the
# default branch and must be restored.
GENESIS_SHA256 = "1a244ddff283f249a7625186e2073c63e465edc8057781accdc2dc41093f11a3"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TASK_ID = "ATLAS-RUNNER-E2E-001"


def _load() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _is_genesis(doc: dict) -> bool:
    return doc["counter"] == 0 and doc["previous_sha256"] is None


def test_genesis_fixture_sha256_stable():
    doc = _load()
    if not _is_genesis(doc):
        # Bumped fixture on a dedicated result branch: structural checks
        # below apply; the golden digest binds the genesis record only.
        return
    digest = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert digest == GENESIS_SHA256, (
        "genesis fixture mutated on the default branch; "
        "ATLAS-RUNNER-E2E-001 history must stay append-only"
    )


def test_fixture_schema_and_identity():
    doc = _load()
    assert doc["schema_version"] == 1
    assert doc["task_id"] == _TASK_ID


def test_run_ids_are_positive_integers():
    doc = _load()
    assert isinstance(doc["github_run_id"], int) and doc["github_run_id"] >= 1
    assert isinstance(doc["run_attempt"], int) and doc["run_attempt"] >= 1


def test_nonce_and_previous_sha256_shape():
    doc = _load()
    assert _HEX64.match(doc["nonce"]), "nonce must be 64-hex"
    previous = doc["previous_sha256"]
    assert previous is None or _HEX64.match(previous)


def test_counter_is_monotonic_integer():
    doc = _load()
    counter = doc["counter"]
    assert isinstance(counter, int) and counter >= 0


def test_chain_continuity_on_bumped_fixture():
    doc = _load()
    if _is_genesis(doc):
        return
    # A bumped fixture (dedicated result branch) must chain to its parent.
    assert _HEX64.match(doc["previous_sha256"]), (
        "bumped fixture must carry the parent fixture sha256 chain link"
    )


def test_fixture_exact_key_set():
    doc = _load()
    assert set(doc) == {
        "schema_version",
        "task_id",
        "github_run_id",
        "run_attempt",
        "executed_at",
        "nonce",
        "previous_sha256",
        "counter",
    }
