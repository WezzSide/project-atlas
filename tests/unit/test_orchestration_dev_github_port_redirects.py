"""HARDEN-DEVLOOP-001: the API token never follows a redirect off the API origin.

Offline only: in-process HTTP servers bound to 127.0.0.1 on ephemeral ports, or a
stubbed opener. No network access, no fixed sleeps.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from project_atlas.orchestration.autonomy import dev_github_port
from project_atlas.orchestration.autonomy.dev_fabric_adapter import AdapterError
from project_atlas.orchestration.autonomy.dev_github_port import GitHubRestPort

TOKEN = "ghs_SECRETTOKENBYTES0123456789"
QUERY_SECRET = "QSECRET-sig-987654"
REDIRECTS = (301, 302, 303, 307, 308)


class Server:
    """A tiny local HTTP server: path -> (status, headers, body); records every request."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.seen: list[tuple[str, str | None]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.seen.append((self.path, self.headers.get("Authorization")))
                status, headers, body = server.routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.do_GET()

            def log_message(self, *a: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Server, Server]]:
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("NO_PROXY", "*")
    api, other = Server(), Server()
    monkeypatch.setattr(dev_github_port, "API", api.origin)
    try:
        yield api, other
    finally:
        api.close()
        other.close()


def _json(d: object) -> tuple[int, dict[str, str], bytes]:
    return 200, {"Content-Type": "application/json"}, json.dumps(d).encode()


def _port() -> GitHubRestPort:
    return GitHubRestPort("o/r", TOKEN)


@pytest.mark.parametrize("code", REDIRECTS)
def test_cross_origin_redirect_is_refused_and_token_never_leaves(servers, code):
    api, other = servers
    loc = f"{other.origin}/steal?sig={QUERY_SECRET}&t={TOKEN}"
    api.routes["/repos/o/r/actions/runs/1"] = (code, {"Location": loc}, b"")
    other.routes["/steal?sig=" + QUERY_SECRET + "&t=" + TOKEN] = _json({"stolen": True})

    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/actions/runs/1")

    assert all(auth is None for _, auth in other.seen)
    assert other.seen == []
    assert api.seen == [("/repos/o/r/actions/runs/1", f"Bearer {TOKEN}")]
    assert str(ei.value) == "github GET /actions/runs/1 refused cross-origin redirect"


def test_cross_origin_refusal_is_redacted_and_unchained(servers):
    api, other = servers
    loc = f"{other.origin}/x?sig={QUERY_SECRET}&token={TOKEN}"
    api.routes["/repos/o/r/actions/runs/2"] = (302, {"Location": loc}, b"")

    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/actions/runs/2")

    exc = ei.value
    assert exc.__cause__ is None and exc.__suppress_context__
    texts = [str(exc), repr(exc), str(exc.__context__ or ""), repr(exc.__context__ or "")]
    for text in texts:
        for secret in (TOKEN, QUERY_SECRET, loc, other.origin):
            assert secret not in text
    # the public, guarded path surfaces the same refusal
    with pytest.raises(AdapterError, match="refused cross-origin redirect"):
        _port().get_run(2)
    assert other.seen == []


def test_cross_origin_after_same_origin_hop_is_refused(servers):
    api, other = servers
    api.routes["/repos/o/r/a"] = (302, {"Location": "/repos/o/r/b"}, b"")
    api.routes["/repos/o/r/b"] = (307, {"Location": f"{other.origin}/c"}, b"")
    with pytest.raises(AdapterError):
        _port()._request("GET", "/a")
    assert other.seen == []


@pytest.mark.parametrize("code", REDIRECTS)
def test_same_origin_redirect_still_followed(servers, code):
    api, _ = servers
    api.routes["/repos/o/r/actions/runs/3"] = (code, {"Location": "/repos/o/r/moved"}, b"")
    api.routes["/repos/o/r/moved"] = _json({"ok": 1})
    assert _port()._request("GET", "/actions/runs/3") == (200, {"ok": 1})
    api.routes["/repos/o/r/abs"] = (code, {"Location": f"{api.origin}/repos/o/r/moved"}, b"")
    assert _port()._request("GET", "/abs") == (200, {"ok": 1})
    assert all(auth == f"Bearer {TOKEN}" for _, auth in api.seen)


def test_404_and_http_errors_keep_base_mapping(servers):
    api, _ = servers
    assert _port()._request("GET", "/missing") == (404, None)
    api.routes["/repos/o/r/boom"] = (500, {}, b"")
    with pytest.raises(AdapterError, match=r"^github GET /boom -> 500$"):
        _port()._request("GET", "/boom")


@pytest.mark.parametrize(
    ("exc", "text"),
    [
        (urllib.error.URLError("down"), "<urlopen error down>"),
        (TimeoutError("timed out"), "timed out"),
    ],
)
def test_urlerror_and_timeout_keep_base_format(monkeypatch, exc, text):
    def fail(self, *a, **k):
        raise exc

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fail)
    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/x")
    assert str(ei.value) == f"github GET /x unreachable: {text}"
    assert ei.value.__cause__ is exc


def test_artifact_download_redirect_model_unchanged(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("m.json", json.dumps({"verdict": "ok"}))
    blob = buf.getvalue()
    loc = "https://pipelines.blob.core.windows.net/a.zip?sig=x"
    seen: dict[str, object] = {}

    class Opener:
        def open(self, req, timeout=None):
            seen["api_auth"] = req.get_header("Authorization")
            hdrs = Message()
            hdrs["Location"] = loc
            raise urllib.error.HTTPError(req.full_url, 302, "Found", hdrs, None)

    def fake_build_opener(*handlers):
        seen["handlers"] = handlers
        return Opener()

    def fake_urlopen(url, timeout=None):
        seen["blob_url"] = url
        return io.BytesIO(blob)

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    class Port(GitHubRestPort):
        def _request(self, method, path, body=None):
            return 200, {"artifacts": [{"id": 7, "name": "n"}]}

    assert Port("o/r", TOKEN).read_json_artifact(1, "n", "m.json") == {"verdict": "ok"}
    assert seen["handlers"] == (dev_github_port._NoRedirect,)
    assert seen["api_auth"] == f"Bearer {TOKEN}"
    assert seen["blob_url"] == loc  # a bare URL string: no Authorization header attached


# --- RESOLVE:P1-1 / P1-2: nothing reachable from the AdapterError holds a Location -----------


def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    todo: list[BaseException | None] = [exc]
    while todo:
        e = todo.pop()
        if e is None or any(e is s for s in out):
            continue
        out.append(e)
        todo += [e.__cause__, e.__context__]
    return out


def _assert_detached(exc: AdapterError, message: str, *secrets: str) -> None:
    assert str(exc) == message
    assert exc.__cause__ is None and exc.__context__ is None
    for e in _chain(exc):
        attrs = [str(e), repr(e), repr(vars(e))]
        attrs += [str(getattr(e, a, "")) for a in ("headers", "url", "filename", "fp", "reason")]
        for text in attrs:
            for secret in (TOKEN, QUERY_SECRET, *secrets):
                assert secret not in text


@pytest.mark.parametrize(
    ("loc", "message"),
    [
        # urllib lets ftp reach the handler, which refuses the other origin
        (f"ftp://evil.invalid/x?sig={QUERY_SECRET}", "github GET /s refused cross-origin redirect"),
        # urllib itself rejects file: with an HTTPError naming the Location
        (f"file:///etc/passwd?sig={QUERY_SECRET}", "github GET /s -> 302"),
    ],
)
def test_non_http_location_scheme_is_refused_detached(servers, loc, message):
    api, _ = servers
    api.routes["/repos/o/r/s"] = (302, {"Location": loc}, b"")
    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/s")
    _assert_detached(ei.value, message, loc)


@pytest.mark.parametrize(
    "loc",
    [f"http://[evil.invalid/x?sig={QUERY_SECRET}", f"http://[::1x]/x?sig={QUERY_SECRET}"],
)
def test_malformed_bracketed_location_is_refused_detached(servers, loc):
    api, _ = servers
    api.routes["/repos/o/r/m"] = (302, {"Location": loc}, b"")
    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/m")
    _assert_detached(ei.value, "github GET /m rejected invalid URL", loc)


def test_same_origin_redirect_loop_limit_is_detached(servers):
    api, _ = servers
    loop = f"/repos/o/r/loop?sig={QUERY_SECRET}"
    api.routes["/repos/o/r/loop"] = (302, {"Location": loop}, b"")
    api.routes[loop] = (302, {"Location": loop}, b"")
    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/loop")
    _assert_detached(ei.value, "github GET /loop -> 302", loop)


@pytest.mark.parametrize("code", (307, 308))
def test_post_307_308_refusal_is_detached(servers, code):
    api, other = servers
    loc = f"/repos/o/r/moved?sig={QUERY_SECRET}"
    api.routes["/repos/o/r/p"] = (code, {"Location": loc}, b"")
    with pytest.raises(AdapterError) as ei:
        _port()._request("POST", "/p", {"a": 1})
    _assert_detached(ei.value, f"github POST /p -> {code}", loc)
    assert other.seen == []


@pytest.mark.parametrize("final", (403, 500))
def test_same_origin_redirect_then_error_is_detached(servers, final):
    api, _ = servers
    loc = f"/repos/o/r/next?sig={QUERY_SECRET}"
    api.routes["/repos/o/r/first"] = (302, {"Location": loc}, b"")
    api.routes[loc] = (final, {}, b"")
    with pytest.raises(AdapterError) as ei:
        _port()._request("GET", "/first")
    _assert_detached(ei.value, f"github GET /first -> {final}", loc)


def test_same_origin_redirect_then_404_still_maps(servers):
    api, _ = servers
    api.routes["/repos/o/r/gone"] = (302, {"Location": "/repos/o/r/nowhere"}, b"")
    assert _port()._request("GET", "/gone") == (404, None)


def test_plain_http_error_keeps_chained_cause(servers):
    api, _ = servers
    api.routes["/repos/o/r/err"] = (502, {}, b"")
    with pytest.raises(AdapterError, match=r"^github GET /err -> 502$") as ei:
        _port()._request("GET", "/err")
    assert isinstance(ei.value.__cause__, urllib.error.HTTPError)


def test_json_errors_unchanged(servers):
    api, _ = servers
    api.routes["/repos/o/r/bad"] = (200, {}, b"not json")
    with pytest.raises(json.JSONDecodeError):
        _port()._request("GET", "/bad")
