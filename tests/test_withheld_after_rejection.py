"""After a rejection, withheld memory remembers only what the rejection withheld: the categories it names as not
needed (never the needed details, destinations, or details the user's request names); a rejection that names no
detail remembers the destination, not the content."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES

TO = "referrals@surgery.test"
PHONE = "917-555-0142"
OTHER = "owner.c.91aa@example.org"


def make(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    return TestClient(app), app


def call(client, app, tool_input, decide=None):
    """Send one tool call; decide it with `decide(action_id)` if it waits; return (hook reply, ledger decision)."""
    out = {}

    def run():
        out["r"] = client.post("/hook/pretooluse", json={"tool_name": "mcp__office__send_email", "tool_input": tool_input},
                               headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()
    t = threading.Thread(target=run)
    t.start()
    for _ in range(100):
        if "r" in out:
            break
        w = app.state.store.list_actions(status="waiting")
        if w and decide:
            decide(w[0].id)
            break
        time.sleep(0.02)
    t.join()
    return out["r"]


def reject(app, withhold, reason):
    """The person rejects with a reason; `withhold` lists the categories the rejection names as not needed."""
    return lambda i: app.state.engine.decide(i, "reject", note=reason, withhold=withhold)


def approve(app):
    return lambda i: app.state.engine.decide(i, "approve")


def memory_blocked(reply) -> bool:
    return "held back earlier" in json.dumps(reply)


def first_reject(client, app, withhold, reason):
    call(client, app, {"to": TO, "text": f"Referral for Ann. Call {PHONE}. Copy {OTHER}."},
         reject(app, withhold, reason))


def test_a_corrected_resend_that_keeps_needed_details_passes(tmp_path):
    client, app = make(tmp_path)
    first_reject(client, app, ["email"], "please resend without the email address")
    r = call(client, app, {"to": TO, "text": f"Referral for Ann. Call {PHONE}."}, approve(app))
    assert not memory_blocked(r)
    assert r["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_b_a_resend_that_still_carries_a_named_detail_is_stopped(tmp_path):
    client, app = make(tmp_path)
    first_reject(client, app, ["email"], "please resend without the email address")
    r = call(client, app, {"to": TO, "text": f"Referral for Ann. Call {PHONE}. Copy {OTHER}."}, approve(app))
    assert memory_blocked(r)


def test_c_a_named_detail_in_another_form_is_caught(tmp_path):
    client, app = make(tmp_path)
    first_reject(client, app, ["email"], "please resend without the email address")
    r = call(client, app, {"to": TO, "text": "Referral for Ann. Copy owner.c.91aa at example dot org."}, approve(app))
    assert memory_blocked(r)


def test_d_a_rejection_naming_no_detail_remembers_the_destination_only(tmp_path):
    client, app = make(tmp_path)
    first_reject(client, app, [], "this destination is not one I asked you to send to")
    r = call(client, app, {"to": "front.desk@clinic.test", "text": f"Referral for Ann. Call {PHONE}. Copy {OTHER}."},
             approve(app))
    assert not memory_blocked(r)                                   # the content is not withheld
    r = call(client, app, {"to": TO, "text": "Referral for Ann."}, approve(app))
    assert memory_blocked(r)                                       # the rejected destination is


def test_ledger_logs_the_withheld_categories_only(tmp_path):
    client, app = make(tmp_path)
    first_reject(client, app, ["email"], "please resend without the email address")
    recs = [json.loads(l) for p in (tmp_path / "evidence").glob("*.jsonl") for l in p.read_text().splitlines()]
    dec = next(r["data"] for r in recs if r["event"] == "decision" and r["data"].get("choice") == "reject")
    assert dec["withheld"] == ["email"] and OTHER not in json.dumps(dec)


def test_a_person_reason_naming_a_category_and_a_plain_rejection(tmp_path):
    client, app = make(tmp_path)
    call(client, app, {"to": TO, "text": f"Referral for Ann. Call {PHONE}. Copy {OTHER}."},
         lambda i: app.state.engine.decide(i, "reject", note="leave out the phone number"))
    assert app.state.withheld.match("s1", "ring 917 555 0142") is not None
    assert app.state.withheld.match("s1", f"write {OTHER}") is None
    client2, app2 = make(tmp_path / "plain")
    call(client2, app2, {"to": TO, "text": f"Referral for Ann. Call {PHONE}."},
         lambda i: app2.state.engine.decide(i, "reject"))
    assert app2.state.withheld.match("s1", f"to {TO}") is None      # destinations are never remembered as content
    assert app2.state.withheld.match("s1", "917.555.0142") is not None
