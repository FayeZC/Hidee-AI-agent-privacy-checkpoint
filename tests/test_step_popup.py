import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def hook(client, app, tool, tool_input, session="s1"):
    return client.post("/hook/pretooluse", json={"tool_name": tool, "tool_input": tool_input},
                       headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": session}).json()


def test_checked_step_pops_up_as_a_picture(tmp_path):
    client, app = make(tmp_path)
    hook(client, app, "WebSearch", {"query": "is AKIAIOSFODNN7EXAMPLE valid"})
    rec = app.state.store.list_actions()[0]
    page = client.get(f"/step/{rec.id}").text
    assert 'role="dialog"' in page and "<svg" in page and "Information flow" in page
    assert "security" in page and "Blocked" in page and f"/dashboard?id={rec.id}" in page


def test_recorded_step_pops_up_too(tmp_path):
    client, app = make(tmp_path)
    hook(client, app, "Read", {"file_path": "/x/notes.md"})
    seq = client.get("/live").json()["events"][0]["seq"]
    page = client.get(f"/step/event/{seq}").text
    assert "Recorded step" in page and "This computer" in page and "ordinary work" in page.lower()
    assert client.get("/step/event/999").status_code == 404


def test_waiting_step_points_to_the_checkpoint(tmp_path):
    client, app = make(tmp_path)
    rec = app.state.engine.submit("session:s1", "search", "web-search",
                                  {"query": {"value": "bob@x.io", "category": "email"}})
    page = client.get(f"/step/{rec.id}").text
    assert 'href="/workspace"' in page and "Decide in the checkpoint" in page


def test_app_navigation(tmp_path):
    client, app = make(tmp_path)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/workspace"
    assert client.get("/?embed=1").status_code == 200
    dash = client.get("/dashboard").text
    assert 'href="/workspace"' in dash and "App preview" not in dash and ">To review<" not in dash
    assert 'target="_blank"' not in client.get("/workspace").text


def test_dashboard_items_open_the_popup(tmp_path):
    client, app = make(tmp_path)
    hook(client, app, "WebSearch", {"query": "is AKIAIOSFODNN7EXAMPLE valid"})
    rec = app.state.store.list_actions()[0]
    assert f'data-step="/step/{rec.id}"' in client.get("/dashboard").text


def test_session_names_survive_a_restart(tmp_path):
    client, app = make(tmp_path)
    s = app.state.sessions.create("Research", str(tmp_path), argv=["/bin/sh", "-c", "true"])
    app.state.sessions.close_all()
    _, again = make(tmp_path)
    assert again.state.session_names[f"session:{s.id}"] == "Research"
