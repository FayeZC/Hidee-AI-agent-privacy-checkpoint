"""Starting a session should be easy: a new project or an existing folder, then how to sign in."""
from pathlib import Path

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.keys import KeyStore, MemoryBackend
from hidee.workspace import project_folder


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path / "home", app_mode=True, allowed_hosts=("testserver",), claude="/bin/sh")
    app.state.keys = KeyStore(MemoryBackend())
    app.state.projects_dir = tmp_path / "Hidee Projects"
    started = []
    real = app.state.sessions.create

    def spy(name, cwd, kind="claude", argv=None, **kw):
        started.append({"name": name, "cwd": cwd, "kind": kind, **kw})
        return real(name, cwd, "shell", argv=["/bin/sh", "-c", "sleep 0.1"])
    app.state.sessions.create = spy
    return TestClient(app), app, started


def form(app, **kw):
    return {"csrf": app.state.csrf, "kind": "claude", "signin": "terminal", **kw}


def test_project_folder_names_are_safe(tmp_path):
    assert project_folder(tmp_path, "My Essay: draft 2") == tmp_path / "My Essay draft 2"
    assert project_folder(tmp_path, "../../etc") == tmp_path / "etc"
    assert project_folder(tmp_path, "   ") == tmp_path / "Untitled project"


def test_new_project_creates_its_folder(tmp_path):
    client, app, started = make(tmp_path)
    r = client.post("/sessions", data=form(app, where="new", project="Lab 1"), follow_redirects=False)
    assert r.status_code == 303
    folder = tmp_path / "Hidee Projects" / "Lab 1"
    assert folder.is_dir() and started[0]["cwd"] == str(folder) and started[0]["name"] == "Lab 1"
    app.state.sessions.close_all()


def test_open_an_existing_folder(tmp_path):
    client, app, started = make(tmp_path)
    work = tmp_path / "thesis"
    work.mkdir()
    client.post("/sessions", data=form(app, where="open", cwd=str(work)))
    assert started[0]["cwd"] == str(work) and started[0]["name"] == "thesis"
    app.state.sessions.close_all()


def test_open_a_missing_folder_is_explained(tmp_path):
    client, app, _ = make(tmp_path)
    r = client.post("/sessions", data=form(app, where="open", cwd=str(tmp_path / "nope")))
    assert r.status_code == 400 and "is not a folder" in r.text


def test_sign_in_with_a_saved_key(tmp_path):
    client, app, started = make(tmp_path)
    app.state.keys.save("anthropic", "sk-ant-api03-test-1111")
    work = tmp_path / "w"
    work.mkdir()
    client.post("/sessions", data=form(app, where="open", cwd=str(work), signin="key"))
    assert started[0]["env"] == {"HIDEE_KEY": "sk-ant-api03-test-1111"}
    assert "ANTHROPIC_API_KEY" in started[0]["prefix"]
    app.state.sessions.close_all()


def test_sign_in_with_a_key_that_is_not_saved(tmp_path):
    client, app, _ = make(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    r = client.post("/sessions", data=form(app, where="open", cwd=str(work), signin="key"))
    assert r.status_code == 400 and "Settings" in r.text


def test_start_panel_offers_both_ways_and_the_saved_key(tmp_path):
    client, app, _ = make(tmp_path)
    app.state.keys.save("anthropic", "sk-ant-api03-test-2222")
    page = client.get("/workspace?new=1").text
    for text in ("New project", "Open a folder", "Sign in the way my terminal does", "My saved key", "Anthropic ••••2222 for Claude Code",
                 "Show what happens behind the agent"):
        assert text in page, text


def test_key_prefix_reaches_the_shell_command(tmp_path):
    from hidee.sessions import SessionManager
    m = SessionManager(tmp_path, "http://x", "t", claude="claude")
    argv = m.argv_for("claude", tmp_path / "s.json", prefix='export A="$HIDEE_KEY"; ')
    assert argv[4].startswith('export A="$HIDEE_KEY"; exec claude --settings')


def test_folder_picker_needs_the_form_token(tmp_path):
    client, _, _ = make(tmp_path)
    assert client.post("/pick-folder", data={"csrf": "wrong"}).status_code == 403


def test_safe_mode_is_offered_on_by_default_and_reaches_the_session(tmp_path):
    client, app, started = make(tmp_path)
    page = client.get("/workspace?new=1").text
    assert 'name="safe" value="1" checked' in page and "Safe mode" in page
    work = tmp_path / "w"
    work.mkdir()
    client.post("/sessions", data=form(app, where="open", cwd=str(work), safe="1"))
    client.post("/sessions", data=form(app, where="open", cwd=str(work)))
    assert started[0].get("safe") is True and "safe" not in started[1]
    app.state.sessions.close_all()


def test_safe_mode_without_the_macos_sandbox_is_explained(tmp_path, monkeypatch):
    import hidee.workspace as workspace
    real_which = workspace.shutil.which
    monkeypatch.setattr(workspace.shutil, "which", lambda p: None if p == "sandbox-exec" else real_which(p))
    client, app, started = make(tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    for kind, name in (("gemini", "Gemini CLI"), ("codex", "Codex CLI")):
        r = client.post("/sessions", data=form(app, kind=kind, where="open", cwd=str(work), safe="1"))
        assert r.status_code == 400 and "macOS sandbox" in r.text and name in r.text and not started
    app.state.sessions.close_all()
