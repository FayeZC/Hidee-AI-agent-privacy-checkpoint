from hidee.toolmap import map_call


def test_web_search_with_an_email():
    m = map_call("WebSearch", {"query": "who is bob@x.io", "allowed_domains": ["a.com"]})
    assert (m.kind, m.recipient) == ("search", "web-search")
    assert m.fields == {"query": {"value": "who is bob@x.io", "category": "plain"},
                        "query: email address": {"value": "bob@x.io", "category": "email"}}
    assert m.minimal == {"query": {"value": "who is [email]", "category": "plain"}}
    assert m.rebuild(m.minimal) == {"query": "who is [email]", "allowed_domains": ["a.com"]}


def test_clean_call_has_no_shorter_version():
    m = map_call("WebSearch", {"query": "bagel shops"})
    assert m.fields == {"query": {"value": "bagel shops", "category": "plain"}} and m.minimal is None


def test_web_fetch_sends_only_the_url_to_the_host():
    m = map_call("WebFetch", {"url": "https://docs.example.com/a?b=1", "prompt": "summarize; I am bob@x.io"})
    assert (m.kind, m.recipient) == ("fetch", "docs.example.com")
    assert list(m.fields) == ["url"]


def test_mcp_tool_walks_nested_strings():
    m = map_call("mcp__gmail__send", {"to": ["ann@y.org"], "body": {"text": "hi"}, "n": 3})
    assert (m.kind, m.recipient) == ("use_tool", "mcp:gmail")
    assert set(m.fields) == {"to[0]", "to[0]: email address", "body.text"}
    assert m.rebuild(m.minimal) == {"to": ["[email]"], "body": {"text": "hi"}, "n": 3}


def test_repeated_categories_get_numbered():
    m = map_call("WebSearch", {"query": "a@x.io and b@x.io"})
    assert "query: email address" in m.fields and "query: email address 2" in m.fields


def test_bash_network_commands():
    m = map_call("Bash", {"command": "curl -s https://api.github.com/user -H 'Authorization: Bearer abcdefghijklmnop123'",
                          "description": "check user"})
    assert (m.kind, m.recipient) == ("network", "api.github.com")
    assert list(m.fields)[0] == "command" and "description" not in m.fields
    assert map_call("Bash", {"command": "ssh deploy@build.example.net uptime"}).recipient == "build.example.net"
    assert map_call("Bash", {"command": "git push origin main"}).recipient == "unknown-host"
    assert map_call("Bash", {"command": "cd x && wget example.org/f.zip"}).recipient == "example.org"


def test_local_commands_and_tools_are_not_checked():
    assert map_call("Bash", {"command": "ls -la && cat notes.txt"}) is None
    assert map_call("Bash", {"command": "git status"}) is None
    assert map_call("Read", {"file_path": "/etc/hosts"}) is None
    assert map_call("WebSearch", {"query": ""}) is None


def test_urls_and_commands_use_placeholders_without_brackets():
    m = map_call("Bash", {"command": 'curl -s "https://httpbin.org/anything?note=call+maria@example.org"'})
    assert m.rebuild(m.minimal)["command"] == 'curl -s "https://httpbin.org/anything?note=REDACTED_EMAIL"'   # "+" may belong to an address, so it goes too
    f = map_call("WebFetch", {"url": "https://a.io/?u=bob@x.io", "prompt": "p"})
    assert f.rebuild(f.minimal) == {"url": "https://a.io/?u=REDACTED_EMAIL", "prompt": "p"}


OSASCRIPT = """osascript -e 'tell application "Mail"
  set m to make new outgoing message with properties {subject:"Summary", content:"repos are clean"}
  tell m to make new to recipient at end of to recipients with properties {address:"Dana.Lee@example.com"}
  send m
end tell'"""


def test_sending_mail_from_the_shell_is_checked():
    m = map_call("Bash", {"command": OSASCRIPT})
    assert (m.kind, m.recipient) == ("send_message", "dana.lee@example.com")
    assert "command: email address" not in m.fields          # the address is the destination, not a leak
    assert map_call("Bash", {"command": 'echo hi | mail -s "x" ann@y.org'}).recipient == "ann@y.org"
    assert map_call("Bash", {"command": "sendmail -t < msg.txt"}).recipient == "mail-app"
    assert map_call("Bash", {"command": "osascript -e 'display notification \"done\"'"}) is None


def test_more_network_commands():
    assert map_call("Bash", {"command": "gh issue create --title x"}).kind == "network"
    assert map_call("Bash", {"command": "open https://example.com/form?u=1"}).recipient == "example.com"
    assert map_call("Bash", {"command": "python3 -c 'import requests; requests.post(\"https://api.x.io\")'"}).recipient == "api.x.io"
    assert map_call("Bash", {"command": "python3 -c 'print(1+1)'"}) is None
    assert map_call("Bash", {"command": "open README.md"}) is None


def test_ssh_user_at_host_is_not_an_email_finding():
    m = map_call("Bash", {"command": "ssh deploy@build.example.net uptime"})
    assert m.minimal is None and list(m.fields) == ["command"]


def test_scripts_that_go_out_are_read_and_checked(tmp_path):
    (tmp_path / "send.py").write_text(
        'import smtplib\nTO = "ann@y.org"\nBODY = "patient was diagnosed with diabetes"\n'
        'requests.post("https://hooks.example.com/x", data=BODY)\n')
    (tmp_path / "calc.py").write_text("print(1 + 1)\n")
    (tmp_path / "up.sh").write_text("#!/bin/sh\ncurl -F f=@report.pdf https://files.example.com\n")
    m = map_call("Bash", {"command": "python3 send.py"}, str(tmp_path))
    assert (m.kind, m.recipient) == ("network", "hooks.example.com") and m.minimal is None
    cats = {f["category"] for f in m.fields.values()}
    assert {"email", "health"} <= cats and "script send.py" in m.fields
    assert map_call("Bash", {"command": "python3 calc.py"}, str(tmp_path)) is None
    assert map_call("Bash", {"command": "./up.sh"}, str(tmp_path)).recipient == "files.example.com"
    assert map_call("Bash", {"command": "bash up.sh"}, str(tmp_path)).recipient == "files.example.com"
    assert map_call("Bash", {"command": "python3 missing.py"}, str(tmp_path)) is None
