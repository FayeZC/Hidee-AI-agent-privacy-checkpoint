import base64
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.gate import Gate, is_model_host
from hidee.hook_api import APP_RULES


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"hello {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def upstream():
    server = HTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture
def gate():
    seen = []
    g = Gate("tok", lambda: {"s1"}, lambda s, host, url: (host != "blocked.test", "no"),
             lambda *a: seen.append(a))
    g.start()
    g.seen = seen
    return g


def opener(proxy):
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def test_needs_session_credentials(gate, upstream):
    with pytest.raises(urllib.error.HTTPError) as e:
        opener(f"http://127.0.0.1:{gate.port}").open(f"http://127.0.0.1:{upstream}/x", timeout=5)
    assert e.value.code == 407
    with pytest.raises(urllib.error.HTTPError) as e:
        opener(f"http://s2:tok@127.0.0.1:{gate.port}").open(f"http://127.0.0.1:{upstream}/x", timeout=5)
    assert e.value.code == 407                                  # not a live session


def test_forwards_plain_http_and_records_it(gate, upstream):
    r = opener(gate.proxy_url("s1")).open(f"http://127.0.0.1:{upstream}/a?b=1", timeout=5)
    assert r.read() == b"hello /a?b=1"
    assert gate.seen == [("s1", "127.0.0.1", upstream, f"http://127.0.0.1:{upstream}/a?b=1", True)]


def test_tunnels_connect(gate, upstream):
    s = socket.create_connection(("127.0.0.1", gate.port), timeout=5)
    auth = base64.b64encode(b"s1:tok").decode()
    s.sendall(f"CONNECT 127.0.0.1:{upstream} HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
    assert s.recv(100).startswith(b"HTTP/1.1 200")
    s.sendall(b"GET /t HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    assert data.endswith(b"hello /t")
    assert gate.seen[-1][:3] == ("s1", "127.0.0.1", upstream)


def test_refuses_forbidden_hosts(gate):
    with pytest.raises(urllib.error.HTTPError) as e:
        opener(gate.proxy_url("s1")).open("http://blocked.test/", timeout=5)
    assert e.value.code == 403 and gate.seen[-1][-1] is False


def test_model_hosts():
    assert is_model_host("api.anthropic.com") and is_model_host("us-east5-aiplatform.googleapis.com")
    assert not is_model_host("anthropic.com.evil.io") and not is_model_host("example.com")


def test_app_wires_the_gate_to_rules_and_the_map(tmp_path):
    rules = APP_RULES + '  - {agent: "*", kind: network, recipient: tracker.example, forbid: [plain]}\n'
    (tmp_path / "rules.yaml").write_text(rules.replace("recipients:\n", "recipients:\n  tracker.example: a tracker\n"))
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    s = app.state.sessions.create("t", str(tmp_path), argv=["/bin/sh", "-c", 'echo "$HTTPS_PROXY|$NO_PROXY"'])
    try:
        g = app.state.sessions.gate
        assert g.check(s.id, "api.github.com", "https://api.github.com:443")[0] is True
        assert g.check(s.id, "tracker.example", "https://tracker.example:443")[0] is False
        assert g.check(s.id, "api.anthropic.com", "https://api.anthropic.com:443")[0] is True
        g.record(s.id, "api.anthropic.com", 443, "https://api.anthropic.com:443", True)
        g.record(s.id, "api.anthropic.com", 443, "https://api.anthropic.com:443", True)   # repeat: not doubled
        g.record(s.id, "tracker.example", 443, "https://tracker.example:443", False)
        events = TestClient(app).get("/live").json()["events"]
        assert [(e["recipient"], e["kind"], e["status"]) for e in events] == [
            ("model-provider", "model", "allowed"), ("tracker.example", "connection", "blocked")]
        import time
        for _ in range(100):
            if f"@127.0.0.1:{g.port}|127.0.0.1" in bytes(s.scrollback).decode():
                break
            time.sleep(0.02)
        assert f"http://{s.id}:" in bytes(s.scrollback).decode()
    finally:
        app.state.sessions.close_all()


def test_session_port_needs_no_credentials_and_attributes_the_session(gate):
    port = gate.session_port("s1")
    assert port != gate.port and gate.session_port("s1") == port          # one port per session, reused
    with pytest.raises(urllib.error.HTTPError) as e:
        opener(f"http://127.0.0.1:{port}").open("http://blocked.test/x", timeout=5)
    assert e.value.code == 403 and gate.seen[-1][:2] == ("s1", "blocked.test")


def test_session_port_refuses_local_services(gate, upstream):
    port = gate.session_port("s1")
    for target in (f"http://127.0.0.1:{upstream}/x", f"http://localhost:{upstream}/x"):
        with pytest.raises(urllib.error.HTTPError) as e:
            opener(f"http://127.0.0.1:{port}").open(target, timeout=5)
        assert e.value.code == 403, target
    assert gate.seen[-1][4] is False                                      # recorded as refused


def test_is_local_host():
    from hidee.gate import is_local_host
    for host in ("localhost", "api.localhost", "127.0.0.1", "::1", "169.254.169.254", "0.0.0.0",
                 "metadata.google.internal"):
        assert is_local_host(host), host
    assert not is_local_host("93.184.215.14")
