"""Messages to people: Hidee holds them and asks how they go out (send now, save as a draft, shorter, reject)."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.toolmap import is_draft_tool, is_person_message, map_call

TO = "ann.b.7f2k@example.org"


def make(tmp_path, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False, **kw)
    return TestClient(app), app


def hook(client, app, tool, tool_input):
    return client.post("/hook/pretooluse", json={"tool_name": tool, "tool_input": tool_input},
                       headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()


def decide_when_waiting(app, choice):
    def run():
        for _ in range(300):
            w = app.state.store.list_actions(status="waiting")
            if w:
                app.state.engine.decide(w[0].id, choice)
                return
            time.sleep(0.02)
    t = threading.Thread(target=run)
    t.start()
    return t


def records(tmp_path, event):
    return [json.loads(l)["data"] for p in (tmp_path / "evidence").glob("*.jsonl")
            for l in p.read_text().splitlines() if json.loads(l)["event"] == event]


def test_what_counts_as_a_message_to_a_person():
    assert is_person_message("mcp__office__send_email", map_call("mcp__office__send_email", {"to": TO, "text": "hi"}))
    assert is_person_message("mcp__chat__post_message", map_call("mcp__chat__post_message", {"channel": "#a", "text": "x"}))
    assert is_person_message("Bash", map_call("Bash", {"command": f"curl -d '{{\"to\": \"{TO}\", \"text\": \"x\"}}' "
                                                                  "http://api.mail.test/v1/messages"}))
    assert is_person_message("Bash", map_call("Bash", {"command": f'echo hi | mail -s s {TO}'}))
    assert not is_person_message("mcp__shop__submit_order", map_call("mcp__shop__submit_order", {"items": "flour"}))
    assert not is_person_message("WebSearch", map_call("WebSearch", {"query": "bagels"}))
    assert is_draft_tool("mcp__office__save_draft") and map_call("mcp__office__save_draft", {"to": TO, "text": "x"}) is None


def test_a_plain_message_waits_by_default_and_send_now_lets_it_go(tmp_path):
    client, app = make(tmp_path)
    t = decide_when_waiting(app, "approve")
    out = hook(client, app, "mcp__chat__post_message", {"channel": "#team", "text": "Back at 3pm."})
    t.join()
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert records(tmp_path, "decision")[-1]["choice"] == "approve"


def test_save_as_draft_sends_nothing_and_tells_the_agent(tmp_path):
    client, app = make(tmp_path)
    t = decide_when_waiting(app, "draft")
    out = hook(client, app, "mcp__office__send_email", {"to": TO, "subject": "Re: schedule", "text": "Friday works."})
    t.join()
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and "draft" in hso["permissionDecisionReason"]
    assert "not sent" in hso["permissionDecisionReason"]
    assert records(tmp_path, "decision")[-1]["choice"] == "draft"
    drafted = records(tmp_path, "drafted")[-1]
    assert drafted["input"]["text"] == "Friday works." and "nothing was sent" in drafted["note"]
    rec = app.state.store.list_actions()[0]
    assert rec.status == "drafted" and rec.executed is None
    assert app.state.withheld.match("s1", TO) is None                 # a draft holds nothing back


def test_draft_default_needs_no_person(tmp_path):
    client, app = make(tmp_path / "d", message_policy="draft")
    out = hook(client, app, "mcp__chat__post_message", {"channel": "#team", "text": "Back at 3pm."})
    assert "draft" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert records(tmp_path / "d", "decision")[-1]["by"] == "default"


def test_off_lets_the_rules_decide(tmp_path):
    client, app = make(tmp_path, message_policy="off")
    assert hook(client, app, "mcp__chat__post_message", {"channel": "#team", "text": "Back at 3pm."}) == {}


def test_card_offers_send_now_and_draft_and_shows_attribution(tmp_path):
    client, app = make(tmp_path)
    t = threading.Thread(target=lambda: hook(client, app, "mcp__office__send_email",
                                             {"to": TO, "text": "Friday works.\nSent with Claude Code"}))
    t.start()
    for _ in range(200):
        if app.state.store.list_actions(status="waiting"):
            break
        time.sleep(0.02)
    page = client.get("/?embed=1").text
    assert "Send it now" in page and "Save as a draft for me" in page
    assert "Sent with Claude Code" in page and "wording you did not write" in page
    rec = app.state.store.list_actions(status="waiting")[0]
    assert client.get(f"/confirm/{rec.id}?choice=draft").status_code == 200
    app.state.engine.decide(rec.id, "reject")
    t.join()
