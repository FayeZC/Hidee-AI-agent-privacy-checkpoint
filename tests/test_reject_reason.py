"""A rejection can carry a short reason that the agent is told, written on the card's confirmation page.
The ledger's decision record logs the reason as written; without one, nothing changes."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.engine import NOTE_MAX, clean_note
from hidee.hook_api import APP_RULES

TO = "ann.b.7f2k@example.org"
PHONE = "917-555-0142"


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    return TestClient(app), app


def held_call(client, app, decide):
    out = {}

    def run():
        out["reply"] = client.post("/hook/pretooluse", json={"tool_name": "mcp__office__send_email",
                                                             "tool_input": {"to": TO, "text": f"call {PHONE}"}},
                                   headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()
    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        w = app.state.store.list_actions(status="waiting")
        if w:
            decide(w[0].id)
            break
        time.sleep(0.02)
    t.join()
    return out["reply"]


def ledger(tmp_path):
    return [json.loads(l) for p in (tmp_path / "evidence").glob("*.jsonl") for l in p.read_text().splitlines()]


def test_reject_with_reason_reaches_the_agent_and_the_ledger(tmp_path):
    client, app = make(tmp_path)
    reason = "please resend without the phone number"
    reply = held_call(client, app, lambda i: app.state.engine.decide(i, "reject", note=reason))
    out = reply["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert f"Rejected by the user: {reason}" in out["permissionDecisionReason"]
    dec = next(r["data"] for r in ledger(tmp_path) if r["event"] == "decision")
    assert dec["choice"] == "reject" and dec["note"] == reason and dec["by"] == "person"


def test_reject_without_reason_is_unchanged(tmp_path):
    client, app = make(tmp_path)
    reply = held_call(client, app, lambda i: app.state.engine.decide(i, "reject"))
    assert "Rejected by owner." in reply["hookSpecificOutput"]["permissionDecisionReason"]
    assert "note" not in next(r["data"] for r in ledger(tmp_path) if r["event"] == "decision")


def test_reason_is_ignored_for_other_choices(tmp_path):
    client, app = make(tmp_path)
    held_call(client, app, lambda i: app.state.engine.decide(i, "approve", note="ignored"))
    assert "note" not in next(r["data"] for r in ledger(tmp_path) if r["event"] == "decision")


def test_card_confirmation_page_has_the_field_and_the_form_passes_it(tmp_path):
    client, app = make(tmp_path)

    def decide(i):
        page = client.get(f"/confirm/{i}?choice=reject").text
        assert "Tell the agent why" in page and 'name="note"' in page
        assert "Tell the agent why" not in client.get(f"/confirm/{i}?choice=approve").text
        client.post(f"/decide/{i}", data={"choice": "reject", "csrf": app.state.csrf,
                                          "note": "  not the phone,\nplease  "}, follow_redirects=False)
    reply = held_call(client, app, decide)
    assert "Rejected by the user: not the phone, please" in reply["hookSpecificOutput"]["permissionDecisionReason"]
    dec = next(r["data"] for r in ledger(tmp_path) if r["event"] == "decision")
    assert dec["by"] == "person" and dec["note"] == "not the phone, please"


def test_reason_is_one_short_line():
    assert clean_note("a\nb\tc\x07") == "a b c"
    assert len(clean_note("x" * 1000)) == NOTE_MAX
