from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.demo import create_demo_app


def test_cloud_demo_starts_with_one_card_and_a_banner(tmp_path):
    app = create_demo_app(tmp_path)
    page = TestClient(app).get("/").text
    assert page.count("Send shorter version") == 1
    assert "fictional data" in page and "Run the demo again" in page
    statuses = [r.status for r in app.state.store.list_actions()]
    assert statuses.count("waiting") == 1 and len(statuses) == 6


def test_cloud_demo_waits_a_day_before_timing_out(tmp_path):
    app = create_demo_app(tmp_path)
    assert app.state.engine.review_timeout_s == 24 * 3600


def test_replay_starts_the_demo_again_from_a_clean_slate(tmp_path):
    app = create_demo_app(tmp_path)
    client = TestClient(app)
    assert client.post("/demo/replay", data={"csrf": "wrong"}).status_code == 403
    r = client.post("/demo/replay", data={"csrf": app.state.csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert len(app.state.store.list_actions()) == 6


def test_replay_and_banner_do_not_exist_outside_demo_mode(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.post("/demo/replay", data={"csrf": app.state.csrf}).status_code == 404
    assert "fictional data" not in client.get("/").text


def test_the_online_demo_publishes_no_local_only_pages(tmp_path):
    # Visitors may watch scripted sessions; they reach nothing that belongs to a user's own computer.
    from fastapi.testclient import TestClient
    from hidee.online import create_online_app
    app = create_online_app(pace=0.01)
    client = TestClient(app, base_url="http://try.testserver")
    page = client.get("/workspace")
    assert page.status_code == 200
    import re
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    for path in ("/api/docs", "/openapi.json", "/settings", "/evidence"):
        assert client.get(path).status_code == 404, path
    for path in ("/settings/updates", "/settings/updates/check", "/evidence/export", "/evidence/timeline",
                 "/evidence/reconcile", "/settings/keys/anthropic/test"):
        assert client.post(path, data={"csrf": csrf}).status_code == 404, path
    for kind in ("shell", "claude", "gemini", "codex"):
        assert client.post("/sessions", data={"csrf": csrf, "kind": kind, "where": "open", "cwd": "/"}).status_code == 400
