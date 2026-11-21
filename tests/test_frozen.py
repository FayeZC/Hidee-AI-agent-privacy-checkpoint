"""The Hidee.app entry point: hook commands, the command line, and a double click each go to the right place."""
import io
import json

import pytest

from hidee import frozen


def test_hook_commands_run_the_hooks(monkeypatch):
    calls = []
    import hidee.hook as hook, hidee.learn_hook as learn
    monkeypatch.setattr(hook, "main", lambda after=False: calls.append(("hook", after)) or 0)
    monkeypatch.setattr(learn, "main", lambda: calls.append(("learn",)) or 0)
    assert frozen.main(["-m", "hidee.hook"]) == 0
    assert frozen.main(["-m", "hidee.hook", "--after"]) == 0           # Gemini's AfterTool note
    assert frozen.main(["-m", "hidee.learn_hook"]) == 0
    assert calls == [("hook", False), ("hook", True), ("learn",)]
    assert frozen.main(["-m", "something.else"]) == 2


def test_a_command_goes_to_the_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr("hidee.cli.main", lambda argv: seen.append(argv))
    assert frozen.main(["evidence", "verify"]) == 0 and seen == [["evidence", "verify"]]


def test_a_double_click_opens_the_app_and_ignores_the_process_serial_number(monkeypatch):
    opened = []
    monkeypatch.setattr(frozen, "open_app", lambda: opened.append(True) or 0)
    assert frozen.main(["-psn_0_12345"]) == 0 and opened == [True]


def test_a_second_launch_opens_a_window_onto_the_running_hidee(monkeypatch, tmp_path):
    import hidee.cli as cli
    from hidee import desktop
    windows = []
    monkeypatch.setenv("HIDEE_APP_PORT", "8791")
    monkeypatch.setattr(cli, "port_free", lambda port: False)
    monkeypatch.setattr(frozen, "hidee_running", lambda port: True)
    monkeypatch.setattr(desktop, "open_window", lambda url: windows.append(url))
    assert frozen.open_app() == 0 and windows == ["http://127.0.0.1:8791/workspace"]


def test_a_port_held_by_something_else_is_explained(monkeypatch):
    import hidee.cli as cli
    alerts = []
    monkeypatch.setattr(cli, "port_free", lambda port: False)
    monkeypatch.setattr(frozen, "hidee_running", lambda port: False)
    monkeypatch.setattr(frozen, "alert", lambda message: alerts.append(message))
    assert frozen.open_app() == 1 and "in use" in alerts[0]


def test_inside_the_app_an_update_points_to_the_download_page(monkeypatch, tmp_path):
    import sys
    from hidee.update import UpdateError, install
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(UpdateError, match="hidee.net/download"):
        install(tmp_path / "hidee-9.0.0-py3-none-any.whl")
