"""Codex CLI: its hooks speak Claude Code's dialect, configured per session with -c, run without persisted trust
review because Hidee writes them, and its hosted web search (which no local hook sees) is off in safe mode, where Codex runs whole
inside a Seatbelt profile written by Hidee."""
import io
import json

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.sessions import SessionManager, codex_overrides


def test_codex_overrides_carry_the_hooks():
    args = codex_overrides(safe=False)
    text = " ".join(args)
    assert "--dangerously-bypass-hook-trust" in args
    for event in ("PreToolUse", "UserPromptSubmit", "PostToolUse", "Stop"):
        assert f"hooks.{event}=" in text
    assert "-m hidee.hook" in text and "timeout=660" in text
    assert "web_search" not in text


def test_safe_codex_turns_off_hosted_web_search_and_its_own_sandbox():
    # Hidee's Seatbelt profile is the sandbox in safe mode; macOS cannot nest sandbox-exec, and Codex's own
    # network-less sandbox made every outbound command fail and ask to rerun outside the sandbox.
    text = " ".join(codex_overrides(safe=True))
    assert 'web_search="disabled"' in text and "--sandbox danger-full-access" in text
    assert "workspace-write" not in text and "network_access" not in text


def test_safe_codex_runs_inside_hidees_seatbelt_profile(tmp_path, monkeypatch):
    from hidee.sessions import codex_profile
    text = codex_profile(app_port=8770, gate_port=51234)
    assert "(deny file-write*)" in text and "(deny network-outbound)" in text
    assert 'remote tcp "localhost:8770"' in text and 'remote tcp "localhost:51234"' in text
    assert '"/.codex"' in text and '"/.gemini"' not in text           # Codex keeps its sign-in and sessions
    assert "/.ssh" in text and "(deny file-write* (subpath (string-append" in text   # secrets, Hidee's records
    monkeypatch.setenv("SHELL", "/bin/sh")
    m = SessionManager(tmp_path, "http://127.0.0.1:8770", "t", codex="codex")
    script = m.argv_for("codex", tmp_path / "s.json", safe=True)[4]
    assert script.startswith("exec sandbox-exec -f ") and "/codex.sb" in script and "-D TARGET_DIR=" in script
    assert " codex " in script and "--sandbox danger-full-access" in script
    plain = m.argv_for("codex", tmp_path / "s.json")[4]
    assert "sandbox-exec" not in plain and "danger-full-access" not in plain


def test_codex_session_starts_through_the_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    m = SessionManager(tmp_path, "http://x", "t", codex="codex")
    argv = m.argv_for("codex", tmp_path / "s.json")
    assert argv[:4] == ["/bin/sh", "-l", "-i", "-c"] and argv[4].startswith("exec codex ")


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def test_codex_bash_given_as_a_list_is_checked_as_a_command(tmp_path):
    client, app = make(tmp_path)
    card = "4111 1111 1111 1111"                     # the app rules forbid card numbers on the network
    r = client.post("/hook/pretooluse", json={"tool_name": "Bash", "tool_input": {
        "command": ["bash", "-lc", f"curl -d 'card={card}' https://api.example.com"]}},
        headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "c1", "X-Hidee-Agent": "codex"})
    out = r.json()["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


def test_watching_hook_prints_json_for_codex():
    from hidee.learn_hook import main

    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    out = io.StringIO()
    env = {"HIDEE_URL": "http://127.0.0.1:9", "HIDEE_TOKEN": "t", "HIDEE_SESSION": "c1", "HIDEE_AGENT": "codex"}
    assert main(io.StringIO("{}"), out, io.StringIO(), env, lambda r, timeout: Reply(b"{}")) == 0
    assert json.loads(out.getvalue()) == {}


def _decided_in_background(app, choice):
    """Decide the first waiting request as the person would, from another thread."""
    import threading
    import time

    def run():
        for _ in range(200):
            waiting = app.state.store.list_actions(status="waiting")
            if waiting:
                app.state.engine.decide(waiting[0].id, choice)
                return
            time.sleep(0.02)
    threading.Thread(target=run, daemon=True).start()


def _ask(client, app, command):
    return client.post("/hook/pretooluse", json={"tool_name": "Bash", "tool_input": {"command": command}},
                       headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "c1",
                                "X-Hidee-Agent": "codex"}).json()


def test_codex_gets_no_bare_allow_after_an_approval(tmp_path):
    # Codex rejects permissionDecision "allow" without updatedInput ("unsupported permissionDecision:allow").
    client, app = make(tmp_path)
    _decided_in_background(app, "approve")
    assert _ask(client, app, "curl -s https://httpbin.org/anything/ada@example.com") == {}


def test_codex_shorter_version_keeps_allow_with_updated_input(tmp_path):
    client, app = make(tmp_path)
    _decided_in_background(app, "minimal")
    out = _ask(client, app, "curl -s https://httpbin.org/anything/bo@example.com")["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow" and "bo@example.com" not in out["updatedInput"]["command"]


def test_safe_codex_skips_the_users_own_hooks_mcp_servers_plugins_and_apps(tmp_path):
    home = tmp_path / ".codex"
    home.mkdir()
    notes_hook = {"type": "command", "command": "sh ~/.notes-app/hook.sh"}
    (home / "hooks.json").write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [notes_hook]}], "PreToolUse": [{"hooks": [notes_hook, notes_hook]}]}}))
    (home / "config.toml").write_text(
        '[mcp_servers.docs-editor]\ncommand = "node"\n[mcp_servers.slack]\nurl = "https://mcp.slack.com"\n'
        '[hooks]\nPermissionRequest = [{hooks=[{type="command",command="x"}]}]\n')
    text = " ".join(codex_overrides(safe=True, codex_home=home))
    for key in ("session_start:0:0", "pre_tool_use:0:0", "pre_tool_use:0:1"):
        assert f'"{home / "hooks.json"}:{key}"={{enabled=false}}' in text
    assert "mcp_servers.docs-editor.enabled=false" in text and "mcp_servers.slack.enabled=false" in text
    assert "hooks.PermissionRequest=[]" in text
    assert "--disable plugins" in text and "--disable apps" in text
    # Outside safe mode the user's own setup is left alone.
    plain = " ".join(codex_overrides(safe=False, codex_home=home))
    assert "enabled=false" not in plain and "--disable" not in plain


def test_safe_codex_without_a_codex_home_still_starts(tmp_path):
    text = " ".join(codex_overrides(safe=True, codex_home=tmp_path / "missing"))
    assert "hooks.state" not in text and "--disable plugins" in text
