"""The one shorter version removes sensitive details in the content but keeps the address the call is sent to."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.toolmap import RECIPIENT_KEYS, is_recipient_detail, map_call

TO = "ann.b.7f2k@example.org"
OTHER = "owner.c.91aa@example.org"
PHONE = "917-555-0142"


def test_mcp_send_email_keeps_to_and_removes_body_details():
    m = map_call("mcp__office__send_email", {"to": TO, "subject": "Reminder",
                                             "text": f"Hi Ann, call {PHONE} or write to {OTHER}"})
    assert m.minimal["to"]["value"] == TO
    assert m.minimal["text"]["value"] == "Hi Ann, call [phone] or write to [email]"
    assert sorted(m.default_remove()) == ["text: email address", "text: phone number"]


def test_destination_keys_in_structured_and_text_inputs():
    assert {"to", "cc", "bcc", "channel", "email", "recipient"} <= RECIPIENT_KEYS
    m = map_call("mcp__chat__post_message", {"channel": OTHER, "text": f"call {PHONE}"})
    assert m.minimal["channel"]["value"] == OTHER and "[phone]" in m.minimal["text"]["value"]
    m = map_call("mcp__billing__create_invoice", {"email": TO, "memo": f"student born 2011, parent {OTHER}"})
    assert m.minimal["email"]["value"] == TO and "[email]" in m.minimal["memo"]["value"]
    cmd = (f"curl -X POST -H 'Content-Type: application/json' -d '{{\"to\": \"{TO}\", \"text\": \"ring {PHONE}, "
           f"cc {OTHER}\"}}' http://api.mailrelay.test/v1/messages")
    m = map_call("Bash", {"command": cmd})
    out = m.minimal["command"]["value"]
    assert TO in out and PHONE not in out and OTHER not in out and "REDACTED_PHONE" in out
    form = map_call("Bash", {"command": f"curl -d to={TO} -d note={PHONE} http://api.mailrelay.test/send"})
    assert TO in form.minimal["command"]["value"] and PHONE not in form.minimal["command"]["value"]
    assert is_recipient_detail(("x",), '"cc": "', len('"cc": "'))


def test_search_and_fetch_content_is_still_removed():
    assert map_call("WebSearch", {"query": f"dentist for {TO}"}).minimal["query"]["value"] == "dentist for [email]"
    f = map_call("WebFetch", {"url": f"https://a.example.com/?q={PHONE}", "prompt": "p"})
    assert PHONE not in f.minimal["url"]["value"]


def test_when_every_detail_is_a_destination_the_shorter_version_removes_them():
    m = map_call("mcp__office__send_email", {"to": TO, "text": "Hello"})
    assert m.minimal["to"]["value"] == "[email]"          # otherwise the shorter version would equal the original


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    return TestClient(app), app


def shorten(client, app, tool, tool_input, session="s1"):
    def decide():
        for _ in range(200):
            w = app.state.store.list_actions(status="waiting")
            if w:
                app.state.engine.decide(w[0].id, "minimal")
                return
            time.sleep(0.02)
    t = threading.Thread(target=decide)
    t.start()
    out = client.post("/hook/pretooluse", json={"tool_name": tool, "tool_input": tool_input},
                      headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": session}).json()
    t.join()
    return out


def test_ledger_lists_removed_categories_and_kept_destination_without_values(tmp_path):
    client, app = make(tmp_path)
    out = shorten(client, app, "mcp__office__send_email", {"to": TO, "text": f"call {PHONE}"})
    assert out["hookSpecificOutput"]["updatedInput"] == {"to": TO, "text": "call [phone]"}
    recs = [json.loads(l) for p in (tmp_path / "evidence").glob("*.jsonl") for l in p.read_text().splitlines()]
    ex = next(r["data"] for r in recs if r["event"] == "executed")
    assert ex["removed"] == ["phone"] and ex["kept_destination"] == ["email"]
    assert "destination fields" in ex["note"]
    assert PHONE not in json.dumps({k: v for k, v in ex.items() if k != "input"})


def test_withheld_memory_ignores_the_kept_destination(tmp_path):
    client, app = make(tmp_path)
    shorten(client, app, "mcp__office__send_email", {"to": TO, "text": f"call {PHONE}"})
    assert app.state.withheld.match("s1", f"another note to {TO}") is None        # kept: not withheld
    assert app.state.withheld.match("s1", "ring 917 555 0142") is not None         # removed: withheld


def test_card_marks_only_removed_details(tmp_path):
    client, app = make(tmp_path)
    t = threading.Thread(target=lambda: client.post(
        "/hook/pretooluse", json={"tool_name": "mcp__office__send_email", "tool_input": {"to": TO, "text": f"call {PHONE}"}},
        headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}))
    t.start()
    for _ in range(200):
        if app.state.store.list_actions(status="waiting"):
            break
        time.sleep(0.02)
    page = client.get("/?embed=1").text
    assert page.count("removed in shorter version") == 1          # the phone number, not the address it goes to
    app.state.engine.decide(app.state.store.list_actions(status="waiting")[0].id, "reject")
    t.join()


def test_a_contact_line_in_prose_is_content_not_a_destination():
    m = map_call("mcp__office__send_email", {"to": TO, "text": f"Patient: Ann B.\nEmail: {OTHER}\nPhone: {PHONE}"})
    assert OTHER not in m.minimal["text"]["value"] and PHONE not in m.minimal["text"]["value"]
    assert m.minimal["to"]["value"] == TO
