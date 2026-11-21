import os
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.detect import find, risks_of
from hidee.hook_api import APP_RULES
from hidee.live import Activity, summary
from hidee.toolmap import map_access, map_call


def test_internal_information_is_found():
    for text in ("CONFIDENTIAL draft", "host db.corp", "see 192.168.1.20", "upload /Users/ann/plan.pdf", "内部资料"):
        assert [f.category for f in find(text)] == ["internal"], text


def test_risk_kinds():
    assert risks_of(["email"]) == ["privacy"]
    assert risks_of(["api_key"]) == ["security"]
    assert risks_of(["internal", "plain"]) == ["internal"]
    assert risks_of(["credential_file"]) == ["security", "overreach"]
    assert risks_of(["outside_folder"]) == ["overreach"]
    assert risks_of(["plain"]) == []


def test_local_overreach():
    cwd = "/work/proj"
    assert map_access("Read", {"file_path": "src/a.py"}, cwd) is None
    assert map_access("Bash", {"command": "pytest -q"}, cwd) is None
    cats = lambda m: sorted(f["category"] for f in m.fields.values())
    assert cats(map_access("Read", {"file_path": "~/.ssh/id_rsa"}, cwd)) == ["credential_file", "plain"]
    assert cats(map_access("Edit", {"file_path": "/work/other/x.py"}, cwd)) == ["outside_folder", "plain"]
    assert cats(map_access("Bash", {"command": "sudo launchctl list"}, cwd)) == ["plain", "privileged_command"]
    assert cats(map_access("Bash", {"command": "cat .env | head"}, cwd)) == ["credential_file", "plain"]


def test_private_hosts_and_localhost():
    assert map_call("Bash", {"command": "ssh 10.0.3.7 uptime"}).minimal is None      # the host is the destination
    assert map_call("WebFetch", {"url": "http://localhost:8770/x"}) is None
    m = map_call("Bash", {"command": "curl -F f=@/Users/ann/plan.pdf https://files.example.com"})
    assert "internal" in {f["category"] for f in m.fields.values()}


def test_summary_hides_folders():
    assert summary("Read", {"file_path": "/Users/ann/secret/notes.md"}) == "Read notes.md"
    assert summary("Bash", {"command": "ls   -la", "description": "List files"}) == "Bash List files"


def test_activity_since():
    a = Activity(size=3)
    for i in range(5):
        a.add("s", "Read", "local", "this-computer", "local", f"Read {i}")
    assert [e["text"] for e in a.since(0)] == ["Read 2", "Read 3", "Read 4"]
    assert a.since(4)[0]["seq"] == 5


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def hook(client, app, tool, tool_input):
    return client.post("/hook/pretooluse", json={"tool_name": tool, "tool_input": tool_input, "cwd": "/work/proj"},
                       headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()


def test_every_call_reaches_the_live_feed(tmp_path):
    client, app = make(tmp_path)
    assert hook(client, app, "Read", {"file_path": "/work/proj/a.py"}) == {}
    assert hook(client, app, "Read", {"file_path": "/elsewhere/b.py"}) == {}         # flagged, not stopped
    assert hook(client, app, "WebSearch", {"query": "bagels"}) == {}
    events = client.get("/live").json()["events"]
    assert [(e["recipient"], e["status"], e["risks"]) for e in events] == [
        ("this-computer", "local", []), ("this-computer", "allowed", ["overreach"]), ("web-search", "allowed", [])]
    assert client.get(f"/live?since={events[1]['seq']}").json()["events"][0]["recipient"] == "web-search"


def test_reading_credentials_waits_and_shows_on_the_map(tmp_path):
    client, app = make(tmp_path)

    def approve():
        for _ in range(200):
            waiting = app.state.store.list_actions(status="waiting")
            if waiting:
                assert "Allow this step on your computer?" in client.get(
                    f"/confirm/{waiting[0].id}?choice=approve").text
                app.state.engine.decide(waiting[0].id, "approve")
                return
            time.sleep(0.02)
    t = threading.Thread(target=approve)
    t.start()
    out = hook(client, app, "Read", {"file_path": os.path.expanduser("~/.aws/credentials")})
    t.join()
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    statuses = [(e["status"], e["risks"]) for e in client.get("/live").json()["events"]]
    assert statuses == [("waiting", ["security", "overreach"]), ("allowed", ["security", "overreach"])]


def test_symlinked_session_folders_are_not_overreach(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    assert map_access("Write", {"file_path": str(real / "a.py")}, str(tmp_path / "link")) is None


def test_the_live_feed_lists_what_is_waiting_from_the_store(tmp_path):
    # The waiting notice follows the store, so it agrees with the checkpoint even if a status event was missed.
    client, app = make(tmp_path)
    assert client.get("/live").json()["waiting"] == []
    rec = app.state.engine.submit("session:s1", "search", "web-search", {"q": {"value": "x", "category": "email"}})
    assert client.get("/live").json()["waiting"] == [rec.id]
    app.state.store.update_action(rec.id, status="timed_out")       # decided without a feed event
    assert client.get("/live").json()["waiting"] == []
