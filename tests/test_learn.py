"""Learn mode, phase 1: capture what happens behind the agent into a per-session timeline."""
import io
import json

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.learn import EXCERPT, Timeline
from hidee.learn_hook import main as learn_hook
from hidee.sessions import LEARN_EVENTS, hook_settings


# ---- the timeline itself ----

def test_prompt_call_result_answer_in_order():
    t = Timeline(clock=lambda: 1.0)
    t.record("s1", {"hook_event_name": "UserPromptSubmit", "prompt": "Find overdue patients"})
    t.call("s1", "Read", {"file_path": "/clinic/patients.csv"}, "")
    t.record("s1", {"hook_event_name": "PostToolUse", "tool_name": "Read",
                    "tool_input": {"file_path": "/clinic/patients.csv"}, "tool_response": "name,due\nMaria,2025"})
    t.record("s1", {"hook_event_name": "Stop", "last_assistant_message": "Two patients are overdue."})
    events = t.since("s1", 0)
    assert [e["kind"] for e in events] == ["prompt", "call", "result", "answer"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert events[0]["text"] == "Find overdue patients" and events[0]["where"] == "model"
    assert events[1]["tool"] == "Read" and events[1]["where"] == "local"
    assert events[2]["where"] == "model" and events[2]["size"] == len("name,due\nMaria,2025")
    assert events[3]["text"] == "Two patients are overdue." and events[3]["where"] == "model"
    assert t.since("s1", 2) == events[2:]


def test_outbound_call_is_marked_outside():
    t = Timeline()
    t.call("s1", "WebSearch", {"query": "dental reminder wording"}, "")
    t.call("s1", "Bash", {"command": "curl https://files.example.com/upload"}, "")
    t.call("s1", "Bash", {"command": "ls -la"}, "")
    assert [e["where"] for e in t.since("s1", 0)] == ["outside", "outside", "local"]


def test_long_results_are_cut_and_secrets_masked():
    t = Timeline()
    key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    t.record("s1", {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {},
                    "tool_response": f"API_KEY={key}\n" + "x" * 5000})
    e = t.since("s1", 0)[0]
    assert key not in e["text"] and len(e["text"]) <= EXCERPT + 1
    assert e["size"] > 5000 and "api_key" in e["found"]


def test_structured_tool_response_becomes_text():
    t = Timeline()
    t.record("s1", {"hook_event_name": "PostToolUse", "tool_name": "Grep", "tool_input": {"pattern": "due"},
                    "tool_response": {"matches": ["a", "b"]}})
    assert '"matches"' in t.since("s1", 0)[0]["text"]


def test_failure_is_recorded():
    t = Timeline()
    t.record("s1", {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                    "tool_input": {"command": "false"}, "error": "exit code 1"})
    e = t.since("s1", 0)[0]
    assert e["kind"] == "failure" and "exit code 1" in e["text"]


def test_unknown_events_are_ignored_and_sessions_kept_apart():
    t = Timeline()
    t.record("s1", {"hook_event_name": "Notification", "message": "hi"})
    t.record("s2", {"hook_event_name": "UserPromptSubmit", "prompt": "x"})
    assert t.since("s1", 0) == [] and len(t.since("s2", 0)) == 1


def test_timeline_keeps_a_bounded_number_of_events():
    t = Timeline(size=3)
    for i in range(5):
        t.record("s1", {"hook_event_name": "UserPromptSubmit", "prompt": str(i)})
    assert [e["text"] for e in t.since("s1", 0)] == ["2", "3", "4"]


# ---- session settings ----

def test_learn_sessions_register_the_extra_hooks():
    plain, learn = hook_settings(), hook_settings(learn=True)
    # Every session reports its turns (the evidence log needs them); learn mode only adds the view.
    assert set(plain["hooks"]) == set(learn["hooks"]) == {"PreToolUse", *LEARN_EVENTS}
    command = learn["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert command.endswith("-m hidee.learn_hook")


# ---- the hook script: never blocks, never changes the session ----

class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_learn_hook_posts_the_event_and_prints_nothing():
    seen = {}

    def opener(request, timeout):
        seen.update(url=request.full_url, body=json.loads(request.data), headers=dict(request.headers),
                    timeout=timeout)
        return Reply(b"{}")

    out = io.StringIO()
    env = {"HIDEE_URL": "http://127.0.0.1:9", "HIDEE_TOKEN": "t", "HIDEE_SESSION": "s1"}
    code = learn_hook(io.StringIO('{"hook_event_name": "Stop"}'), out, io.StringIO(), env, opener)
    assert code == 0 and out.getvalue() == ""
    assert seen["url"].endswith("/hook/learn") and seen["body"]["hook_event_name"] == "Stop"
    assert seen["headers"]["X-hidee-session"] == "s1" and seen["timeout"] <= 3


def test_learn_hook_fails_open():
    def opener(request, timeout):
        raise OSError("connection refused")

    err = io.StringIO()
    env = {"HIDEE_URL": "http://127.0.0.1:9", "HIDEE_TOKEN": "t", "HIDEE_SESSION": "s1"}
    assert learn_hook(io.StringIO("{}"), io.StringIO(), err, env, opener) == 0


# ---- the endpoints ----

def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def test_learn_endpoint_records_and_the_api_returns_events(tmp_path):
    client, app = make(tmp_path)
    app.state.learn.enable("s1")
    headers = {"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}
    r = client.post("/hook/learn", json={"hook_event_name": "UserPromptSubmit", "prompt": "hello"}, headers=headers)
    assert r.status_code == 200 and r.json() == {}
    events = client.get("/learn/s1").json()["events"]
    assert events[0]["kind"] == "prompt" and events[0]["text"] == "hello"
    assert client.get("/learn/s1?since=1").json()["events"] == []


def test_learn_endpoint_needs_the_hook_token(tmp_path):
    client, _ = make(tmp_path)
    r = client.post("/hook/learn", json={"hook_event_name": "Stop"},
                    headers={"X-Hidee-Token": "wrong", "X-Hidee-Session": "s1"})
    assert r.status_code == 401


def test_tool_calls_of_learn_sessions_join_the_timeline(tmp_path):
    client, app = make(tmp_path)
    app.state.learn.enable("s1")
    client.post("/hook/pretooluse", json={"tool_name": "Read", "tool_input": {"file_path": "notes.txt"}},
                headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"})
    client.post("/hook/pretooluse", json={"tool_name": "Read", "tool_input": {"file_path": "notes.txt"}},
                headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s2"})
    assert [e["kind"] for e in client.get("/learn/s1").json()["events"]] == ["call"]
    assert client.get("/learn/s2").json()["events"] == []


def test_starting_a_learn_session_turns_on_its_timeline(tmp_path, monkeypatch):
    client, app = make(tmp_path)
    r = client.post("/sessions", data={"csrf": app.state.csrf, "name": "Lab 1", "cwd": str(tmp_path),
                                       "kind": "shell", "learn": "1"}, follow_redirects=False)
    assert r.status_code == 303
    sid = next(iter(app.state.sessions.sessions))
    assert app.state.learn.enabled(sid) and app.state.sessions.sessions[sid].learn
    app.state.sessions.close_all()


def test_behind_the_agent_page(tmp_path):
    client, app = make(tmp_path)
    app.state.learn.enable("s1")
    page = client.get("/behind/s1").text
    assert "Behind the agent" in page and 'data-session="s1"' in page and "behind.js" in page


def test_explanations_cover_each_step():
    from hidee.learn import explain
    assert "file on this computer" in explain("call", "Read", "local")
    assert explain("call", "WebSearch", "outside").count("leave this computer") == 1
    assert "sends data off this computer" in explain("call", "Bash", "outside")
    assert "model provider" in explain("result", "Read", "model")
    assert explain("prompt", "", "model") and explain("answer", "", "model") and explain("failure", "Bash", "model")
    assert explain("call", "SomeNewTool", "local")                      # a fallback for unknown tools


def test_timeline_events_carry_their_explanation():
    t = Timeline()
    t.call("s1", "Read", {"file_path": "a.txt"}, "")
    assert "file on this computer" in t.since("s1", 0)[0]["why"]


def test_terminal_head_links_to_the_view_for_learn_sessions(tmp_path):
    client, app = make(tmp_path)
    r = client.post("/sessions", data={"csrf": app.state.csrf, "where": "open", "cwd": str(tmp_path),
                                       "kind": "shell", "learn": "1"}, follow_redirects=True)
    sid = next(iter(app.state.sessions.sessions))
    assert f'href="/behind/{sid}"' in r.text
    app.state.sessions.close_all()


def test_token_usage_comes_from_the_claude_transcript(tmp_path, monkeypatch):
    import json as _json
    from hidee import learn as learn_mod
    projects = tmp_path / ".claude" / "projects" / "p"
    projects.mkdir(parents=True)
    transcript = projects / "t.jsonl"
    def usage(mid, cache_create, cache_read, out):
        return _json.dumps({"type": "assistant", "message": {"id": mid, "model": "claude-x", "usage": {
            "input_tokens": 2, "cache_creation_input_tokens": cache_create, "cache_read_input_tokens": cache_read,
            "output_tokens": out}}})
    transcript.write_text("\n".join([_json.dumps({"type": "user"}), usage("m1", 24515, 0, 68),
                                     usage("m2", 106, 24515, 82), usage("m2", 106, 24515, 82)]) + "\n")
    monkeypatch.setattr(learn_mod, "TRANSCRIPTS", tmp_path / ".claude" / "projects")
    t = Timeline()
    t.record("s1", {"hook_event_name": "Stop", "last_assistant_message": "ok", "transcript_path": str(transcript)})
    u = t.usage("s1")
    assert u == {"requests": 2, "first_input": 24517, "last_input": 24623, "output": 150, "model": "claude-x"}


def test_transcripts_outside_the_claude_folder_are_ignored(tmp_path):
    t = Timeline()
    secret = tmp_path / "secret.jsonl"
    secret.write_text('{"type": "assistant"}\n')
    t.record("s1", {"hook_event_name": "Stop", "last_assistant_message": "ok", "transcript_path": str(secret)})
    assert t.usage("s1") is None
