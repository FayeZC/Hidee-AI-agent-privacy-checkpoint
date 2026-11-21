"""Regressions for issues found while documenting Hidee (2026-11-05)."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.gate import Gate, is_model_host
from hidee.hook_api import APP_RULES
from hidee.sessions import SessionManager, codex_overrides, gemini_profile


def test_gemini_session_keeps_its_name(tmp_path):
    m = SessionManager(tmp_path, "http://127.0.0.1:8770", "t", gemini="/bin/sh")
    s = m.create("Lab 1", str(tmp_path), "gemini", argv=["/bin/sh", "-c", "sleep 0.1"])
    assert s.name == "Lab 1"
    assert json.loads((tmp_path / "sessions" / s.id / "meta.json").read_text())["name"] == "Lab 1"
    m.close_all()


def make(tmp_path, **kw):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), **kw)
    return TestClient(app), app


def evidence(app, event):
    out = []
    for f in sorted((app.state.home / "evidence").glob("*.jsonl")):
        out += [json.loads(l) for l in f.read_text().splitlines()]
    return [r["data"] for r in out if r["event"] == event]


def search(client, app, q):
    return client.post("/hook/pretooluse", json={"tool_name": "WebSearch", "tool_input": {"query": q}},
                       headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"})


def test_a_persons_reject_is_recorded_as_reject(tmp_path):
    client, app = make(tmp_path)

    def student():
        for _ in range(200):
            w = app.state.store.list_actions(status="waiting")
            if w:
                app.state.engine.decide(w[0].id, "reject")
                return
            time.sleep(0.02)
    t = threading.Thread(target=student)
    t.start()
    search(client, app, "maria.lopez@example.org dentist")
    t.join()
    d = evidence(app, "decision")[-1]
    assert d["by"] == "person" and d["choice"] == "reject"


def test_a_timeout_is_not_a_persons_decision(tmp_path):
    client, app = make(tmp_path, review_timeout_s=0.3)
    search(client, app, "maria.lopez@example.org dentist")
    d = evidence(app, "decision")[-1]
    assert d["by"] == "timeout" and d["choice"] == "timed_out" and "user" not in d


def test_model_hosts_cover_every_supported_agent():
    for host in ("generativelanguage.googleapis.com", "cloudcode-pa.googleapis.com", "api.openai.com",
                 "chatgpt.com", "api.anthropic.com", "aiplatform.googleapis.com"):
        assert is_model_host(host), host
    assert not is_model_host("example.com")


class Echo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def do_GET(self):
        Echo.seen.append((self.path, self.headers.get("Proxy-Authorization")))
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_gate_does_not_forward_a_second_request_on_a_reused_connection(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    seen = []
    g = Gate("tok", lambda: {"s1"}, lambda s, h, u: (True, ""), lambda *a: seen.append(a))
    g.start()
    import base64
    auth = "Basic " + base64.b64encode(b"s1:tok").decode()
    sock = socket.create_connection(("127.0.0.1", g.port), timeout=5)
    req = (f"GET http://127.0.0.1:{port}/first HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nProxy-Authorization: {auth}\r\n\r\n"
           f"GET http://127.0.0.1:{port}/second HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nProxy-Authorization: {auth}\r\n\r\n")
    sock.sendall(req.encode())
    time.sleep(1)
    sock.close()
    server.shutdown()
    assert ("/first", None) in Echo.seen
    assert all(path != "/second" for path, _ in Echo.seen)               # not forwarded unchecked
    assert all(a is None for _, a in Echo.seen)                         # credentials never reach the host


def test_codex_hook_commands_survive_unusual_python_paths(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "executable", "/Users/o'neil/My Apps/python3")
    args = codex_overrides()
    value = args[args.index("-c") + 1]
    import tomllib
    parsed = tomllib.loads("x = " + value.split("=", 1)[1])
    assert parsed["x"][0]["hooks"][0]["command"] == "/Users/o'neil/My Apps/python3 -m hidee.hook"


def test_gemini_safe_profile_denies_writes_to_hidee_records():
    assert '"/.hidee"' in gemini_profile(8770, 1234).split("(deny file-read*")[0].split("(allow file-write*")[1] \
        or "(deny file-write* (subpath (string-append" in gemini_profile(8770, 1234)


def test_safe_mode_refuses_the_home_folder(tmp_path, monkeypatch):
    client, app = make(tmp_path, claude="/bin/sh")
    monkeypatch.setattr("hidee.workspace.Path.home", lambda: tmp_path)
    r = client.post("/sessions", data={"csrf": app.state.csrf, "kind": "claude", "where": "open",
                                       "cwd": str(tmp_path), "safe": "1"})
    assert r.status_code == 400 and "home folder" in r.text


def test_start_panel_names_the_right_key_for_each_agent(tmp_path):
    from hidee.keys import KeyStore, MemoryBackend
    client, app = make(tmp_path)
    app.state.keys = KeyStore(MemoryBackend())
    app.state.keys.save("gemini", "AIzaSyTESTKEY-9876")
    page = client.get("/workspace?new=1").text
    assert "Gemini ••••9876" in page and "My Anthropic key" not in page
    assert "coming soon" not in client.get("/settings").text
