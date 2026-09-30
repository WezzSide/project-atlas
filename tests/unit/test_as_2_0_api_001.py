"""AS-2.0-API-001 - read-only intelligence LIVE_API projections."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from project_atlas.api_server import serve_api, session_credentials
from project_atlas.web_api.intelligence import (
    CERTIFIED_QUERY_KINDS,
    WebIntelligenceError,
    read_intelligence_conflicts,
    read_intelligence_evidence,
    read_intelligence_explain,
    read_intelligence_query,
    read_portfolio_state,
    read_project_attention,
    read_project_state,
)

HASH_A = "a" * 64
API_SERVER = Path("src/project_atlas/api_server.py")


def _write_claims(vault: Path, project_id: str, claims: list[dict[str, object]]) -> None:
    root = vault / "state" / "claims"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{project_id}.json").write_text(
        json.dumps({"schema_version": 1, "project_id": project_id, "claims": claims}),
        encoding="utf-8",
    )


def _row(
    claim_id: str,
    value: str,
    source_id: str = "src-a",
    *,
    project_id: str = "harbor-api",
    lifecycle: str = "new",
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "claim_id": claim_id,
        "project_id": project_id,
        "subject": f"project:{project_id}",
        "field": "datastore",
        "value": value,
        "claim_type": "architecture-statement",
        "authority": "primary",
        "confidence": "high",
        "lifecycle": lifecycle,
        "provenance": [
            {
                "source_id": source_id,
                "resource": f"docs/{source_id}.md",
                "sha256": HASH_A,
            }
        ],
    }
    if valid_from:
        row["valid_from"] = valid_from
    if valid_to:
        row["valid_to"] = valid_to
    return row


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def test_evidence_and_state_are_not_authority(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [_row("claim-a", "PostgreSQL 16"), _row("claim-b", "PostgreSQL 16", "src-b")],
    )
    evidence = read_intelligence_evidence(vault, "harbor-api")
    state = read_project_state(vault, "harbor-api")
    assert evidence["derived_intelligence_is_authority"] == "NO"
    assert evidence["canonical_write"] is False
    assert evidence["numeric_confidence"] is None
    assert evidence["assessments"]
    assert evidence["supporting_evidence"]
    assert evidence["provenance"]
    assert state["authority_note"] == "derived-state-not-canonical"
    assert "healthy" not in json.dumps(state).lower()


def test_conflicts_do_not_replace_v1_conflicts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [_row("claim-a", "PostgreSQL 15"), _row("claim-b", "PostgreSQL 16", "src-b")],
    )
    payload = read_intelligence_conflicts(vault, "harbor-api")
    assert payload["candidates"]
    assert payload["replaces_v1_conflicts"] is False
    assert payload["authority_note"] == "candidate-not-resolution"
    assert payload["honesty"] == "CONTESTED"
    assert payload["contradiction_is_proven_falsehood"] == "NO"


def test_explain_attention_and_portfolio(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [_row("claim-a", "PostgreSQL 15"), _row("claim-b", "PostgreSQL 16", "src-b")],
    )
    explain = read_intelligence_explain(vault, "harbor-api", field="datastore")
    attention = read_project_attention(vault, "harbor-api")
    portfolio = read_portfolio_state(vault, ("harbor-api",))
    assert explain["explanation"]
    assert attention["authority_note"] == "risk-is-not-fact"
    assert attention["attention_rank_is_score"] == "NO"
    assert portfolio["numeric_priority_score"] is None
    assert portfolio["state"]["authority_note"] == "portfolio-not-authority"
    assert portfolio["scope"] == "portfolio"


def test_empty_project_is_unknown_not_healthy(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    state = read_project_state(vault, "harbor-api")
    dumped = json.dumps(state).lower()
    assert "healthy" not in dumped
    assert state["honesty"] == "NO_DATA"
    assert any(item["status"] == "unknown" for item in state["unknown_facts"])


def test_valid_empty_claims_file_is_not_no_data(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [])
    evidence = read_intelligence_evidence(vault, "harbor-api")
    assert evidence["honesty"] == "VALID_EMPTY"
    assert evidence["honesty"] != "NO_DATA"


def test_no_match_filter_is_distinct_from_no_data(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_row("claim-a", "PostgreSQL 16")])
    evidence = read_intelligence_evidence(vault, "harbor-api", claim_id="claim-missing")
    assert evidence["honesty"] == "NO_MATCH"
    assert evidence["assessments"] == []


def test_stale_is_not_invalid(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [_row("claim-a", "PostgreSQL 16", lifecycle="stale")],
    )
    state = read_project_state(vault, "harbor-api")
    assert state["honesty"] == "STALE"
    assert state["stale_facts"]
    dumped = json.dumps(state).lower()
    assert "stale-not-invalid" in dumped or "stale" in dumped
    assert "healthy" not in dumped


def test_contested_is_not_resolved(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [_row("claim-a", "PostgreSQL 15"), _row("claim-b", "PostgreSQL 16", "src-b")],
    )
    state = read_project_state(vault, "harbor-api")
    conflicts = read_intelligence_conflicts(vault, "harbor-api")
    assert state["honesty"] == "CONTESTED"
    assert conflicts["honesty"] == "CONTESTED"
    assert "resolved" not in json.dumps(conflicts).lower()


def test_cross_project_isolation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_row("claim-a", "PostgreSQL 16")])
    _write_claims(
        vault,
        "other-project",
        [
            _row(
                "claim-z",
                "secret-other-value",
                project_id="other-project",
                source_id="src-z",
            )
        ],
    )
    evidence = read_intelligence_evidence(vault, "harbor-api")
    dumped = json.dumps(evidence)
    assert "secret-other-value" not in dumped
    assert "other-project" not in dumped
    other = read_intelligence_evidence(vault, "other-project")
    assert "PostgreSQL 16" not in json.dumps(other)


def test_determinism_and_ordering(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [
            _row("claim-b", "PostgreSQL 16", "src-b"),
            _row("claim-a", "PostgreSQL 15"),
        ],
    )
    first = read_intelligence_conflicts(vault, "harbor-api")
    second = read_intelligence_conflicts(vault, "harbor-api")
    assert _digest(first) == _digest(second)
    ids = [item.get("candidate_id") or item.get("id") for item in first["candidates"]]
    assert ids == sorted(str(item) for item in ids)


def test_wall_clock_as_of_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(WebIntelligenceError, match="wall-clock") as exc:
        read_project_state(vault, "harbor-api", as_of_valid_time="now")
    assert exc.value.honesty.value == "MALFORMED_INPUT"


def test_path_traversal_project_id_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(WebIntelligenceError) as exc:
        read_intelligence_evidence(vault, "../etc")
    assert exc.value.honesty.value == "MALFORMED_INPUT"


def test_certified_query_kinds_do_not_duplicate_dedicated_routes(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_row("claim-a", "PostgreSQL 16")])
    for kind in sorted(CERTIFIED_QUERY_KINDS):
        payload = read_intelligence_query(vault, "harbor-api", kind)
        assert payload["kind"] == kind
        assert payload["canonical_write"] is False
        assert payload["decision_candidate_is_command"] == "NO"
    with pytest.raises(WebIntelligenceError) as exc:
        read_intelligence_query(vault, "harbor-api", "evidence")
    assert exc.value.honesty.value == "UNSUPPORTED_SCOPE"
    with pytest.raises(WebIntelligenceError) as exc:
        read_intelligence_query(vault, "harbor-api", "invented")
    assert exc.value.honesty.value == "UNSUPPORTED_SCOPE"


def test_no_writes_to_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_row("claim-a", "PostgreSQL 16")])
    before = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }
    read_intelligence_evidence(vault, "harbor-api")
    read_intelligence_conflicts(vault, "harbor-api")
    read_project_state(vault, "harbor-api")
    read_project_attention(vault, "harbor-api")
    read_portfolio_state(vault, ("harbor-api",))
    read_intelligence_query(vault, "harbor-api", "decision")
    after = {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_api_server_registers_get_only_intelligence_routes() -> None:
    text = API_SERVER.read_text(encoding="utf-8")
    for route in (
        "/v1/intelligence/evidence",
        "/v1/intelligence/conflicts",
        "/v1/intelligence/explain",
        "/v1/intelligence/query",
        "/v1/project-state",
        "/v1/project-attention",
        "/v1/portfolio-state",
    ):
        assert f'"{route}"' in text
    assert 'if path == "/v1/conflicts":' in text
    assert "replaces_v1_conflicts" not in text.split('if path == "/v1/conflicts":')[1][:400]
    assert 'path not in {"/v1/actions", "/v1/captures/conversation"}' in text
    assert 'require("api.write")' not in text
    assert "vault.write" not in text


def _http_json(
    host: str,
    port: int,
    headers: dict[str, str],
    path: str,
    method: str = "GET",
    body: bytes | None = None,
) -> tuple[int, dict[str, object]]:
    req = Request(
        f"http://{host}:{port}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(req, timeout=3) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload


def test_http_honesty_and_no_auth_expansion(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(
        vault,
        "harbor-api",
        [_row("claim-a", "PostgreSQL 15"), _row("claim-b", "PostgreSQL 16", "src-b")],
    )
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hdrs = session_credentials(server).auth_headers()
        status, contested = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/intelligence/conflicts?project=harbor-api",
        )
        assert status == 200
        assert contested["honesty"] == "CONTESTED"
        assert contested["canonical_write"] is False
        status, missing = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/project-state?project=missing-project",
        )
        assert status == 200
        assert missing["honesty"] == "NO_DATA"
        status, bad = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/project-state?project=harbor-api&as_of=now",
        )
        assert status == 400
        assert bad["honesty"] == "MALFORMED_INPUT"
        status, unsupported = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/intelligence/query?project=harbor-api&kind=evidence",
        )
        assert status == 400
        assert unsupported["honesty"] == "UNSUPPORTED_SCOPE"
        status, posted = _http_json(
            str(host),
            int(port),
            {**hdrs, "Content-Type": "application/json"},
            "/v1/intelligence/evidence?project=harbor-api",
            method="POST",
            body=b"{}",
        )
        assert status == 405
        assert posted["error"] == "writes-forbidden"
        status, decision = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/intelligence/query?project=harbor-api&kind=decision",
        )
        assert status == 200
        assert decision["decision_candidate_is_command"] == "NO"
        status, classic = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/conflicts?project=harbor-api",
        )
        assert status == 200
        assert "replaces_v1_conflicts" not in classic
    finally:
        server.shutdown()


def test_project_token_boundary_matches_conflicts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    valid_64 = "a" + ("x" * 63)
    _write_claims(vault, valid_64, [_row("claim-a", "ok", project_id=valid_64)])
    payload = read_project_state(vault, valid_64)
    assert payload["project_id"] == valid_64
    for token in (valid_64 + "y", "x" * 512, "z" * 8000):
        with pytest.raises(WebIntelligenceError) as exc:
            read_project_state(vault, token)
        assert exc.value.honesty.value == "MALFORMED_INPUT"


def test_http_enametoolong_and_portfolio_scope(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    project_a = "dark-factory-02ee94d0"
    project_b = "harbor-portal"
    _write_claims(vault, project_a, [_row("claim-a", "factory-only", project_id=project_a)])
    _write_claims(
        vault,
        project_b,
        [_row("claim-b", "SECRET-PORTAL-VALUE", project_id=project_b)],
    )
    server = serve_api(vault, host="127.0.0.1", port=0)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hdrs = session_credentials(server).auth_headers()
        before = {
            path.relative_to(vault).as_posix(): path.read_bytes()
            for path in vault.rglob("*")
            if path.is_file()
        }
        for token in ("a" + ("x" * 64), "y" * 512, "z" * 8000):
            status, body = _http_json(
                str(host),
                int(port),
                hdrs,
                f"/v1/project-state?project={token}",
            )
            assert status == 400
            assert body["honesty"] == "MALFORMED_INPUT"
            assert "Traceback" not in str(body)
        status, unscoped = _http_json(
            str(host),
            int(port),
            hdrs,
            "/v1/portfolio-state",
        )
        assert status == 400
        assert unscoped["honesty"] == "UNSUPPORTED_SCOPE"
        assert "SECRET-PORTAL-VALUE" not in json.dumps(unscoped)
        status, scoped_a = _http_json(
            str(host),
            int(port),
            hdrs,
            f"/v1/portfolio-state?project={project_a}",
        )
        assert status == 200
        dumped_a = json.dumps(scoped_a)
        assert "SECRET-PORTAL-VALUE" not in dumped_a
        assert project_b not in dumped_a
        status, scoped_b = _http_json(
            str(host),
            int(port),
            hdrs,
            f"/v1/portfolio-state?project={project_b}",
        )
        assert status == 200
        assert "SECRET-PORTAL-VALUE" in json.dumps(scoped_b)
        status, scoped_both = _http_json(
            str(host),
            int(port),
            hdrs,
            f"/v1/portfolio-state?project={project_a}&project={project_b}",
        )
        assert status == 200
        dumped_both = json.dumps(scoped_both)
        assert project_a in dumped_both
        assert "SECRET-PORTAL-VALUE" in dumped_both
        for path in (
            f"/v1/intelligence/evidence?project={project_a}",
            f"/v1/intelligence/conflicts?project={project_a}",
            f"/v1/intelligence/explain?project={project_a}&field=datastore",
            f"/v1/project-state?project={project_a}",
            f"/v1/project-attention?project={project_a}",
            f"/v1/intelligence/query?project={project_a}&kind=decision",
        ):
            status, payload = _http_json(str(host), int(port), hdrs, path)
            assert status == 200
            assert "SECRET-PORTAL-VALUE" not in json.dumps(payload)
        status, patched = _http_json(
            str(host),
            int(port),
            {**hdrs, "Content-Type": "application/json"},
            f"/v1/intelligence/evidence?project={project_a}",
            method="PATCH",
            body=b"{}",
        )
        assert status == 405
        assert patched["error"] == "writes-forbidden"
        status, _health = _http_json(str(host), int(port), hdrs, "/v1/health")
        assert status == 200
        after = {
            path.relative_to(vault).as_posix(): path.read_bytes()
            for path in vault.rglob("*")
            if path.is_file()
        }
        assert after == before
    finally:
        server.shutdown()
