"""Hidee's own window: chosen when it can open, the browser otherwise; pages opened beside the workspace become more
Hidee windows. No window opens in these tests."""
import builtins
import types

import hidee.cli as cli
from hidee import desktop


def test_without_pywebview_there_is_no_window(monkeypatch):
    real_import = builtins.__import__

    def no_webview(name, *a, **kw):
        if name == "webview":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_webview)
    assert desktop.available() is False


def test_pages_opened_beside_the_workspace_become_hidee_windows():
    made = []

    class Window:
        def __init__(self):
            self.events = types.SimpleNamespace(loaded=Hooks())

    class Hooks(list):
        def __iadd__(self, fn):
            self.append(fn)
            return self
    fake = types.SimpleNamespace(create_window=lambda title, url, **kw: made.append((title, url)) or Window())
    desktop.Api(fake).open("http://127.0.0.1:8770/behind/s1", "Behind the agent")
    assert made == [("Hidee · Behind the agent", "http://127.0.0.1:8770/behind/s1")]
    assert "a[target]" in desktop.OPEN_IN_HIDEE and "url.origin !== location.origin" in desktop.OPEN_IN_HIDEE


def test_the_app_icon_ships_with_the_package():
    assert desktop.ICON.exists() and desktop.ICON.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _run(monkeypatch, tmp_path, window_ok, **kw):
    """Start run_app with the server and the window replaced by recorders."""
    seen = {"window": [], "browser": [], "served": 0}
    monkeypatch.setattr(cli, "updates_before_start", lambda home: False)
    monkeypatch.setattr(desktop, "available", lambda: window_ok)
    monkeypatch.setattr(desktop, "open_window", lambda url: seen["window"].append(url))

    class Server:
        def __init__(self, config):
            self.started, self.should_exit = False, False

        def run(self):                                      # like uvicorn: serve until told to stop
            import time
            seen["served"] += 1
            self.started = True
            while not self.should_exit:
                time.sleep(0.01)
    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.__setitem__("served", seen["served"] + 1))
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: seen["browser"].append(url))
    cli.run_app(tmp_path / "home", 8790, True, **kw)
    return seen


def test_hidee_app_opens_its_own_window_when_it_can(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, window_ok=True)
    assert seen["window"] == ["http://127.0.0.1:8790/workspace"] and seen["served"] == 1


def test_hidee_app_falls_back_to_the_browser(monkeypatch, tmp_path):
    import time
    seen = _run(monkeypatch, tmp_path, window_ok=False)
    time.sleep(1.2)                                         # the browser opens a moment after the server starts
    assert seen["window"] == [] and seen["browser"] == ["http://127.0.0.1:8790/workspace"]


def test_browser_flag_skips_the_window(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, window_ok=True, browser=True)
    assert seen["window"] == []


def test_a_busy_port_is_explained(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(cli, "updates_before_start", lambda home: False)
    monkeypatch.setattr(cli, "port_free", lambda port: False)
    with pytest.raises(SystemExit, match="Port 8790 is in use"):
        cli.run_app(tmp_path / "home", 8790, True)
