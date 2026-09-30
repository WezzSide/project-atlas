"""D-178 / KDIFF_TZ_AWARE_CRASH — UTC-aware comparison matrix.

Canonical policy: every parsed instant is UTC-aware. Naive date-only and
naive ISO clocks are interpreted as UTC (legacy chronology), not local time.
Offsets are converted, never stripped.

CLI and LIVE_API must share those semantics and must never raise
aware-vs-naive TypeError or empty-reset the HTTP connection.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.app_service import open_app_service
from project_atlas.bitemporal import (
    ClaimValidityWindow,
    _as_utc,
    _parse_instant,
    evaluate_as_of,
)
from project_atlas.knowledge_diff import read_as_of
from project_atlas.schema import validate_record

WINDOW_FROM = "2024-06-01"
WINDOW_TO = "2024-06-30"
INSIDE = "2024-06-15"
OUTSIDE_BEFORE = "2024-05-31"
OUTSIDE_AFTER = "2024-07-01"


def _window(
    claim_id: str,
    valid_from: str,
    *,
    valid_to: str | None = None,
    evidence_kind: str = "document-declared",
) -> ClaimValidityWindow:
    return ClaimValidityWindow(
        claim_id=claim_id,
        valid_from=valid_from,
        valid_to=valid_to,
        knowledge_compilation_id="compile-1",
        evidence_kind=evidence_kind,  # type: ignore[arg-type]
        core005_temporal_status="current",
    )


def _closed() -> list[ClaimValidityWindow]:
    return [_window("claim.closed", WINDOW_FROM, valid_to=WINDOW_TO)]


def _open_ended() -> list[ClaimValidityWindow]:
    return [_window("claim.open", WINDOW_FROM)]


def _select(windows: list[ClaimValidityWindow], as_of: str) -> dict[str, Any]:
    result = evaluate_as_of(
        windows,
        as_of_valid_time=as_of,
        subject="harbor.datastore",
        field="engine",
    )
    validate_record(result, "bitemporal-as-of-result")
    return result


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _kdiff_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    _write_json(
        vault / "state" / "claims" / "harbor-api.json",
        {
            "schema_version": 1,
            "project_id": "harbor-api",
            "claims": [
                {
                    "schema_version": 2,
                    "claim_id": "claim.closed",
                    "subject": "doc:harbor-api-datastore",
                    "field": "engine",
                    "value": "PostgreSQL 16",
                    "provenance": [
                        {
                            "schema_version": 1,
                            "source_id": "src-ds",
                            "resource": "sources/src-ds.md",
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        vault / "generated" / "ops" / "bitemporal" / "default-validity-catalog.json",
        {
            "schema_version": 1,
            "package_id": "AS-2.0-TEMPORAL-001",
            "compat_snapshot_id": "atlas-1.0.0-compat",
            "catalog_id": "default",
            "window_count": 1,
            "windows": [
                {
                    "schema_version": 1,
                    "package_id": "AS-2.0-TEMPORAL-001",
                    "compat_snapshot_id": "atlas-1.0.0-compat",
                    "claim_id": "claim.closed",
                    "valid_from": WINDOW_FROM,
                    "valid_to": WINDOW_TO,
                    "knowledge_compilation_id": "compile-1",
                    "evidence_kind": "document-declared",
                    "rationale": "explicit-validity-window",
                    "authority": {"level": "derived", "note": "test"},
                    "truth_boundary": "VALIDITY WINDOW ≠ AUTHORITY",
                    "generated": {"by": "project-atlas"},
                }
            ],
            "authority": {"level": "derived", "note": "test"},
            "truth_boundary": "VALIDITY CATALOG ≠ TEMPORAL EVALUATOR REWRITE",
            "generated": {"by": "project-atlas"},
        },
    )
    return vault


# ---------------------------------------------------------------------------
# Parser / policy
# ---------------------------------------------------------------------------


def test_d178_canonical_form_is_utc_aware() -> None:
    naive = datetime(2024, 6, 15, 0, 0, 0)
    zoned = datetime(2024, 6, 15, 1, 0, 0, tzinfo=UTC)
    assert _as_utc(naive).tzinfo is UTC
    assert _as_utc(naive) == datetime(2024, 6, 15, 0, 0, tzinfo=UTC)
    assert _as_utc(zoned) == zoned


@pytest.mark.parametrize(
    "raw, expected_utc",
    [
        ("2024-06-15", "2024-06-15T00:00:00+00:00"),
        ("2024-06-15T00:00:00", "2024-06-15T00:00:00+00:00"),
        ("2024-06-15T00:00:00Z", "2024-06-15T00:00:00+00:00"),
        ("2024-06-15T00:00:00+00:00", "2024-06-15T00:00:00+00:00"),
        ("2024-06-15T01:00:00+01:00", "2024-06-15T00:00:00+00:00"),
        ("2024-06-15T00:00:00-05:00", "2024-06-15T05:00:00+00:00"),
    ],
)
def test_d178_parse_instant_matrix(raw: str, expected_utc: str) -> None:
    parsed = _parse_instant(raw, field="as-of")
    assert parsed.tzinfo is not None
    assert parsed == datetime.fromisoformat(expected_utc)


def test_d178_offset_is_not_stripped() -> None:
    plus_one = _parse_instant("2024-06-01T00:00:00+01:00", field="as-of")
    zulu = _parse_instant("2024-06-01T00:00:00Z", field="as-of")
    assert plus_one != zulu
    assert plus_one == datetime(2024, 5, 31, 23, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Coverage matrix (closed + open-ended)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "as_of",
    [
        "2024-06-15",
        "2024-06-15T00:00:00",
        "2024-06-15T00:00:00Z",
        "2024-06-15T00:00:00+00:00",
        "2024-06-15T02:00:00+02:00",
        "2024-06-15T00:00:00-05:00",
    ],
)
def test_d178_inside_interval_all_supported_forms(as_of: str) -> None:
    result = _select(_closed(), as_of)
    assert result["status"] == "selected"
    assert result["selected_claim_id"] == "claim.closed"


@pytest.mark.parametrize(
    "as_of",
    [
        WINDOW_FROM,
        f"{WINDOW_FROM}T00:00:00",
        f"{WINDOW_FROM}T00:00:00Z",
        f"{WINDOW_FROM}T00:00:00+00:00",
    ],
)
def test_d178_lower_bound_inclusive(as_of: str) -> None:
    result = _select(_closed(), as_of)
    assert result["status"] == "selected"


@pytest.mark.parametrize(
    "as_of",
    [
        WINDOW_TO,
        f"{WINDOW_TO}T00:00:00",
        f"{WINDOW_TO}T00:00:00Z",
        f"{WINDOW_TO}T00:00:00+00:00",
    ],
)
def test_d178_upper_bound_inclusive(as_of: str) -> None:
    result = _select(_closed(), as_of)
    assert result["status"] == "selected"


@pytest.mark.parametrize(
    "as_of",
    [
        OUTSIDE_BEFORE,
        f"{OUTSIDE_BEFORE}T00:00:00Z",
        f"{OUTSIDE_BEFORE}T00:00:00+00:00",
        "2024-05-31T23:59:59Z",
        OUTSIDE_AFTER,
        f"{OUTSIDE_AFTER}T00:00:00Z",
        "2024-06-30T00:00:01Z",
        # +01:00 on the start calendar day is the previous UTC day — not stripped.
        "2024-06-01T00:00:00+01:00",
    ],
)
def test_d178_outside_interval(as_of: str) -> None:
    result = _select(_closed(), as_of)
    assert result["status"] == "not_found"
    assert result["selected_claim_id"] is None


@pytest.mark.parametrize(
    "as_of",
    [
        INSIDE,
        "2099-01-01",
        "2099-01-01T00:00:00Z",
        "2099-01-01T00:00:00+00:00",
        "2024-06-01T00:00:00-05:00",
    ],
)
def test_d178_open_ended_covers_future_and_offsets(as_of: str) -> None:
    result = _select(_open_ended(), as_of)
    assert result["status"] == "selected"
    assert result["selected_claim_id"] == "claim.open"


def test_d178_timezone_equivalent_instants_compare_identically() -> None:
    forms = (
        "2024-06-15T00:00:00Z",
        "2024-06-15T00:00:00+00:00",
        "2024-06-15T01:00:00+01:00",
        "2024-06-14T19:00:00-05:00",
    )
    results = [_select(_closed(), form)["status"] for form in forms]
    assert results == ["selected"] * len(forms)


def test_d178_date_only_legacy_behavior_preserved() -> None:
    result = _select(_closed(), "2024-06-15")
    assert result["status"] == "selected"
    naive_clock = _select(_closed(), "2024-06-15T00:00:00")
    assert naive_clock["status"] == "selected"
    assert naive_clock["selected_claim_id"] == result["selected_claim_id"]


def test_d178_no_aware_naive_typeerror() -> None:
    for as_of in (
        "2024-06-15",
        "2024-06-15T12:00:00",
        "2024-06-15T12:00:00Z",
        "2024-06-15T12:00:00+00:00",
        "2024-06-15T14:00:00+02:00",
        "2024-06-15T07:00:00-05:00",
    ):
        _select(_closed(), as_of)


# ---------------------------------------------------------------------------
# kdiff reader + LIVE_API
# ---------------------------------------------------------------------------


def test_d178_kdiff_reader_accepts_z_and_offset(tmp_path: Path) -> None:
    vault = _kdiff_vault(tmp_path)
    for as_of in (
        "2024-06-15",
        "2024-06-15T00:00:00",
        "2024-06-15T00:00:00Z",
        "2024-06-15T00:00:00+00:00",
        "2024-06-15T02:00:00+02:00",
        "2024-06-15T00:00:00-05:00",
    ):
        snap = read_as_of(vault, project_id="harbor-api", as_of_valid_time=as_of)
        cells = snap["cells"]
        selected = [c for c in cells if c["disposition"] == "selected"]
        assert selected, f"expected selected cell for {as_of}: {snap}"
        assert selected[0]["selected_claim_id"] == "claim.closed"


def test_d178_kdiff_reader_offset_not_stripped(tmp_path: Path) -> None:
    vault = _kdiff_vault(tmp_path)
    stripped_would_cover = read_as_of(
        vault,
        project_id="harbor-api",
        as_of_valid_time="2024-06-01T00:00:00+01:00",
    )
    zulu = read_as_of(vault, project_id="harbor-api", as_of_valid_time="2024-06-01T00:00:00Z")
    stripped_cells = [c for c in stripped_would_cover["cells"] if c["disposition"] == "selected"]
    zulu_cells = [c for c in zulu["cells"] if c["disposition"] == "selected"]
    assert stripped_cells == []
    assert zulu_cells and zulu_cells[0]["selected_claim_id"] == "claim.closed"


def test_d178_app_service_and_live_api_share_semantics(tmp_path: Path) -> None:
    vault = _kdiff_vault(tmp_path)
    service = open_app_service(vault)
    forms = (
        "2024-06-15",
        "2024-06-15T00:00:00",
        "2024-06-15T00:00:00Z",
        "2024-06-15T00:00:00+00:00",
        "2024-06-15T02:00:00+02:00",
        "2024-06-15T00:00:00-05:00",
        "2024-06-01T00:00:00+01:00",
    )
    cli_dispositions: dict[str, list[str]] = {}
    for as_of in forms:
        payload = service.kdiff_as_of("harbor-api", as_of)
        cli_dispositions[as_of] = sorted(c["disposition"] for c in payload["cells"])

    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hdrs = session_credentials(server).auth_headers()
        for as_of in forms:
            url = f"http://{host}:{port}/v1/kdiff?project=harbor-api&as_of={quote(as_of, safe='')}"
            req = Request(url, headers=hdrs)
            with urlopen(req, timeout=3) as resp:
                assert resp.status == 200
                body = resp.read()
            assert body, f"LIVE_API empty body for {as_of}"
            payload = json.loads(body.decode("utf-8"))
            assert "error" not in payload
            api_disp = sorted(c["disposition"] for c in payload["cells"])
            assert api_disp == cli_dispositions[as_of], as_of
    finally:
        server.shutdown()


def test_d178_live_api_malformed_as_of_returns_json(tmp_path: Path) -> None:
    vault = _kdiff_vault(tmp_path)
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hdrs = session_credentials(server).auth_headers()
        url = f"http://{host}:{port}/v1/kdiff?project=harbor-api&as_of=now"
        req = Request(url, headers=hdrs)
        with urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # Wall-clock is rejected as malformed snapshot, not a connection reset.
        assert body["status"] == "rejected_malformed" or "error" in body
    finally:
        server.shutdown()
