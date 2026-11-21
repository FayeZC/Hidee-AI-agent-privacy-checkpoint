"""Sensitive facts written as free text are highlighted with a category (locally, no model): special categories and
other sensitive facts attached to a person, and sentences that restate the user's designated sensitive files. The
shorter version removes the whole sentence unless the user's request names it; the ledger records categories only."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee import freetext
from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.known import KnownData
from hidee.toolmap import map_call

TO = "ann.b.7f2k@example.org"


def cats(text, known=None):
    return [f.category for f in freetext.find(text, known)]


def test_a_fact_about_a_person_is_highlighted_and_a_general_statement_is_not():
    assert cats("Jane is being treated for depression.") == ["mental_health"]
    assert cats("Depression is common in winter.") == []
    assert cats("Cancer research funding grew in 2025.") == []
    assert cats("Tom's chemo starts Monday.") == ["health"]


def test_categories():
    assert cats("Omar goes to the mosque on Fridays with his brother.") == ["religion"]
    assert cats("He was arrested last year for a DUI.") == ["legal"]
    assert cats("My husband filed for bankruptcy in May.") == ["finances"]
    assert cats("Her family is undocumented and facing deportation.") == ["immigration"]
    assert cats("Lena is pregnant and due in June.") == ["family"]
    assert cats("Mark came out as gay to his parents.") == ["sexual_orientation"]
    assert cats("Sam's BRCA1 genetic test came back positive.") == ["genetic_biometric"]
    assert cats("Priya is a member of the labor union at the plant.") == ["trade_union"]


def test_ordinary_business_text_is_not_highlighted():
    for text in ("Your invoice INV-4 is overdue, please pay by Friday.", "We adopted the new schedule.",
                 "Please bring your insurance card to the appointment.", "Order 12 bags of flour for Saturday.",
                 "The comma-separated file is attached.", "The fraud alert was a test.", "Thanks for your help today!"):
        assert cats(text) == [], text


def test_restated_designated_notes(tmp_path):
    (tmp_path / "notes.md").write_text("Ana's son was suspended from school after the fight in March.\n"
                                       "The bakery opens at six.\n")
    k = KnownData.from_paths(["notes.md"], root=tmp_path)
    assert cats("Quick update: Ana's son got suspended from school after that fight in March.", k) == ["known_fact"]
    assert cats("Our delivery truck arrives on Tuesday.", k) == []


def test_a_sentence_of_exact_values_stays_known_data_not_a_restatement(tmp_path):
    (tmp_path / "p.csv").write_text("name,dob\nMaria Velkoran,1981-05-29\n")
    k = KnownData.from_paths(["p.csv"], root=tmp_path)
    m = map_call("mcp__office__send_email", {"to": TO, "text": "Patients: Maria Velkoran, 1981-05-29"}, known=k)
    assert m.minimal["text"]["value"] == "Patients: [known_data], [known_data]"


def test_shorter_version_removes_the_whole_sentence_with_any_detail_inside_it():
    m = map_call("mcp__office__send_email",
                 {"to": TO, "text": "Hi Ann. Jane is being treated for depression, call 917-555-0142. See you Friday."})
    assert m.minimal["text"]["value"] == "Hi Ann. [mental_health] See you Friday."
    assert m.minimal["to"]["value"] == TO


def test_request_naming_the_fact_keeps_it():
    m = map_call("mcp__office__send_email", {"to": TO, "text": "Hi. Jane is being treated for depression. Bye."},
                 request="Send the referral and include her mental health diagnosis.")
    assert m.minimal is None or "depression" in m.minimal["text"]["value"]


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    return TestClient(app), app


def test_card_label_and_ledger_categories_only(tmp_path):
    client, app = make(tmp_path)
    text = "Hi Ann. Jane is being treated for depression. See you Friday."
    out = {}

    def run():
        out["r"] = client.post("/hook/pretooluse", json={"tool_name": "mcp__office__send_email",
                                                         "tool_input": {"to": TO, "text": text}},
                               headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()
    t = threading.Thread(target=run)
    t.start()
    for _ in range(200):
        w = app.state.store.list_actions(status="waiting")
        if w:
            page = client.get("/?embed=1").text
            assert "mental health" in page and "removed in shorter version" in page
            app.state.engine.decide(w[0].id, "minimal")
            break
        time.sleep(0.02)
    t.join()
    assert out["r"]["hookSpecificOutput"]["updatedInput"]["text"] == "Hi Ann. [mental_health] See you Friday."
    recs = [json.loads(l) for p in (tmp_path / "evidence").glob("*.jsonl") for l in p.read_text().splitlines()]
    dec = next(r["data"] for r in recs if r["event"] == "decision")
    ex = next(r["data"] for r in recs if r["event"] == "executed")
    assert "mental_health" in dec["found"] and "mental_health" in ex["removed"]
    assert "depression" not in json.dumps({k: v for k, v in ex.items() if k != "input"}) + json.dumps(dec)


def test_free_text_detection_can_be_turned_off(tmp_path):
    text = "Hi Ann. Jane was arrested last year. See you Friday."
    assert map_call("mcp__office__send_email", {"to": TO, "text": text}).minimal["text"]["value"] == \
        "Hi Ann. [legal] See you Friday."
    off = map_call("mcp__office__send_email", {"to": TO, "text": text}, freetext=False)
    assert "arrested" in off.minimal["text"]["value"]               # only the address is a finding without it
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False, freetext=False)
    client = TestClient(app)
    out = client.post("/hook/pretooluse", json={"tool_name": "WebSearch", "tool_input": {"query": text}},
                      headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()
    assert out == {}                     # nothing highlighted, so the search passes under the starter rules
    on = create_app(tmp_path / "on", app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    assert on.state.freetext is True and app.state.freetext is False


def test_cli_flag(monkeypatch):
    import hidee.cli as cli
    seen = {}
    monkeypatch.setattr(cli, "run_app", lambda *a, **k: seen.update(k))
    cli.main(["app", "--no-freetext", "--no-browser"])
    assert seen["freetext"] is False
    cli.main(["app", "--no-browser"])
    assert seen["freetext"] is True
