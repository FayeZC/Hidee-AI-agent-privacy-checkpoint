import time

import pytest
from fastapi.testclient import TestClient

from hidee.agents import add_agent
from hidee.app import create_app
from hidee.rules import from_dict, save_rules
from tests.test_api import MSG, RULES

ROUTINE = {"value": "Away every Tuesday", "category": "routine"}


@pytest.fixture
def setup(tmp_path):
    save_rules(from_dict(RULES), tmp_path / "rules.yaml")
    app = create_app(tmp_path)
    add_agent(app.state.store, "shop", "Shopping assistant", time.time())
    rec = app.state.engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE}, {"msg": MSG})
    return TestClient(app), app, rec


def test_card_buttons_lead_to_a_confirmation_first(setup):
    client, _, rec = setup
    page = client.get("/").text
    assert f'action="/confirm/{rec.id}"' in page and 'method="get"' in page
    assert f'action="/decide/{rec.id}"' not in page


def test_confirm_shorter_version_shows_what_goes_and_what_stays(setup):
    client, app, rec = setup
    page = client.get(f"/confirm/{rec.id}?choice=minimal").text
    assert "Send the shorter version to Parcel Co.?" in page
    sent = page.split("Will be sent")[1].split("Will be left out")[0]
    left_out = page.split("Will be left out")[1]
    assert "Back door" in sent and "Away every Tuesday" not in sent
    assert "Away every Tuesday" in left_out
    assert f'action="/decide/{rec.id}"' in page and 'name="choice" value="minimal"' in page
    assert app.state.csrf in page and "Go back" in page
    assert app.state.engine.get(rec.id).status == "waiting"      # looking does not decide


def test_confirm_approve_lists_everything(setup):
    client, _, rec = setup
    page = client.get(f"/confirm/{rec.id}?choice=approve").text
    assert "Send it as written to Parcel Co.?" in page
    sent = page.split("Will be sent")[1]
    assert "Back door" in sent and "Away every Tuesday" in sent and "Will be left out" not in page


def test_confirm_reject_says_nothing_is_sent(setup):
    client, _, rec = setup
    page = client.get(f"/confirm/{rec.id}?choice=reject").text
    assert "Reject this request?" in page and "Nothing will be sent" in page


def test_only_the_confirmed_post_acts(setup):
    client, app, rec = setup
    page = client.get(f"/confirm/{rec.id}?choice=minimal").text
    assert app.state.engine.get(rec.id).status == "waiting"
    r = client.post(f"/decide/{rec.id}", data={"choice": "minimal", "csrf": app.state.csrf}, follow_redirects=False)
    assert r.status_code == 303 and app.state.engine.get(rec.id).status == "minimized"


def test_bad_confirm_requests(setup):
    client, app, rec = setup
    assert client.get(f"/confirm/{rec.id}?choice=delete").status_code == 400
    assert client.get("/confirm/nope?choice=approve").status_code == 404
    other = app.state.engine.submit("shop", "send_message", "parcel", {"note": ROUTINE})
    assert client.get(f"/confirm/{other.id}?choice=minimal").status_code == 409     # no shorter version
    app.state.engine.decide(rec.id, "reject")
    r = client.get(f"/confirm/{rec.id}?choice=approve")
    assert r.status_code == 409 and "already been decided" in r.text
