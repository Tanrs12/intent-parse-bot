"""The app's own pages may POST to the bridge on app port + 1; other origins get no CORS grant."""
from __future__ import annotations

import http.client
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import intent_live_draft_bridge as bridge  # noqa: E402


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def preflight(httpd, origin: str) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    conn.request("OPTIONS", "/api/control", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "content-type"})
    return conn.getresponse()


def test_preflight_from_the_app_page_is_granted(server, monkeypatch):
    app_port = server.server_address[1] - 1
    monkeypatch.setattr(bridge, "COACH_STATE_URL", f"http://127.0.0.1:{app_port}/api/state")
    for origin in (f"http://127.0.0.1:{app_port}", f"http://localhost:{app_port}"):
        response = preflight(server, origin)
        assert response.status == 204
        assert response.getheader("Access-Control-Allow-Origin") == origin
        assert "POST" in response.getheader("Access-Control-Allow-Methods")
        assert "Content-Type" in response.getheader("Access-Control-Allow-Headers")


@pytest.mark.parametrize("origin", ["https://example.com", "http://127.0.0.1:1", "null", ""])
def test_preflight_from_any_other_origin_gets_no_grant(server, origin):
    response = preflight(server, origin)
    assert response.status == 204
    assert response.getheader("Access-Control-Allow-Origin") is None


def post(httpd, origin, content_type="text/plain"):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    conn.request("POST", "/api/control", body=b'{"my_role": "Top"}', headers=headers)
    return conn.getresponse()


@pytest.mark.parametrize("origin", ["https://example.com", "null", "http://127.0.0.1:1"])
def test_a_simple_post_from_another_site_cannot_write(server, monkeypatch, origin):
    """A text/plain POST skips the browser preflight entirely: the bridge itself must refuse it."""
    calls = []
    monkeypatch.setattr(bridge, "_set_wanted_role", lambda role: calls.append(role) or role)
    response = post(server, origin)
    assert response.status == 403 and calls == []


def test_the_app_page_and_local_tools_can_still_write(server, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "_set_wanted_role", lambda role: calls.append(role) or role)
    monkeypatch.setattr(bridge, "COACH_STATE_URL", f"http://127.0.0.1:{server.server_address[1] - 1}/api/state")
    assert post(server, f"http://127.0.0.1:{server.server_address[1] - 1}", "application/json").status == 200
    assert post(server, None, "application/json").status == 200        # run.py's server-side relay, party tools
    assert calls == ["Top", "Top"]


def test_allowed_origin_follows_the_configured_app_url(server, monkeypatch):
    """The bridge may sit on any port: the app's own URL, not "bridge port - 1", names the trusted page."""
    monkeypatch.setattr(bridge, "COACH_STATE_URL", "http://127.0.0.1:9999/api/state")
    assert preflight(server, "http://127.0.0.1:9999").getheader("Access-Control-Allow-Origin") == "http://127.0.0.1:9999"
    assert preflight(server, f"http://127.0.0.1:{server.server_address[1] - 1}").getheader("Access-Control-Allow-Origin") is None
