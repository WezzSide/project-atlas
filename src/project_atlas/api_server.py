"""AS-2.1-API-SERVER-001 - stdlib LIVE_API read server (+ bounded actions).

Serves AppService JSON over HTTP. Default bind 127.0.0.1 only.
GET is read-only. POST /v1/actions records reconstructable web actions only
(never Layer B). POST /v1/captures/conversation writes quarantined
conversation evidence (CAPTURE != TRUTH CORE). Requires authz api.read /
web.action as applicable.
Hardened: localhost CORS, Host gate, POST size cap, per-connection read
timeout (slowloris, D-INTEGRATE-007A §11), obs/authz/mcp routes.

SEC-009: loopback / Host binding is defense-in-depth only. Every GET/POST
requires a high-entropy per-launch Bearer credential bound to an explicit
request principal (OperatorProfile).
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from project_atlas.app_service import AppService, AppServiceError, open_app_service
from project_atlas.ask_atlas_live import AskAtlasLiveError, ask_atlas_live
from project_atlas.authz import (
    ApiAuthError,
    ApiSessionCredentials,
    ApiSessionStore,
    OperatorProfile,
    default_operator,
    mint_api_session,
)
from project_atlas.compat_anchor import require_compatibility_anchor
from project_atlas.conversation_capture import (
    ConversationCaptureError,
    capture_conversation,
)
from project_atlas.mcp_server import list_mcp_tools
from project_atlas.obs_live import build_live_observability_receipt
from project_atlas.ops_receipts import inventory_ops_receipts
from project_atlas.web_actions import (
    WebActionError,
    list_recent_actions,
    load_action_ledger,
    submit_web_action,
)
from project_atlas.web_mission_workspace import build_mission_view, build_workspace_view

PACKAGE_ID = "AS-2.1-API-SERVER-001"
TRUTH_BOUNDARY = (
    "LIVE_API READ + BOUNDED ACTIONS != AUTHORITY / != LAYER-B WRITE"
)
MAX_POST_BYTES = 64_000
# D-INTEGRATE-007A §11 (SLOWLORIS): stdlib BaseHTTPRequestHandler.timeout is
# None by default, and Bearer auth is only enforced AFTER the request line and
# headers are fully parsed. A client that opens a socket and dribbles a partial
# request (no header terminator) would otherwise hold a worker thread forever.
# A bounded per-connection read timeout caps how long any thread can block on a
# slow/partial request; on timeout stdlib closes the connection (availability
# defense-in-depth only, loopback + Bearer + existing guards stay intact).
READ_TIMEOUT_SECONDS = 10.0
# Default Vite port; productization launcher overrides via ATLAS_CORS_ORIGIN
# so /v1/meta + Access-Control-Allow-Origin match the session -WebPort
# (PROD-ADV-011: CORS must not stay pinned at 5173 when WebPort differs).
DEFAULT_CORS_ORIGIN = "http://127.0.0.1:5173"
CORS_ORIGIN = DEFAULT_CORS_ORIGIN
_LOCAL_HOST_RE = re.compile(
    r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
    re.IGNORECASE,
)
_CORS_ORIGIN_RE = re.compile(
    r"^http://(127\.0\.0\.1|localhost|\[::1\]):(\d{1,5})$",
    re.IGNORECASE,
)


class ApiServerError(ValueError):
    """Fail-closed API server error."""


def resolve_cors_origin(raw: str | None = None) -> str:
    """Resolve loopback CORS origin for this LIVE_API launch.

    Reads ``ATLAS_CORS_ORIGIN`` when ``raw`` is omitted. Empty/unset → default
    ``http://127.0.0.1:5173``. Non-loopback or malformed values fail closed.
    """
    value = (
        raw if raw is not None else os.environ.get("ATLAS_CORS_ORIGIN", "")
    ).strip()
    if not value:
        return DEFAULT_CORS_ORIGIN
    match = _CORS_ORIGIN_RE.fullmatch(value)
    if match is None:
        raise ApiServerError("cors-origin-non-local-forbidden")
    port = int(match.group(2))
    if port < 1 or port > 65535:
        raise ApiServerError("cors-origin-non-local-forbidden")
    host = match.group(1)
    # Normalize to lowercase host form for stable meta + ACAO headers.
    if host.lower() == "localhost":
        return f"http://localhost:{port}"
    if host.lower() == "[::1]":
        return f"http://[::1]:{port}"
    return f"http://127.0.0.1:{port}"


class AtlasApiServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the per-launch API session store."""

    # D-044 B5: never silently share an occupied port with another Atlas vault.
    allow_reuse_address = False
    atlas_session: ApiSessionStore
    atlas_vault_id: str | None = None
    atlas_bind_path: Path | None = None

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        bind_and_activate: bool = True,
    ) -> None:
        # D-041 HIGH: the stdlib default ``address_family`` is AF_INET, which
        # can never bind an IPv6 literal. ``serve_api`` already restricts
        # ``host`` to exactly "127.0.0.1" / "localhost" / "::1" before this
        # constructor runs, so an exact "::1" match is sufficient here -- no
        # general-purpose IPv6 parsing is needed or wanted.
        if server_address[0] == "::1":
            import socket

            self.address_family = socket.AF_INET6
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _parse_limit(qs: dict[str, list[str]], *, default: int = 100) -> int:
    raw = (qs.get("limit") or [str(default)])[0]
    try:
        value = int(raw)
    except ValueError as exc:
        raise ApiServerError("api-limit-invalid") from exc
    if value < 1 or value > 500:
        raise ApiServerError("api-limit-out-of-range")
    return value


def session_credentials(server: ThreadingHTTPServer) -> ApiSessionCredentials:
    """Return the per-launch credentials attached to a LIVE_API server."""
    store = getattr(server, "atlas_session", None)
    if not isinstance(store, ApiSessionStore):
        raise ApiServerError("api-session-missing")
    return store.credentials


def make_handler(
    service: AppService,
    session: ApiSessionStore,
    *,
    cors_origin: str | None = None,
    read_timeout: float = READ_TIMEOUT_SECONDS,
) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to a vault AppService + session store.

    ``read_timeout`` (D-INTEGRATE-007A §11) is the per-connection socket read
    timeout in seconds applied while stdlib parses the request line/headers,
    before Bearer auth runs. It bounds slowloris-style partial-request holds so
    a slow client cannot pin a worker thread indefinitely.
    """
    allowed_origin = resolve_cors_origin(cors_origin)

    class Handler(BaseHTTPRequestHandler):
        # Socket read timeout: stdlib StreamRequestHandler.setup() applies this
        # to the connection, and handle_one_request() catches the resulting
        # TimeoutError, closing the connection instead of blocking forever.
        timeout = read_timeout

        def log_message(self, format: str, *args: object) -> None:
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization",
            )
            self.send_header("Vary", "Origin")

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Atlas-Package", PACKAGE_ID)
            self.send_header("X-Atlas-Truth-Boundary", TRUTH_BOUNDARY)
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            if not host:
                return True
            return bool(_LOCAL_HOST_RE.fullmatch(host))

        def _authenticate(self) -> OperatorProfile | None:
            """Resolve request principal; send 401 and return None on failure."""
            try:
                return session.resolve_bearer(self.headers.get("Authorization"))
            except ApiAuthError as exc:
                self._send(401, {"error": str(exc), "package_id": PACKAGE_ID})
                return None

        def do_OPTIONS(self) -> None:
            # CORS preflight: Host gate only (browsers omit Authorization).
            if not self._host_ok():
                self._send(403, {"error": "host-non-local-forbidden", "package_id": PACKAGE_ID})
                return
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if not self._host_ok():
                self._send(403, {"error": "host-non-local-forbidden", "package_id": PACKAGE_ID})
                return
            operator = self._authenticate()
            if operator is None:
                return
            try:
                operator.require("api.read")
            except PermissionError as exc:
                self._send(403, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)
            if path == "/v1/ask":
                query = (qs.get("q") or qs.get("query") or [""])[0]
                try:
                    self._send(
                        200,
                        ask_atlas_live(service.vault, query=query, operator=operator),
                    )
                except (AskAtlasLiveError, PermissionError, ValueError) as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/projects":
                try:
                    limit = _parse_limit(qs)
                except ApiServerError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                    return
                projects = service.projects()[:limit]
                self._send(200, {"projects": projects, "limit": limit})
                return
            if path == "/v1/discovery":
                # D-049 Lane G — categorized estate discovery (read-only projection).
                self._send(200, service.estate_discovery())
                return
            if path == "/v1/knowledge":
                try:
                    limit = _parse_limit(qs)
                except ApiServerError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                    return
                project_filter = (qs.get("project") or [""])[0] or None
                try:
                    knowledge = service.knowledge(project_filter)[:limit]
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                    return
                self._send(
                    200,
                    {
                        "knowledge": knowledge,
                        "limit": limit,
                        "project": project_filter,
                    },
                )
                return
            if path == "/v1/next-status":
                try:
                    self._send(200, service.next_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/changed-status":
                try:
                    self._send(200, service.changed_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/overview-status":
                try:
                    self._send(200, service.overview_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/decisions-status":
                try:
                    self._send(200, service.decisions_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/unknown-status":
                try:
                    self._send(200, service.unknown_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/state-status":
                try:
                    self._send(200, service.state_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/architecture-status":
                try:
                    self._send(200, service.architecture_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/roadmap-status":
                try:
                    self._send(200, service.roadmap_answers_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/portfolio-status":
                try:
                    self._send(200, service.portfolio_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/bitemporal-status":
                try:
                    self._send(200, service.bitemporal_view())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/index-status":
                try:
                    self._send(200, service.index_status())
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/brief":
                project = (qs.get("project") or [""])[0]
                if not project:
                    self._send(
                        400,
                        {
                            "error": "brief-requires-project",
                            "package_id": PACKAGE_ID,
                        },
                    )
                    return
                try:
                    self._send(200, service.brief(project))
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/roadmap":
                project = (qs.get("project") or [""])[0]
                if not project:
                    self._send(
                        400,
                        {
                            "error": "roadmap-requires-project",
                            "package_id": PACKAGE_ID,
                        },
                    )
                    return
                try:
                    self._send(200, service.roadmap(project))
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/source-health":
                project = (qs.get("project") or qs.get("project_id") or [""])[0]
                if not project:
                    self._send(
                        400,
                        {
                            "error": "source-health-requires-project",
                            "package_id": "AS-CODER-ALPHA-SOURCE-HEALTH-API-001",
                            "honesty": "UNSUPPORTED_SCOPE",
                        },
                    )
                    return
                try:
                    self._send(200, service.source_health(project))
                except AppServiceError as exc:
                    honesty = getattr(exc, "honesty", None) or "MALFORMED_INPUT"
                    self._send(
                        400,
                        {
                            "error": str(exc),
                            "package_id": "AS-CODER-ALPHA-SOURCE-HEALTH-API-001",
                            "honesty": honesty,
                        },
                    )
                return
            if path == "/v1/architecture":
                project = (qs.get("project") or qs.get("project_id") or [""])[0]
                if not project:
                    self._send(
                        400,
                        {
                            "error": "architecture-requires-project",
                            "package_id": "AS-CODER-ALPHA-ARCHITECTURE-SURFACE-001",
                            "honesty": "UNSUPPORTED_SCOPE",
                        },
                    )
                    return
                try:
                    self._send(200, service.architecture(project))
                except AppServiceError as exc:
                    honesty = getattr(exc, "honesty", None) or "MALFORMED_INPUT"
                    self._send(
                        400,
                        {
                            "error": str(exc),
                            "package_id": "AS-CODER-ALPHA-ARCHITECTURE-SURFACE-001",
                            "honesty": honesty,
                        },
                    )
                return
            if path == "/v1/actions/recent":
                try:
                    limit = _parse_limit(qs, default=20)
                except ApiServerError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                    return
                self._send(200, list_recent_actions(service.vault, limit=limit))
                return
            if path == "/v1/ops/receipts":
                try:
                    limit = _parse_limit(qs, default=100)
                except ApiServerError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                    return
                try:
                    self._send(
                        200,
                        inventory_ops_receipts(service.vault, limit=limit),
                    )
                except ValueError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path in {
                "/v1/intelligence/evidence",
                "/v1/intelligence/conflicts",
                "/v1/intelligence/explain",
                "/v1/intelligence/query",
                "/v1/project-state",
                "/v1/project-attention",
            }:
                project = (qs.get("project") or qs.get("project_id") or [""])[0]
                as_of = (qs.get("as_of_valid_time") or qs.get("as_of") or [""])[0]
                subject = (qs.get("subject") or [""])[0] or None
                field = (qs.get("field") or [""])[0] or None
                claim_id = (qs.get("claim_id") or [""])[0] or None
                kind = (qs.get("kind") or [""])[0]
                if not project:
                    self._send(
                        400,
                        {
                            "error": "intel-api-project-id-required",
                            "package_id": "AS-2.0-API-001",
                            "honesty": "MALFORMED_INPUT",
                        },
                    )
                    return
                try:
                    if path == "/v1/intelligence/evidence":
                        payload = service.intelligence_evidence(
                            project,
                            subject=subject,
                            field=field,
                            claim_id=claim_id,
                            as_of_valid_time=as_of or None,
                        )
                    elif path == "/v1/intelligence/conflicts":
                        payload = service.intelligence_conflicts(
                            project, as_of_valid_time=as_of or None
                        )
                    elif path == "/v1/intelligence/explain":
                        payload = service.intelligence_explain(
                            project,
                            subject=subject,
                            field=field,
                            claim_id=claim_id,
                            as_of_valid_time=as_of or None,
                        )
                    elif path == "/v1/intelligence/query":
                        payload = service.intelligence_query(
                            project,
                            kind,
                            subject=subject,
                            field=field,
                            claim_id=claim_id,
                            as_of_valid_time=as_of or None,
                        )
                    elif path == "/v1/project-state":
                        payload = service.project_state(
                            project, as_of_valid_time=as_of or None
                        )
                    else:
                        payload = service.project_attention(
                            project, as_of_valid_time=as_of or None
                        )
                    self._send(200, payload)
                except AppServiceError as exc:
                    honesty = getattr(exc, "honesty", None) or "MALFORMED_INPUT"
                    self._send(
                        400,
                        {
                            "error": str(exc),
                            "package_id": "AS-2.0-API-001",
                            "honesty": honesty,
                        },
                    )
                except OSError as exc:
                    self._send(
                        400,
                        {
                            "error": f"intel-api-filesystem-unreadable:{exc.__class__.__name__}",
                            "package_id": "AS-2.0-API-001",
                            "honesty": "MALFORMED_INPUT",
                        },
                    )
                return
            if path == "/v1/portfolio-state":
                project_ids = tuple(
                    item
                    for item in (qs.get("project") or qs.get("project_id") or [])
                    if item.strip()
                )
                as_of = (qs.get("as_of_valid_time") or qs.get("as_of") or [""])[0]
                try:
                    self._send(
                        200,
                        service.portfolio_state(
                            project_ids, as_of_valid_time=as_of or None
                        ),
                    )
                except AppServiceError as exc:
                    honesty = getattr(exc, "honesty", None) or "MALFORMED_INPUT"
                    self._send(
                        400,
                        {
                            "error": str(exc),
                            "package_id": "AS-2.0-API-001",
                            "honesty": honesty,
                        },
                    )
                except OSError as exc:
                    self._send(
                        400,
                        {
                            "error": f"intel-api-filesystem-unreadable:{exc.__class__.__name__}",
                            "package_id": "AS-2.0-API-001",
                            "honesty": "MALFORMED_INPUT",
                        },
                    )
                return
            if path == "/v1/conflicts":
                project = (qs.get("project") or [""])[0]
                try:
                    self._send(200, service.conflicts(project))
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            if path == "/v1/kdiff":
                project = (qs.get("project") or [""])[0]
                as_of = (qs.get("as_of") or qs.get("as-of") or [""])[0]
                t1 = (qs.get("from") or [""])[0]
                t2 = (qs.get("to") or [""])[0]
                try:
                    if as_of:
                        self._send(200, service.kdiff_as_of(project, as_of))
                    elif t1 and t2:
                        self._send(200, service.kdiff_diff(project, t1, t2))
                    else:
                        self._send(
                            400,
                            {
                                "error": "kdiff-requires-as_of-or-from-and-to",
                                "package_id": PACKAGE_ID,
                            },
                        )
                except AppServiceError as exc:
                    self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            routes: dict[str, Any] = {
                "/health": lambda: service.health(),
                "/v1/health": lambda: service.health(),
                "/v1/graph": lambda: service.graph_summary(),
                "/v1/snapshot": lambda: service.snapshot(),
                "/v1/actions": lambda: load_action_ledger(service.vault),
                "/v1/mcp/tools": lambda: list_mcp_tools(operator=operator),
                "/v1/obs": lambda: build_live_observability_receipt(
                    service.vault, receipt_id="api-obs", persist=False
                ),
                "/v1/mission": lambda: build_mission_view(service.vault),
                "/v1/workspace": lambda: build_workspace_view(service.vault),
                "/v1/authz": lambda: {
                    "package_id": "AS-2.1-AUTHZ-001",
                    "operator_id": operator.operator_id,
                    "capabilities": sorted(operator.capabilities),
                    "authority": False,
                    "write_enabled": False,
                },
                "/v1/meta": lambda: {
                    "package_id": PACKAGE_ID,
                    "truth_boundary": TRUTH_BOUNDARY,
                    "write_enabled": False,
                    "actions_enabled": True,
                    "live_api": True,
                    "ask_atlas_live": True,
                    "obs_live": True,
                    "ops_receipts": True,
                    "mission_live": True,
                    "workspace_live": True,
                    "conflicts_live": True,
                    "intelligence_live": True,
                    "kdiff_live": True,
                    "brief_live": True,
                    "source_health_live": True,
                    "architecture_live": True,
                    "discovery_live": True,
                    "truth_ux_live": True,
                    "authz_profile": True,
                    "session_auth": True,
                    "max_post_bytes": MAX_POST_BYTES,
                    "cors_origin": allowed_origin,
                    "operator_id": operator.operator_id,
                },
            }
            if path not in routes:
                self._send(404, {"error": "not-found", "path": path})
                return
            try:
                self._send(200, routes[path]())
            except PermissionError as exc:
                self._send(403, {"error": str(exc), "package_id": PACKAGE_ID})

        def do_POST(self) -> None:
            if not self._host_ok():
                self._send(403, {"error": "host-non-local-forbidden", "package_id": PACKAGE_ID})
                return
            operator = self._authenticate()
            if operator is None:
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path not in {"/v1/actions", "/v1/captures/conversation"}:
                self._send(
                    405,
                    {
                        "error": "writes-forbidden",
                        "package_id": PACKAGE_ID,
                        "truth_boundary": TRUTH_BOUNDARY,
                    },
                )
                return
            raw_length = self.headers.get("Content-Length", "0") or "0"
            try:
                length = int(raw_length)
            except ValueError:
                self._send(
                    400,
                    {
                        "error": "content-length-invalid",
                        "package_id": PACKAGE_ID,
                    },
                )
                return
            if length < 0 or length > MAX_POST_BYTES:
                # Close without reading body (DoS-safe). Clients — especially on
                # Windows — may see connection abort instead of a full 413 body.
                self.close_connection = True
                self._send(
                    413,
                    {
                        "error": "payload-too-large",
                        "max_post_bytes": MAX_POST_BYTES,
                        "package_id": PACKAGE_ID,
                    },
                )
                return
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                # Avoid leaking parser internals / filesystem paths (ADV path leakage).
                self._send(
                    400,
                    {"error": "json-invalid", "package_id": PACKAGE_ID},
                )
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "body-not-object"})
                return
            if path == "/v1/captures/conversation":
                try:
                    operator.require("web.action")
                    envelope = (
                        body["envelope"]
                        if isinstance(body.get("envelope"), dict)
                        else body
                    )
                    receipt = capture_conversation(service.vault, envelope)
                except PermissionError as exc:
                    self._send(403, {"error": str(exc), "package_id": PACKAGE_ID})
                    return
                except ConversationCaptureError as exc:
                    self._send(
                        400,
                        {
                            "status": "error",
                            "error": exc.code,
                            "message": str(exc),
                            "package_id": PACKAGE_ID,
                            "truth_boundary": TRUTH_BOUNDARY,
                        },
                    )
                    return
                self._send(200, receipt)
                return
            try:
                txn = submit_web_action(
                    service.vault,
                    action_id=str(body.get("action_id", "")),
                    action_type=body.get("action_type"),  # type: ignore[arg-type]
                    payload=body.get("payload")
                    if isinstance(body.get("payload"), dict)
                    else {},
                    operator=operator,
                )
            except (WebActionError, PermissionError, TypeError, ValueError) as exc:
                self._send(400, {"error": str(exc), "package_id": PACKAGE_ID})
                return
            self._send(200, {"accepted": True, "transaction": txn})

        def do_PUT(self) -> None:
            if not self._host_ok():
                self._send(403, {"error": "host-non-local-forbidden", "package_id": PACKAGE_ID})
                return
            if self._authenticate() is None:
                return
            self._send(405, {"error": "writes-forbidden", "package_id": PACKAGE_ID})

        def do_DELETE(self) -> None:
            if not self._host_ok():
                self._send(403, {"error": "host-non-local-forbidden", "package_id": PACKAGE_ID})
                return
            if self._authenticate() is None:
                return
            self._send(405, {"error": "writes-forbidden", "package_id": PACKAGE_ID})

        def do_PATCH(self) -> None:
            if not self._host_ok():
                self._send(403, {"error": "host-non-local-forbidden", "package_id": PACKAGE_ID})
                return
            if self._authenticate() is None:
                return
            self._send(405, {"error": "writes-forbidden", "package_id": PACKAGE_ID})

    return Handler


def _live_api_bind_path(vault: Path) -> Path:
    return vault.expanduser().resolve() / "generated" / "ops" / "live-api-bind.json"


def _loopback_probe_targets(host: str) -> list[tuple[int, str]]:
    """Return ``(address_family, connect_host)`` targets for dual-stack probes."""
    import socket

    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        # Localhost may resolve to either family; probe both loopbacks so an
        # existing ::1 listener cannot be dual-bound via 127.0.0.1 (D-044 B5).
        return [
            (socket.AF_INET, "127.0.0.1"),
            (socket.AF_INET6, "::1"),
        ]
    if ":" in host:
        return [(socket.AF_INET6, host)]
    return [(socket.AF_INET, host)]


def _refuse_stale_or_foreign_bind(
    vault: Path, *, host: str, port: int, vault_id: str
) -> None:
    """Fail closed when another LIVE_API appears to own the endpoint (D-044 B5)."""
    import socket

    if int(port) == 0:
        # Ephemeral test binds — OS assigns a free port; no dual-bind risk.
        return
    path = _live_api_bind_path(vault)
    in_use = False
    for family, connect_host in _loopback_probe_targets(host):
        probe = socket.socket(family, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.35)
            # AF_INET6 requires (host, port, flowinfo, scopeid); AF_INET is 2-tuple.
            address: tuple[Any, ...]
            if family == socket.AF_INET6:
                address = (connect_host, int(port), 0, 0)
            else:
                address = (connect_host, int(port))
            err = probe.connect_ex(address)
        except (OSError, TypeError):
            err = 1
        finally:
            probe.close()
        if err == 0:
            in_use = True
            break
    if not in_use:
        return
    # Port already accepting connections — refuse dual/foreign bind.
    if not path.is_file():
        raise ApiServerError(f"api-bind-port-in-use:{host}:{port}:no-bind-receipt")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiServerError(
            f"api-bind-port-in-use:{host}:{port}:unreadable-bind"
        ) from exc
    if not isinstance(payload, dict):
        raise ApiServerError(f"api-bind-port-in-use:{host}:{port}:invalid-bind")
    if str(payload.get("vault_id") or "") != vault_id:
        raise ApiServerError(
            f"api-bind-foreign-vault:{host}:{port}:owner={payload.get('vault_id')!r}"
        )
    raise ApiServerError(f"api-bind-already-serving:{host}:{port}:{vault_id}")


def _write_live_api_bind(
    vault: Path, *, host: str, port: int, vault_id: str, pid: int
) -> Path:
    path = _live_api_bind_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "schema": "atlas.live-api.bind.v1",
        "package": PACKAGE_ID,
        "host": host,
        "port": int(port),
        "vault_id": vault_id,
        "pid": int(pid),
        "generated": {"by": "project-atlas-api-server"},
        "honesty": {
            "allow_reuse_address": False,
            "dual_bind_forbidden": True,
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)
    return path


def serve_api(
    vault: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    operator: OperatorProfile | None = None,
    session: ApiSessionStore | None = None,
    cors_origin: str | None = None,
    read_timeout: float = READ_TIMEOUT_SECONDS,
) -> AtlasApiServer:
    """Create (but do not serve forever) a LIVE_API server instance.

    Mints (or accepts) a per-launch session store. Callers must present a
    Bearer credential from ``server.atlas_session.credentials`` on every
    GET/POST (SEC-009).

    ``cors_origin`` / ``ATLAS_CORS_ORIGIN`` must be a loopback http origin
    matching the session web port when the productization launcher starts
    Vite on a non-default ``-WebPort`` (PROD-ADV-011).

    D-044 B5: refuse occupied / foreign vault binds (no dual LIVE_API ambiguity).
    """
    import os

    require_compatibility_anchor()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ApiServerError("api-bind-non-local-forbidden")
    vault = vault.expanduser().resolve()
    svc = open_app_service(vault)
    vault_id = str(getattr(svc, "vault_id", None) or "")
    if not vault_id:
        # Best-effort identity for bind ownership.
        try:
            from project_atlas.vault_identity import read_vault_identity

            vault_id = read_vault_identity(vault).vault_id
        except Exception:
            vault_id = vault.as_posix()
    _refuse_stale_or_foreign_bind(vault, host=host, port=port, vault_id=vault_id)
    launch_op = operator or default_operator()
    launch_op.require("api.read")
    store = session or mint_api_session(launch_op)
    # Ensure the read principal can serve api.read.
    store.credentials.read_operator.require("api.read")
    handler = make_handler(
        svc, store, cors_origin=cors_origin, read_timeout=read_timeout
    )
    try:
        server = AtlasApiServer((host, port), handler)
    except OSError as exc:
        raise ApiServerError(f"api-bind-unavailable:{host}:{port}:{exc}") from exc
    server.atlas_session = store
    server.atlas_vault_id = vault_id
    server.atlas_bind_path = _write_live_api_bind(
        vault, host=host, port=port, vault_id=vault_id, pid=os.getpid()
    )
    return server
