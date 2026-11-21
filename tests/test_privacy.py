"""Hidee's own data stays on the user's computer and is readable only by them; Hidee calls no server of ours."""
import os
import socket
import stat

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.privacy import data_inventory, make_private


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_make_private_locks_the_folder_and_files(tmp_path):
    home = tmp_path / ".hidee" / "app"
    (home / "evidence").mkdir(parents=True)
    (home / "hidee.db").write_text("x")
    (home / "evidence" / "2026-11-06.jsonl").write_text("{}\n")
    os.chmod(home / "hidee.db", 0o644)
    make_private(home)
    assert mode(home) == 0o700 and mode(home / "evidence") == 0o700
    assert mode(home / "hidee.db") == 0o600 and mode(home / "evidence" / "2026-11-06.jsonl") == 0o600


def test_the_app_keeps_its_home_private(tmp_path):
    home = tmp_path / "home"
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    create_app(home, app_mode=True, allowed_hosts=("testserver",))
    assert mode(home) == 0o700 and mode(home / "hidee.db") == 0o600


def test_new_evidence_files_are_private(tmp_path):
    from hidee.evidence import EvidenceLog
    log = EvidenceLog(tmp_path / "ev")
    log.append("s1", "prompt", {"text": "hi"})
    assert mode(next((tmp_path / "ev").glob("*.jsonl"))) == 0o600


def test_starting_the_app_contacts_no_outside_server(tmp_path, monkeypatch):
    attempts = []
    real = socket.socket.connect

    def watch(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            attempts.append(address)
            raise OSError("blocked in test")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", watch)
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    # Even with the update check allowed and due, the app itself never checks: only `hidee app` in a terminal
    # does, every two weeks, and Settings when the user clicks "Check now".
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "update.json").write_text('{"check": true, "last_check": 0}')
    app = create_app(tmp_path / "home", app_mode=True, allowed_hosts=("testserver",))
    client = TestClient(app)
    for page in ("/workspace", "/workspace?new=1", "/knows", "/evidence", "/settings", "/dashboard"):
        assert client.get(page).status_code == 200, page
    assert attempts == []


def test_inventory_lists_what_hidee_keeps(tmp_path):
    home = tmp_path / "home"
    (home / "evidence").mkdir(parents=True)
    (home / "evidence" / "2026-11-06.jsonl").write_text("{}\n" * 10)
    (home / "hidee.db").write_text("x" * 100)
    rows = {r["what"]: r for r in data_inventory(home)}
    assert rows["Evidence log"]["bytes"] == 30 and rows["Evidence log"]["path"].endswith("evidence")
    assert rows["Checkpoint database"]["bytes"] == 100


def test_settings_shows_your_data(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path / "home", app_mode=True, allowed_hosts=("testserver",))
    page = TestClient(app).get("/settings").text
    for text in ("Your data", "Evidence log", "Checkpoint database", "Nothing is sent to us"):
        assert text in page, text


def test_folders_made_after_start_are_private(tmp_path):
    from hidee.evidence import EvidenceLog
    from hidee.sessions import SessionManager
    home = tmp_path / "home"
    EvidenceLog(home / "evidence")
    m = SessionManager(home, "http://x", "t")
    s = m.create("t", str(tmp_path), argv=["/bin/sh", "-c", "sleep 0.1"])
    try:
        assert mode(home / "evidence") == 0o700 and mode(home / "sessions") == 0o700
        folder = home / "sessions" / s.id
        assert mode(folder) == 0o700 and all(mode(f) == 0o600 for f in folder.iterdir())
    finally:
        m.close_all()
