import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hidee.app import create_app
from hidee.hook_api import APP_RULES

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def ws_app(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    yield TestClient(app), app
    app.state.sessions.close_all()


def start_shell(app, tmp_path, argv):
    s = app.state.sessions.create("Test", str(tmp_path), argv=argv)
    app.state.session_names[f"session:{s.id}"] = s.name
    return s


def test_page_has_three_panes(ws_app):
    client, _ = ws_app
    page = client.get("/workspace").text
    for label in ("Sessions", "New session", "Where do you want to work?", "Checkpoint", 'src="/?embed=1"', "/static/vendor/xterm.js"):
        assert label in page


def test_other_hosts_are_refused(ws_app):
    client, _ = ws_app
    assert client.get("/workspace", headers={"host": "evil.example"}).status_code == 400


def test_creating_a_session_needs_csrf(ws_app, tmp_path):
    client, app = ws_app
    assert client.post("/sessions", data={"csrf": "no", "kind": "shell", "cwd": str(tmp_path)}).status_code == 403
    assert client.post("/sessions", data={"csrf": app.state.csrf, "kind": "x", "cwd": str(tmp_path)}).status_code == 400
    assert client.post("/sessions", data={"csrf": app.state.csrf, "kind": "shell",
                                          "cwd": str(tmp_path / "missing")}).status_code == 400


def test_create_and_close_a_shell_session(ws_app, tmp_path):
    client, app = ws_app
    r = client.post("/sessions", data={"csrf": app.state.csrf, "kind": "shell", "cwd": str(tmp_path), "name": "Notes"},
                    follow_redirects=False)
    assert r.status_code == 303
    sid = r.headers["location"].split("s=")[1]
    assert app.state.session_names[f"session:{sid}"] == "Notes"
    assert "Notes" in client.get(r.headers["location"]).text
    client.post(f"/sessions/{sid}/close", data={"csrf": app.state.csrf})
    assert sid not in app.state.sessions.sessions


def test_missing_claude_is_explained(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), claude="/no/such/claude")
    r = TestClient(app).post("/sessions", data={"csrf": app.state.csrf, "kind": "claude", "cwd": str(tmp_path)})
    assert r.status_code == 400 and "Claude Code was not found" in r.text


def test_terminal_streams_both_ways(ws_app, tmp_path):
    client, app = ws_app
    s = start_shell(app, tmp_path, ["/bin/cat"])
    with client.websocket_connect(f"/term/{s.id}?key={app.state.csrf}", headers=ORIGIN) as ws:
        ws.send_json({"type": "resize", "rows": 30, "cols": 90})
        ws.send_json({"type": "input", "data": "ping-123\n"})
        seen = ""
        end = time.time() + 5
        while "ping-123" not in seen and time.time() < end:
            seen += ws.receive_text()
        assert "ping-123" in seen


@pytest.mark.parametrize("key, headers", [("wrong", ORIGIN), (None, {"origin": "http://evil.example"}), (None, {})])
def test_terminal_refuses_other_pages(ws_app, tmp_path, key, headers):
    client, app = ws_app
    s = start_shell(app, tmp_path, ["/bin/cat"])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/term/{s.id}?key={key or app.state.csrf}", headers=headers) as ws:
            ws.receive_text()


def test_terminal_routes_exist_only_in_app_mode(tmp_path):
    assert TestClient(create_app(tmp_path)).get("/workspace").status_code == 404


def test_embedded_review_keeps_the_embed_flag(ws_app):
    client, app = ws_app
    rec = app.state.engine.submit("session:x", "search", "web-search",
                                  {"query": {"value": "bob@x.io", "category": "email"}})
    page = client.get("/?embed=1").text
    assert 'class="embed"' in page and 'class="top"' not in page and 'name="embed" value="1"' in page
    confirm = client.get(f"/confirm/{rec.id}?choice=approve&embed=1").text
    assert 'name="embed" value="1"' in confirm and 'href="/?embed=1"' in confirm
    r = client.post(f"/decide/{rec.id}", data={"choice": "approve", "csrf": app.state.csrf, "embed": "1"},
                    follow_redirects=False)
    assert r.headers["location"] == "/?embed=1"
