import threading
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES


def make(tmp_path, **kw):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), **kw)
    return TestClient(app), app


def call(client, app, tool, tool_input, token=None):
    return client.post("/hook/pretooluse", json={"tool_name": tool, "tool_input": tool_input,
                                                 "hook_event_name": "PreToolUse"},
                       headers={"X-Hidee-Token": token or app.state.hook_token, "X-Hidee-Session": "s1"})


def decide_when_waiting(app, choice):
    def run():
        for _ in range(200):
            waiting = app.state.store.list_actions(status="waiting")
            if waiting:
                app.state.engine.decide(waiting[0].id, choice)
                return
            time.sleep(0.02)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_app_rules_parse():
    from hidee.rules import from_dict
    assert from_dict(yaml.safe_load(APP_RULES)).rules


def test_needs_the_hook_token(tmp_path):
    client, app = make(tmp_path)
    assert call(client, app, "WebSearch", {"query": "x"}, token="wrong").status_code == 401


def test_routes_exist_only_in_app_mode(tmp_path):
    client = TestClient(create_app(tmp_path))
    assert client.post("/hook/pretooluse", json={}).status_code == 404


def test_plain_and_local_calls_get_no_opinion(tmp_path):
    client, app = make(tmp_path)
    assert call(client, app, "WebSearch", {"query": "bagels near me"}).json() == {}
    assert call(client, app, "Read", {"file_path": "/etc/hosts"}).json() == {}
    rec = app.state.store.list_actions()[0]
    assert rec.agent_id == "session:s1" and rec.status == "allowed"


def test_shorter_version_rewrites_the_input(tmp_path):
    client, app = make(tmp_path)
    t = decide_when_waiting(app, "minimal")
    out = call(client, app, "WebSearch", {"query": "reviews of dr. kim, I am bob@x.io"}).json()
    t.join()
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "allow"
    assert hso["updatedInput"] == {"query": "reviews of dr. kim, I am [email]"}
    assert "email address" in hso["additionalContext"] and "I am [email]" in hso["additionalContext"]


def test_approve_allows_as_written(tmp_path):
    client, app = make(tmp_path)
    t = decide_when_waiting(app, "approve")
    hso = call(client, app, "WebFetch", {"url": "https://a.example.com/?u=bob@x.io", "prompt": "p"}).json()[
        "hookSpecificOutput"]
    t.join()
    assert hso["permissionDecision"] == "allow" and "updatedInput" not in hso


def test_reject_denies_with_a_reason(tmp_path):
    client, app = make(tmp_path)
    t = decide_when_waiting(app, "reject")
    hso = call(client, app, "WebSearch", {"query": "bob@x.io"}).json()["hookSpecificOutput"]
    t.join()
    assert hso["permissionDecision"] == "deny" and "Rejected" in hso["permissionDecisionReason"]
    assert "It contained: email address." in hso["permissionDecisionReason"]


def test_forbidden_content_is_denied_at_once(tmp_path):
    client, app = make(tmp_path)
    hso = call(client, app, "WebSearch", {"query": "is AKIAIOSFODNN7EXAMPLE valid"}).json()["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and "API key" in hso["permissionDecisionReason"]


def test_no_review_in_time_denies(tmp_path):
    client, app = make(tmp_path, review_timeout_s=0.3)
    hso = call(client, app, "WebSearch", {"query": "bob@x.io"}).json()["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and "not sent" in hso["permissionDecisionReason"]



def test_plain_messages_go_through_but_extra_details_wait(tmp_path):
    client, app = make(tmp_path, message_policy="off")      # the rules alone decide (messages are held by default)
    send = 'echo "repos are clean" | mail -s Summary ann@y.org'
    assert call(client, app, "Bash", {"command": send}).json() == {}
    t = decide_when_waiting(app, "reject")
    out = call(client, app, "Bash", {"command": 'echo "call me at 917-555-0142" | mail ann@y.org'}).json()
    t.join()
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
