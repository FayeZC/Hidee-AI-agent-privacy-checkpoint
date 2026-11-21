"""What reached a service comes from what actually ran. After a shorter version, the details it kept (the user's
request names them, or they are destination fields) show as reached and count in Who knows what; the removed ones
are struck. A rejection or a draft reached no one."""
import re
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.engine import reached_fields
from hidee.flow import RIGHT_ZONE
from hidee.hook_api import APP_RULES

TO = "maria.v.3k@example.org"
PORTAL = "maria.velk@example.net"
CLINIC = "212-555-0187"
TEXT = (f"Dear Maria, the clinic phone is {CLINIC} and your portal address is {PORTAL}. "
        "Your HIV test came back positive. You still owe $1,240.")
REQUEST = f"Email Maria her follow-up; include her address {PORTAL} and the clinic phone {CLINIC}."


def make(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    return TestClient(app), app


def post(client, app, path, body):
    return client.post(path, json=body, headers={"X-Hidee-Token": app.state.hook_token,
                                                 "X-Hidee-Session": "s1"}).json()


def send(client, app, choice, request=REQUEST):
    """The agent emails Maria; the person decides `choice` while the call waits. Returns the decided record."""
    post(client, app, "/hook/learn", {"hook_event_name": "UserPromptSubmit", "prompt": request})

    def decide():
        for _ in range(300):
            w = app.state.store.list_actions(status="waiting")
            if w:
                app.state.engine.decide(w[0].id, choice)
                return
            time.sleep(0.02)
    t = threading.Thread(target=decide)
    t.start()
    out = post(client, app, "/hook/pretooluse", {"tool_name": "mcp__office__send_email",
                                                 "tool_input": {"to": TO, "text": TEXT}})
    t.join()
    rec = app.state.store.list_actions()[0]
    return out, rec


def labels(svg: str, x: str | None = None) -> dict[str, bool]:
    """Each label with a mark in a picture (only those at `x`, if given) -> struck through or not."""
    out = {}
    for attrs, body in re.findall(r"<text([^>]*)>([^<]*)</text>", svg):
        text = re.sub(r"\s+", " ", body).strip().lower()
        if text and text[0] in "✓✕○⚠" and (x is None or f'x="{x}"' in attrs):
            out[text] = "line-through" in attrs
    return out


def pictures(page: str) -> tuple[dict, dict, str]:
    """The "Reached ..." side of Step N up close, the map's label list, and the Who knows what section."""
    flow = page.split('aria-label="Information flow for this case"', 1)[1].split('<p class="reason">', 1)[0]
    overview = page.split('aria-label="All agents', 1)[1].split('<p class="legend">', 1)[0]
    knows = page.split('id="knows"', 1)[1].split("Sensitive steps", 1)[0]
    return labels(flow, f"{RIGHT_ZONE:.0f}"), labels(overview), knows


def marked(chips: dict, word: str) -> list[tuple[str, bool]]:
    return [(text, struck) for text, struck in chips.items() if word in text]


def test_shorter_version_kept_details_reached_and_removed_ones_are_struck(tmp_path):
    client, app = make(tmp_path)
    out, rec = send(client, app, "minimal")
    ran = out["hookSpecificOutput"]["updatedInput"]["text"]
    assert CLINIC in ran and PORTAL in ran and "HIV" not in ran and "$1,240" not in ran
    assert rec.status == "minimized"
    assert {n for n in reached_fields(rec.fields, rec.executed)} == {
        "to", "to: email address", "text", "text: phone number", "text: email address"}

    chips, overview, knows = pictures(client.get(f"/dashboard?id={rec.id}").text)
    # Step N up close, "Reached ...": kept details reached, removed ones struck
    assert marked(chips, "phone") == [("✓ phone number", False)]
    assert marked(chips, "email") == [("✓ email address", False)]
    assert marked(chips, "health") and all(struck and t.startswith("✕") for t, struck in marked(chips, "health"))
    assert marked(chips, "money") == [("✕ money or pay", True)]
    # the map's label list: nothing the shorter version kept is struck
    struck = [t for t, s in overview.items() if s]
    assert not any(w in t for t in struck for w in ("phone", "email", "ordinary"))
    assert any(t.startswith("✓") for t, s in overview.items() if not s)
    # Who knows what: the kept kinds, not the removed ones
    assert "phone number" in knows and "email address" in knows
    assert "health" not in knows and "money" not in knows
    cats = {c for who, c, n in app.state.store.disclosure_counts()}
    assert {"phone", "email", "plain"} <= cats and not cats & {"health", "money"}
    # the step pop-up lists only what was held back
    step = client.get(f"/step/{rec.id}").text
    left = step.split("What was sent", 1)[1]
    assert "Your HIV test came back positive." in left and "$1,240" in left


def test_plain_approve_reached_everything(tmp_path):
    client, app = make(tmp_path)
    _, rec = send(client, app, "approve", request="Email Maria her follow-up.")
    assert rec.status == "allowed"
    assert reached_fields(rec.fields, rec.executed) == set(rec.fields)
    chips, overview, knows = pictures(client.get(f"/dashboard?id={rec.id}").text)
    assert chips and all(t.startswith("✓") and not struck for t, struck in chips.items())
    assert not any(s for t, s in overview.items())
    assert "health" in knows and "money" in knows and "phone number" in knows
    cats = {c for who, c, n in app.state.store.disclosure_counts()}
    assert {"health", "money", "phone", "email"} <= cats


def test_reject_and_draft_reached_no_one(tmp_path):
    for choice, status in (("reject", "blocked"), ("draft", "drafted")):
        client, app = make(tmp_path / choice)
        _, rec = send(client, app, choice)
        assert rec.status == status and rec.executed is None
        chips, overview, knows = pictures(client.get(f"/dashboard?id={rec.id}").text)
        assert chips and all(t.startswith("✕") and struck for t, struck in chips.items())
        assert all(t.startswith("✕") and struck for t, struck in overview.items())
        assert app.state.store.disclosure_counts() == []
        assert "Nothing has left yet" in knows


def test_reached_fields_rules():
    fields = {"text": {"value": "Call 917-555-0142 or a@b.org", "category": "plain"},
              "text: phone number": {"value": "917-555-0142", "category": "phone"},
              "text: email address": {"value": "a@b.org", "category": "email"},
              "note": {"value": "Away on Tuesdays.", "category": "routine"},
              "message": {"value": "Leave it at the back door.", "category": "delivery"}}
    sent = {"text": "Call 917-555-0142 or [email]", "message": "Leave it at the back door."}
    assert reached_fields(fields, sent) == {"text", "text: phone number", "message"}
    # a field that ran with a changed value no longer carries its sensitive kind
    assert reached_fields({"note": {"value": "Away on Tuesdays.", "category": "routine"}},
                          {"note": "[routine]"}) == set()
