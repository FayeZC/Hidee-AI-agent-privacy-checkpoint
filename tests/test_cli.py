from hidee.agents import authenticate
from hidee.cli import STARTER_RULES, init_home, main
from hidee.rules import load_rules
from hidee.store import Store


def test_init_home_writes_starter_rules_once(tmp_path):
    init_home(tmp_path)
    assert (tmp_path / "rules.yaml").read_text() == STARTER_RULES
    (tmp_path / "rules.yaml").write_text("agents: {}\n")
    init_home(tmp_path)
    assert (tmp_path / "rules.yaml").read_text() == "agents: {}\n"
    load_rules(tmp_path / "rules.yaml")


def test_starter_rules_are_valid(tmp_path):
    init_home(tmp_path)
    rs = load_rules(tmp_path / "rules.yaml")
    assert rs.rules == ()


def test_agent_add_prints_a_working_token(tmp_path, capsys):
    main(["agent", "add", "shop", "--name", "Shopping assistant", "--home", str(tmp_path)])
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert authenticate(Store(tmp_path / "hidee.db"), token)["name"] == "Shopping assistant"


def test_app_command_writes_app_rules_and_runs_on_localhost(tmp_path, monkeypatch):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    main(["app", "--home", str(tmp_path), "--port", "8799", "--no-browser"])
    from hidee.hook_api import APP_RULES
    assert (tmp_path / "rules.yaml").read_text() == APP_RULES
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8799
    assert seen["app"].state.app_mode and seen["app"].state.sessions.app_url == "http://127.0.0.1:8799"


def test_help_lists_only_the_product_commands(capsys):
    import pytest
    from hidee.cli import main
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for shown in ("app", "update", "evidence", "demo"):
        assert f"    {shown} " in out
    for hidden in ("serve", "web", "cloud-demo", "agent"):
        assert f"    {hidden} " not in out and hidden not in out.split("\n")[0]


def test_hidee_alone_welcomes_and_lists_the_commands(capsys, monkeypatch):
    from hidee import __version__
    from hidee.cli import main
    monkeypatch.setattr("builtins.input", lambda q: (_ for _ in ()).throw(AssertionError("asked without a terminal")))
    main([])                                                # stdin is not a terminal in tests: no question
    out = capsys.readouterr().out
    assert f"HIDEE  {__version__}" in out and "A privacy checkpoint for your AI agents" in out
    for command in ("hidee app", "hidee demo", "hidee evidence verify", "hidee update"):
        assert command in out


def test_the_welcome_offers_the_workspace_in_a_terminal(monkeypatch):
    import hidee.cli as cli
    opened = []
    monkeypatch.setattr(cli, "run_app", lambda home, port, browser: opened.append((port, browser)))
    cli.welcome(lambda q: "", interactive=True)             # Enter means yes
    cli.welcome(lambda q: "n", interactive=True)
    assert opened == [(8770, True)]


def test_the_startup_block_says_where_things_are(tmp_path):
    from hidee.welcome import banner, ready
    text = ready("http://127.0.0.1:8770/workspace", tmp_path, opening=True)
    assert "http://127.0.0.1:8770/workspace" in text and str(tmp_path) in text and "Ctrl-C" in text
    assert "\033[" not in banner(color=False) + text          # no colour codes off a terminal
    assert "\033[38;2;31;59;255m" in banner(color=True)       # the blue cell of the mark
