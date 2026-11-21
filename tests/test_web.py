import time

import pytest
from fastapi.testclient import TestClient

from hidee.agents import add_agent
from hidee.app import create_app
from hidee.rules import from_dict, save_rules
from tests.test_api import RULES, MSG


@pytest.fixture
def setup(tmp_path):
    save_rules(from_dict(RULES), tmp_path / "rules.yaml")
    app = create_app(tmp_path)
    add_agent(app.state.store, "shop", "Shopping assistant", time.time())
    engine = app.state.engine
    rec = engine.submit("shop", "send_message", "parcel",
                        {"msg": MSG, "note": {"value": "Away every Tuesday", "category": "routine"}}, {"msg": MSG})
    return TestClient(app), app, rec


def test_empty_state_explains_what_to_do(tmp_path):
    page = TestClient(create_app(tmp_path)).get("/").text
    assert "Nothing is waiting for you" in page and "hidee demo" in page


def test_review_card_shows_flow_chips_reason_and_buttons(setup):
    client, _, _ = setup
    page = client.get("/").text
    for text in ("Shopping assistant", "Send message", "Parcel Co.", "daily routine", "check",
                 "delivery instructions",
                 "Approve as written", "Send shorter version", "Reject", "removed in shorter version"):
        assert text in page, text


def test_fragment_returns_only_cards(setup):
    client, _, _ = setup
    html = client.get("/fragments/review").text
    assert "Send shorter version" in html and "<nav" not in html


def test_decision_requires_form_token(setup):
    client, app, rec = setup
    r = client.post(f"/decide/{rec.id}", data={"choice": "approve", "csrf": "wrong"})
    assert r.status_code == 403 and app.state.engine.get(rec.id).status == "waiting"


def test_send_shorter_version_from_dashboard(setup):
    client, app, rec = setup
    r = client.post(f"/decide/{rec.id}", data={"choice": "minimal", "csrf": app.state.csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    assert app.state.engine.get(rec.id).status == "minimized"
    dashboard = client.get("/dashboard").text
    assert "Sent shorter version" in dashboard and "What was sent" in dashboard
    assert "Back door" in dashboard


def test_deciding_twice_is_409(setup):
    client, app, rec = setup
    client.post(f"/decide/{rec.id}", data={"choice": "reject", "csrf": app.state.csrf})
    assert client.post(f"/decide/{rec.id}", data={"choice": "approve", "csrf": app.state.csrf}).status_code == 409


def test_static_files_are_served(setup):
    client, _, _ = setup
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_review_page_shows_one_decision_at_a_time(setup):
    client, app, rec = setup
    second = app.state.engine.submit("shop", "send_message", "parcel",
                                     {"msg": MSG, "note": {"value": "Second request", "category": "routine"}})
    page = client.get("/").text
    assert page.count('class="card"') == 1
    assert "1 of 2 waiting" in page
    assert "Away every Tuesday" in page and "Second request" not in page   # oldest first
    client.post(f"/decide/{rec.id}", data={"choice": "reject", "csrf": app.state.csrf})
    page = client.get("/").text
    assert "Second request" in page and "of 2 waiting" not in page


def test_card_has_no_extra_columns_or_footnotes(setup):
    client, _, _ = setup
    page = client.get("/").text
    assert "Shorter version</th>" not in page and "Received" not in page


def test_who_knows_what_is_empty_before_anything_is_sent(setup):
    client, _, _ = setup
    page = client.get("/knows").text
    assert "Who knows what" in page and "Nothing has left yet" in page


def test_who_knows_what_is_a_section_of_what_left(setup):
    client, app, rec = setup
    client.post(f"/decide/{rec.id}", data={"choice": "minimal", "csrf": app.state.csrf})
    page = client.get("/dashboard").text
    knows = page.split('id="knows"')[1].split('<h3 class="section">')[0]
    assert "Parcel Co." in knows and "delivery instructions" in knows
    assert "daily routine" not in knows                     # removed from the shorter version, so never sent
    assert f"/dashboard?id={rec.id}" in knows                # each cell leads to the last step that sent it
    r = client.get("/knows", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/dashboard#knows"


def test_nav_follows_the_story(setup):
    client, _, _ = setup
    nav = client.get("/dashboard").text.split("<nav>")[1].split("</nav>")[0]
    assert "What left" in nav and 'href="/knows"' not in nav and "Who knows what" not in nav
