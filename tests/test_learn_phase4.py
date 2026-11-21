"""Learn mode, phase 4: export of the timeline, glossary notes, and a practice lesson without an API key."""
import json
import time

from fastapi.testclient import TestClient
from markupsafe import escape

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.learn import GLOSSARY, Timeline, export_json, export_markdown

KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"


def filled() -> Timeline:
    t = Timeline(clock=lambda: 1_762_300_000.0)
    t.record("s1", {"hook_event_name": "UserPromptSubmit", "prompt": "Find overdue patients"})
    t.call("s1", "Read", {"file_path": "/clinic/patients.csv"}, "")
    t.record("s1", {"hook_event_name": "PostToolUse", "tool_name": "Read",
                    "tool_input": {"file_path": "/clinic/patients.csv"},
                    "tool_response": f"name,phone\nMaria,917-555-0142\nAPI_KEY={KEY}\n```\ncode\n```"})
    t.call("s1", "Bash", {"command": f"curl -H 'Authorization: Bearer {KEY}' https://sms.example.com"}, "")
    t.record("s1", {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                    "tool_input": {"command": "curl"}, "error": "exit code 7"})
    t.record("s1", {"hook_event_name": "Stop", "last_assistant_message": "Two patients are overdue."})
    return t


# ---- 1. Export ----

def test_call_details_are_masked_too():
    t = filled()
    call = [e for e in t.since("s1", 0) if e["kind"] == "call"][1]
    assert KEY not in call["detail"] and KEY not in call["text"]


def test_markdown_export_reads_as_an_appendix():
    t = filled()
    usage = {"requests": 3, "first_input": 24517, "last_input": 25100, "output": 150, "model": "claude-x"}
    md = export_markdown(t.since("s1", 0), usage, name="Lab 1", agent="Claude Code", session="s1")
    assert md.startswith("# Behind the agent: Lab 1")
    assert KEY not in md
    for words in ("## Turn 1", "You asked", "The model chose a tool: Read", "The tool returned: Read",
                  "The tool failed: Bash", "The model answered", "Find overdue patients", "Two patients are overdue.",
                  "Stays on this computer", "Goes to the model provider", "Leaves this computer",
                  "Contains: API key or token, phone number", "25,100", "## Words used here", "Tool call"):
        assert words in md, words
    # A fence inside a tool result does not break the Markdown: the outer fence is longer.
    assert "````" in md


def test_markdown_export_without_usage_or_events():
    md = export_markdown([], None, name="Empty", agent="Shell", session="s9")
    assert "# Behind the agent: Empty" in md and "No steps were recorded" in md
    assert "Model requests" not in md


def test_json_export_keeps_every_event_and_the_usage():
    t = filled()
    data = json.loads(export_json(t.since("s1", 0), None, name="Lab 1", agent="Claude Code", session="s1"))
    assert data["session"] == "s1" and data["name"] == "Lab 1" and data["usage"] is None
    assert [e["kind"] for e in data["events"]] == ["prompt", "call", "result", "call", "failure", "answer"]
    assert data["summary"]["steps"] == 6 and data["summary"]["where"]["outside"] == 1
    assert "phone" in data["summary"]["found"]
    assert set(GLOSSARY) <= set(data["glossary"])
    assert KEY not in json.dumps(data)


def make(tmp_path, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), **kw)
    return TestClient(app), app


def test_export_routes_download_files(tmp_path):
    client, app = make(tmp_path)
    app.state.learn.enable("s1")
    headers = {"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}
    client.post("/hook/learn", json={"hook_event_name": "UserPromptSubmit", "prompt": f"my key is {KEY}"},
                headers=headers)
    md = client.get("/learn/s1/export.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"] and ".md" in md.headers["content-disposition"]
    assert "You asked" in md.text and KEY not in md.text
    js = client.get("/learn/s1/export.json")
    assert js.status_code == 200 and js.headers["content-type"].startswith("application/json")
    assert ".json" in js.headers["content-disposition"]
    assert js.json()["events"][0]["kind"] == "prompt" and KEY not in js.text


def test_behind_page_offers_the_downloads(tmp_path):
    client, _ = make(tmp_path)
    page = client.get("/behind/s1").text
    assert 'href="/learn/s1/export.md"' in page and 'href="/learn/s1/export.json"' in page


# ---- 2. Glossary ----

def test_glossary_covers_the_words_the_page_uses():
    for term in ("Tool call", "Conversation (context)", "Token", "Model provider", "Hook", "Checkpoint",
                 "Shorter version", "Sandbox"):
        assert term in GLOSSARY and 20 < len(GLOSSARY[term]) < 260, term


def test_behind_page_shows_the_glossary(tmp_path):
    client, _ = make(tmp_path)
    page = client.get("/behind/s1").text
    assert "What do these words mean?" in page and "<details" in page
    for term, text in GLOSSARY.items():
        assert str(escape(term)) in page and str(escape(text)) in page


# ---- 3. Practice lesson ----

def wait_for(check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_start_panel_offers_the_practice_lesson(tmp_path):
    client, _ = make(tmp_path)
    page = client.get("/workspace?new=1").text
    assert 'value="practice"' in page and "Practice lesson" in page


def test_practice_lesson_plays_through_the_checkpoint_and_fills_the_timeline(tmp_path):
    projects = tmp_path / "projects"
    client, app = make(tmp_path / "home", online_pace=0)
    app.state.projects_dir = projects
    r = client.post("/sessions", data={"csrf": app.state.csrf, "where": "new", "project": "", "kind": "practice"},
                    follow_redirects=True)
    assert r.status_code == 200
    sid = next(iter(app.state.sessions.sessions))
    session = app.state.sessions.sessions[sid]
    assert session.kind == "practice" and session.learn and app.state.learn.enabled(sid)
    assert not projects.exists()                       # a practice lesson makes no project folder
    assert f'href="/behind/{sid}"' in r.text
    # Steps that wait go to the real checkpoint; the student decides there.
    assert wait_for(lambda: decide_all(app) or not session.alive, 20)
    events = client.get(f"/learn/{sid}").json()["events"]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "prompt" and kinds[-1] == "answer"
    assert "call" in kinds and "result" in kinds and "failure" in kinds
    assert {"local", "outside", "model"} <= {e["where"] for e in events}
    assert all("FICTIONAL" not in e["text"] for e in events)        # the fictional key is masked as well
    assert app.state.activity.since(0)                              # the live map saw the steps
    app.state.sessions.close_all()


def decide_all(app) -> bool:
    """Reject every step that waits for a person, as a student might in the checkpoint."""
    engine = app.state.engine
    for rec in engine.store.list_actions(status="waiting"):
        engine.decide(rec.id, "reject")
    return False


def test_practice_lesson_masks_the_fictional_key_when_the_student_approves(tmp_path):
    client, app = make(tmp_path, online_pace=0)
    client.post("/sessions", data={"csrf": app.state.csrf, "kind": "practice"})
    sid = next(iter(app.state.sessions.sessions))
    session, engine = app.state.sessions.sessions[sid], app.state.engine

    def approve_all():
        for rec in engine.store.list_actions(status="waiting"):
            engine.decide(rec.id, "approve")
        return not session.alive
    assert wait_for(approve_all, 20)
    env = [e for e in client.get(f"/learn/{sid}").json()["events"]
           if e["kind"] == "result" and "SMS_API_KEY" in e["text"]]
    assert env and "FICTIONAL" not in env[0]["text"] and "api_key" in env[0]["found"]
    assert "FICTIONAL" not in client.get(f"/learn/{sid}/export.md").text
    app.state.sessions.close_all()


def test_practice_terminal_ignores_typing_and_closes(tmp_path):
    client, app = make(tmp_path, online_pace=0)
    client.post("/sessions", data={"csrf": app.state.csrf, "where": "open", "cwd": "", "kind": "practice"})
    sid = next(iter(app.state.sessions.sessions))
    manager = app.state.sessions
    manager.write(sid, "hello\r")
    manager.resize(sid, 30, 90)
    assert b"fictional" in manager.subscribe(sid, lambda data: None)
    manager.close(sid)
    assert sid not in manager.sessions
