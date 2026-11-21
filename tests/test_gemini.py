"""Gemini CLI: its hooks speak a different dialect. Hidee translates calls in and replies out, so both agents go
through the same rules, checkpoint, and learn-mode timeline."""
import json

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.dialects import gemini_call, gemini_event, gemini_reply
from hidee.hook_api import APP_RULES
from hidee.sessions import SessionManager, gemini_settings


# ---- calls in ----

def test_gemini_tools_map_to_the_same_checks():
    assert gemini_call("run_shell_command", {"command": "curl https://x.example"}) == \
        ("Bash", {"command": "curl https://x.example"})
    assert gemini_call("google_web_search", {"query": "dental"}) == ("WebSearch", {"query": "dental"})
    assert gemini_call("read_file", {"file_path": "/a/b.txt"}) == ("Read", {"file_path": "/a/b.txt"})
    assert gemini_call("read_file", {"absolute_path": "/a/b.txt"}) == ("Read", {"file_path": "/a/b.txt"})
    assert gemini_call("write_file", {"file_path": "/a", "content": "x"}) == ("Write", {"file_path": "/a", "content": "x"})
    assert gemini_call("replace", {"file_path": "/a", "old_string": "o", "new_string": "n"})[0] == "Edit"
    assert gemini_call("mcp__mail__send", {"to": "a@b.c"}) == ("mcp__mail__send", {"to": "a@b.c"})


def test_web_fetch_url_comes_from_the_prompt():
    tool, args = gemini_call("web_fetch", {"prompt": "Summarize https://news.example.com/a please"})
    assert tool == "WebFetch" and args["url"] == "https://news.example.com/a"


# ---- replies out ----

def test_no_opinion_stays_no_opinion():
    assert gemini_reply("Bash", {"command": "ls"}, {}) == {}


def test_deny_becomes_a_gemini_deny():
    out = gemini_reply("WebSearch", {"query": "x"}, {"hookSpecificOutput": {
        "permissionDecision": "deny", "permissionDecisionReason": "Hidee: Rejected by owner."}})
    assert out == {"decision": "deny", "reason": "Hidee: Rejected by owner."}


def test_shorter_version_rewrites_the_gemini_arguments():
    out = gemini_reply("WebSearch", {"query": "maria@example.org"}, {"hookSpecificOutput": {
        "permissionDecision": "allow", "permissionDecisionReason": "ok", "updatedInput": {"query": "[email]"}}})
    assert out["decision"] == "allow" and out["hookSpecificOutput"]["tool_input"] == {"query": "[email]"}


def test_shorter_shell_command_goes_back_as_command():
    out = gemini_reply("Bash", {"command": "curl -d a@b.c x"}, {"hookSpecificOutput": {
        "permissionDecision": "allow", "updatedInput": {"command": "curl -d REDACTED_EMAIL x"}}})
    assert out["hookSpecificOutput"]["tool_input"] == {"command": "curl -d REDACTED_EMAIL x"}


def test_shorter_web_fetch_goes_back_into_the_prompt():
    out = gemini_reply("WebFetch", {"url": "https://x.example/?e=a@b.c", "prompt": "Read https://x.example/?e=a@b.c"},
                       {"hookSpecificOutput": {"permissionDecision": "allow",
                                               "updatedInput": {"url": "https://x.example/?e=REDACTED_EMAIL",
                                                                "prompt": "Read https://x.example/?e=a@b.c"}}})
    assert out["hookSpecificOutput"]["tool_input"]["prompt"] == "Read https://x.example/?e=REDACTED_EMAIL"


# ---- learn-mode events ----

def test_gemini_events_become_timeline_events():
    assert gemini_event({"hook_event_name": "BeforeAgent", "prompt": "hi"}) == \
        {"hook_event_name": "UserPromptSubmit", "prompt": "hi"}
    after = gemini_event({"hook_event_name": "AfterTool", "tool_name": "read_file", "tool_input": {"file_path": "/a"},
                          "tool_response": {"llmContent": "file text", "returnDisplay": "Read 1 line"}})
    assert after == {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/a"},
                     "tool_response": "file text"}
    failed = gemini_event({"hook_event_name": "AfterTool", "tool_name": "run_shell_command",
                           "tool_input": {"command": "false"}, "tool_response": {"error": "exit 1"}})
    assert failed["hook_event_name"] == "PostToolUseFailure" and failed["error"] == "exit 1"
    assert gemini_event({"hook_event_name": "AfterAgent", "prompt_response": "done"}) == \
        {"hook_event_name": "Stop", "last_assistant_message": "done"}


# ---- the endpoints, with the Gemini header ----

def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def headers(app, session="g1"):
    return {"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": session, "X-Hidee-Agent": "gemini"}


def test_gemini_search_with_a_key_is_denied_in_gemini_format(tmp_path):
    client, app = make(tmp_path)
    key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    r = client.post("/hook/pretooluse", json={"hook_event_name": "BeforeTool", "tool_name": "google_web_search",
                                              "tool_input": {"query": f"is {key} valid"}}, headers=headers(app))
    assert r.json()["decision"] == "deny" and "Hidee" in r.json()["reason"]


def test_gemini_local_read_passes_with_no_opinion(tmp_path):
    client, app = make(tmp_path)
    r = client.post("/hook/pretooluse", json={"tool_name": "read_file", "tool_input": {"file_path": "notes.txt"},
                                              "cwd": str(tmp_path)}, headers=headers(app))
    assert r.json() == {}
    assert app.state.activity.since(0)[-1]["tool"] == "Read"


def test_gemini_learn_events_join_the_timeline(tmp_path):
    client, app = make(tmp_path)
    app.state.learn.enable("g1")
    client.post("/hook/learn", json={"hook_event_name": "BeforeAgent", "prompt": "hello"}, headers=headers(app))
    client.post("/hook/pretooluse", json={"tool_name": "read_file", "tool_input": {"file_path": "a.txt"}},
                headers=headers(app))
    client.post("/hook/learn", json={"hook_event_name": "AfterTool", "tool_name": "read_file",
                                     "tool_input": {"file_path": "a.txt"}, "tool_response": {"llmContent": "x"}},
                headers=headers(app))
    client.post("/hook/learn", json={"hook_event_name": "AfterAgent", "prompt_response": "done"}, headers=headers(app))
    kinds = [(e["kind"], e["tool"]) for e in client.get("/learn/g1").json()["events"]]
    assert kinds == [("prompt", ""), ("call", "Read"), ("result", "Read"), ("answer", "")]


# ---- starting a Gemini session ----

def test_gemini_settings_carry_the_hooks_and_the_sign_in(tmp_path):
    plain = gemini_settings(learn=False)
    assert set(plain["hooks"]) == {"BeforeTool", "BeforeAgent", "AfterTool", "AfterAgent"}   # evidence needs all
    assert plain["hooks"]["BeforeTool"][0]["hooks"][0]["timeout"] == 660_000      # milliseconds in Gemini
    learn = gemini_settings(learn=True)
    assert set(learn["hooks"]) == {"BeforeTool", "BeforeAgent", "AfterTool", "AfterAgent"}
    assert len(learn["hooks"]["AfterTool"]) == 2
    assert "security" not in plain                                                 # the user's own sign-in
    assert gemini_settings(auth="vertex-ai")["security"]["auth"]["selectedType"] == "vertex-ai"
    assert gemini_settings(auth="gemini-api-key")["security"]["auth"]["selectedType"] == "gemini-api-key"


def test_gemini_session_picks_its_settings_file_in_the_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    m = SessionManager(tmp_path, "http://x", "t", gemini="gemini")
    argv = m.argv_for("gemini", tmp_path / "settings.json")
    script = argv[4]
    assert argv[:4] == ["/bin/sh", "-l", "-i", "-c"] and script.endswith("exec gemini")
    assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" in script and "GOOGLE_GENAI_USE_VERTEXAI" in script


def test_gemini_session_writes_three_settings_files(tmp_path):
    m = SessionManager(tmp_path, "http://x", "t", gemini="/bin/sh")
    s = m.create("g", str(tmp_path), "gemini", argv=["/bin/sh", "-c", "sleep 0.1"], learn=True)
    folder = tmp_path / "sessions" / s.id
    for name in ("gemini-default.json", "gemini-vertex.json", "gemini-key.json"):
        data = json.loads((folder / name).read_text())
        assert "AfterTool" in data["hooks"]
    m.close_all()


def test_after_a_shorter_version_gemini_is_told_why(tmp_path):
    import threading, time
    client, app = make(tmp_path)

    def student():
        for _ in range(200):
            waiting = app.state.store.list_actions(status="waiting")
            if waiting:
                app.state.engine.decide(waiting[0].id, "minimal")
                return
            time.sleep(0.02)
    t = threading.Thread(target=student)
    t.start()
    client.post("/hook/pretooluse", json={"tool_name": "google_web_search",
                                          "tool_input": {"query": "maria.lopez@example.org dentist"}},
                headers=headers(app))
    t.join()
    after = client.post("/hook/aftertool", json={"hook_event_name": "AfterTool", "tool_name": "google_web_search"},
                        headers=headers(app)).json()
    note = after["hookSpecificOutput"]["additionalContext"]
    assert "the user's privacy checkpoint" in note and "Do not try to send the removed details" in note
    # Only once, and only for that session.
    assert client.post("/hook/aftertool", json={}, headers=headers(app)).json() == {}


def test_gemini_settings_always_carry_the_after_tool_notice():
    hooks = gemini_settings(learn=False)["hooks"]
    assert hooks["AfterTool"][0]["hooks"][0]["command"].endswith("-m hidee.hook --after")


def test_after_hook_script_fails_open():
    import io
    from hidee.hook import main

    def opener(request, timeout):
        raise OSError("down")
    env = {"HIDEE_URL": "http://127.0.0.1:9", "HIDEE_TOKEN": "t", "HIDEE_SESSION": "s1"}
    assert main(io.StringIO("{}"), io.StringIO(), io.StringIO(), env, opener, after=True) == 0


def test_safe_gemini_runs_inside_a_seatbelt_profile(tmp_path, monkeypatch):
    from hidee.sessions import gemini_profile
    text = gemini_profile(app_port=8770, gate_port=51234)
    assert "(deny file-write*)" in text and "(deny network-outbound)" in text
    assert 'remote tcp "localhost:8770"' in text and 'remote tcp "localhost:51234"' in text
    assert "/.ssh" in text and "application_default_credentials.json" in text     # Vertex sign-in still works
    monkeypatch.setenv("SHELL", "/bin/sh")
    m = SessionManager(tmp_path, "http://127.0.0.1:8770", "t", gemini="gemini")
    argv = m.argv_for("gemini", tmp_path / "settings.json", safe=True)
    assert "exec sandbox-exec -f" in argv[4] and argv[4].endswith(" gemini -e none")
    assert "-D TARGET_DIR=" in argv[4]


def test_gate_refuses_local_services_for_safe_sessions_on_its_main_port():
    import urllib.error, urllib.request
    import pytest
    from hidee.gate import Gate
    g = Gate("tok", lambda: {"s1"}, lambda s, h, u: (True, ""), lambda *a: None)
    g.start()
    g.safe.add("s1")
    proxy = urllib.request.ProxyHandler({"http": g.proxy_url("s1")})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.build_opener(proxy).open("http://localhost:9/x", timeout=5)
    assert e.value.code == 403


def test_gemini_mcp_calls_are_checked_as_mcp_calls(tmp_path):
    """Gemini sends MCP tools by their bare name with the server in mcp_context; Hidee must still hold them."""
    import threading
    import time

    from fastapi.testclient import TestClient

    from hidee.app import create_app
    from hidee.dialects import gemini_mcp_name
    from hidee.hook_api import APP_RULES
    assert gemini_mcp_name("send_email", {"server_name": "office", "tool_name": "send_email"}) == "mcp__office__send_email"
    assert gemini_mcp_name("read_file", None) == "read_file"
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    client = TestClient(app)

    def decide():
        for _ in range(200):
            waiting = app.state.store.list_actions(status="waiting")
            if waiting:
                app.state.engine.decide(waiting[0].id, "reject")
                return
            time.sleep(0.02)
    t = threading.Thread(target=decide)
    t.start()
    out = client.post("/hook/pretooluse", json={"tool_name": "send_email",
                                                "tool_input": {"to": "ann.b@example.org", "text": "hi"},
                                                "mcp_context": {"server_name": "office", "tool_name": "send_email"}},
                      headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "g1",
                               "X-Hidee-Agent": "gemini"}).json()
    t.join()
    assert out["decision"] == "deny"
    assert app.state.store.list_actions()[0].recipient == "mcp:office"
