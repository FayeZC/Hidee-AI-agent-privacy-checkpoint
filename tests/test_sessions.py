import json
import time
from pathlib import Path

import pytest

from hidee.sessions import HOOK_MATCHER, SessionManager, hook_settings


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def manager(tmp_path):
    m = SessionManager(tmp_path, "http://127.0.0.1:8770", "secret")
    yield m
    m.close_all()


def text(s):
    return bytes(s.scrollback).decode(errors="replace")


def test_output_reaches_the_scrollback(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/sh", "-c", "printf hello-pty; sleep 0.2"])
    assert wait_for(lambda: "hello-pty" in text(s))
    assert wait_for(lambda: "process exited with code 0" in text(s))


def test_input_and_resize(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/sh"])
    manager.resize(s.id, 40, 120)
    manager.write(s.id, "stty size; echo done-$((1+1))\n")
    assert wait_for(lambda: "40 120" in text(s) and "done-2" in text(s))


def test_environment_and_settings(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/sh", "-c", 'echo "$HIDEE_URL $HIDEE_TOKEN $HIDEE_SESSION"'])
    assert wait_for(lambda: f"http://127.0.0.1:8770 secret {s.id}" in text(s))
    settings = json.loads((tmp_path / "sessions" / s.id / "settings.json").read_text())
    assert settings == hook_settings()
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == HOOK_MATCHER and hook["hooks"][0]["timeout"] == 660
    assert hook["hooks"][0]["command"].endswith(" -m hidee.hook")


def test_claude_sessions_start_like_a_terminal_with_the_settings_file(manager, tmp_path, monkeypatch):
    # Through the user's own shell, so whatever sign-in works in their terminal (for example Vertex AI variables
    # in .zshrc, or a Claude subscription) works here too.
    monkeypatch.setenv("SHELL", "/bin/sh")
    argv = manager.argv_for("claude", tmp_path / "my settings.json")
    assert argv[:4] == ["/bin/sh", "-l", "-i", "-c"]
    assert argv[4] == f"exec claude --settings '{tmp_path / 'my settings.json'}'"


@pytest.mark.skipif(not Path("/bin/zsh").exists(), reason="needs zsh")
def test_agent_started_through_the_shell_sees_shell_profile_variables(tmp_path, monkeypatch):
    (tmp_path / ".zshrc").write_text("export HIDEE_PROFILE_PROBE=from-zshrc\n")
    monkeypatch.setenv("ZDOTDIR", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    probe = tmp_path / "probe.sh"
    probe.write_text("#!/bin/sh\necho probe=$HIDEE_PROFILE_PROBE\nsleep 0.2\n")
    probe.chmod(0o755)
    m = SessionManager(tmp_path, "http://127.0.0.1:8770", "secret", claude=str(probe))
    try:
        s = m.create("t", str(tmp_path))
        assert wait_for(lambda: "probe=from-zshrc" in text(s))
    finally:
        m.close_all()


def test_subscribers_get_scrollback_then_live_output(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/cat"])
    manager.write(s.id, "first\n")
    assert wait_for(lambda: "first" in text(s))
    got = []
    before = manager.subscribe(s.id, got.append)
    assert b"first" in before
    manager.write(s.id, "second\n")
    assert wait_for(lambda: b"second" in b"".join(got))


def test_close_stops_the_process(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/sleep", "30"])
    manager.close(s.id)
    assert not s.alive and s.id not in manager.sessions


def test_claude_session_markers_are_not_inherited(manager, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    s = manager.create("t", str(tmp_path), argv=["/bin/sh", "-c", 'echo "[${CLAUDE_CODE_CHILD_SESSION}|${CLAUDE_CODE_USE_VERTEX}]"'])
    assert wait_for(lambda: "[|1]" in text(s))


def test_learn_session_writes_the_watching_hooks(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/sh", "-c", "sleep 0.1"], learn=True)
    settings = json.loads((tmp_path / "sessions" / s.id / "settings.json").read_text())
    assert "UserPromptSubmit" in settings["hooks"] and s.learn is True


def test_safe_mode_adds_a_strict_sandbox_and_pins_the_permission_mode():
    from hidee.sessions import LOCAL_HOSTS, SECRET_PATHS
    plain, safe = hook_settings(), hook_settings(safe=True)
    assert "sandbox" not in plain and "permissions" not in plain
    box = safe["sandbox"]
    assert box["enabled"] and box["failIfUnavailable"] and box["allowUnsandboxedCommands"] is False
    assert set(SECRET_PATHS) <= set(box["filesystem"]["denyRead"])
    assert "~/.hidee" in box["filesystem"]["denyWrite"]
    assert set(LOCAL_HOSTS) <= set(box["network"]["deniedDomains"])        # no reaching local services
    assert safe["permissions"]["defaultMode"] == "default"                  # never the user's auto mode
    assert "PreToolUse" in safe["hooks"]


def test_safe_session_writes_the_sandbox_into_its_settings(manager, tmp_path):
    s = manager.create("t", str(tmp_path), argv=["/bin/sh", "-c", "sleep 0.1"], safe=True)
    settings = json.loads((tmp_path / "sessions" / s.id / "settings.json").read_text())
    assert settings["sandbox"]["enabled"] and s.safe


def test_safe_session_sends_sandbox_http_to_its_own_gate_port(tmp_path):
    class FakeGate:
        def proxy_url(self, sid): return f"http://{sid}:t@127.0.0.1:1"
        def session_port(self, sid): return 45678
    m = SessionManager(tmp_path, "http://x", "t", gate=FakeGate())
    try:
        s = m.create("t", str(tmp_path), argv=["/bin/sh", "-c", "sleep 0.1"], safe=True)
        box = json.loads((tmp_path / "sessions" / s.id / "settings.json").read_text())["sandbox"]
        assert box["network"]["httpProxyPort"] == 45678
    finally:
        m.close_all()


def test_safe_claude_skips_the_users_own_customizations(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/sh")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"env": {"ANTHROPIC_VERTEX_PROJECT_ID": "p1"}, "hooks": {"Stop": []}, "enabledPlugins": {"x": True}}))
    m = SessionManager(tmp_path, "http://x", "t", user_home=tmp_path)
    script = m.argv_for("claude", tmp_path / "s.json", safe=True)[4]
    for flag in ("--setting-sources project,local", "--disable-slash-commands", "--strict-mcp-config"):
        assert flag in script
    assert "--setting-sources" not in m.argv_for("claude", tmp_path / "s.json")[4]
    # Sign-in variables from the user's own settings still apply; nothing else from that file does.
    s = m.create("t", str(tmp_path), argv=["/bin/sh", "-c", "sleep 0.1"], safe=True)
    settings = json.loads((tmp_path / "sessions" / s.id / "settings.json").read_text())
    assert settings["env"] == {"ANTHROPIC_VERTEX_PROJECT_ID": "p1"} and "enabledPlugins" not in settings
    m.close_all()


def test_safe_gemini_turns_off_the_users_hooks_skills_mcp_and_extensions(tmp_path, monkeypatch):
    from hidee.sessions import gemini_settings
    monkeypatch.setenv("SHELL", "/bin/sh")
    user = {"hooks": {"AfterTool": [{"hooks": [{"type": "command", "command": "sh ~/.notes-app/hook.sh"}]}],
                      "BeforeAgent": [{"hooks": [{"type": "command", "name": "notes", "command": "x"}]}]},
            "mcpServers": {"docs-editor": {"command": "node"}, "slack": {"url": "https://mcp.slack.com"}}}
    safe = gemini_settings(safe=True, user=user)
    assert safe["skills"] == {"enabled": False}
    assert set(safe["hooksConfig"]["disabled"]) == {"sh ~/.notes-app/hook.sh", "notes"}
    assert set(safe["mcp"]["excluded"]) == {"docs-editor", "slack"}
    assert "hidee-checkpoint" not in safe["hooksConfig"]["disabled"]
    m = SessionManager(tmp_path, "http://127.0.0.1:8770", "t", gemini="gemini")
    assert " -e none" in m.argv_for("gemini", tmp_path / "s.json", safe=True)[4]


def test_user_shell_falls_back_when_shell_is_unset_or_missing(monkeypatch, tmp_path):
    import os
    from hidee import sessions
    monkeypatch.setenv("SHELL", str(tmp_path / "no-such-shell"))
    monkeypatch.setattr(sessions, "_account_shell", lambda: None)
    shell = sessions.user_shell()
    assert os.access(shell, os.X_OK) and shell != str(tmp_path / "no-such-shell")
    monkeypatch.setenv("SHELL", "/bin/sh")
    assert sessions.user_shell() == "/bin/sh"
