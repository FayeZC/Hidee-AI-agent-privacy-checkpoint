import time

import pytest
from fastapi.testclient import TestClient

from hidee.agents import add_agent
from hidee.app import create_app
from hidee.rules import from_dict, save_rules

RULES = {
    "agents": {"shop": "Shopping assistant"},
    "recipients": {"parcel": "Parcel Co."},
    "categories": {"delivery": "delivery instructions", "routine": "daily routine"},
    "rules": [{"agent": "shop", "kind": "send_message", "recipient": "parcel", "allow": ["delivery"]}],
}
MSG = {"value": "Back door", "category": "delivery"}


@pytest.fixture
def setup(tmp_path):
    save_rules(from_dict(RULES), tmp_path / "rules.yaml")
    app = create_app(tmp_path)
    token = add_agent(app.state.store, "shop", "Shopping assistant", time.time())
    return TestClient(app), {"Authorization": f"Bearer {token}"}, app


def test_requires_token(setup):
    client, _, app = setup
    assert client.post("/api/actions", json={}).status_code == 401
    assert client.get("/api/actions/x", headers={"Authorization": "Bearer hd_nope"}).status_code == 401
    assert any(e["event"] == "auth_failed" for e in app.state.store.audit_log())


def test_allowed_action(setup):
    client, auth, _ = setup
    r = client.post("/api/actions", headers=auth,
                    json={"kind": "send_message", "recipient": "parcel", "fields": {"msg": MSG}})
    assert r.status_code == 201
    assert r.json()["status"] == "allowed" and r.json()["executed"] == {"msg": "Back door"}


def test_waiting_action_and_poll(setup):
    client, auth, app = setup
    body = {"kind": "send_message", "recipient": "parcel",
            "fields": {"msg": MSG, "note": {"value": "Away Tuesdays", "category": "routine"}},
            "minimal": {"msg": MSG}}
    r = client.post("/api/actions", headers=auth, json=body).json()
    assert r["status"] == "waiting" and "executed" not in r
    app.state.engine.decide(r["id"], "minimal")
    got = client.get(f"/api/actions/{r['id']}", headers=auth).json()
    assert got["status"] == "minimized" and got["executed"] == {"msg": "Back door"}


def test_wait_returns_when_time_is_up(setup):
    client, auth, _ = setup
    body = {"kind": "send_message", "recipient": "parcel", "wait": 1,
            "fields": {"note": {"value": "Away Tuesdays", "category": "routine"}}}
    started = time.monotonic()
    r = client.post("/api/actions", headers=auth, json=body).json()
    assert r["status"] == "waiting" and 0.9 <= time.monotonic() - started < 5


def test_invalid_body_is_422(setup):
    client, auth, _ = setup
    assert client.post("/api/actions", headers=auth,
                       json={"kind": "send_message", "recipient": "parcel", "fields": {}}).status_code == 422
    assert client.post("/api/actions", headers=auth,
                       json={"kind": "send_message", "recipient": "parcel", "fields": {"m": MSG}, "wait": 999}
                       ).status_code == 422


def test_agents_cannot_read_each_others_actions(setup):
    client, auth, app = setup
    other = add_agent(app.state.store, "spy", "Other agent", time.time())
    r = client.post("/api/actions", headers=auth,
                    json={"kind": "send_message", "recipient": "parcel", "fields": {"msg": MSG}}).json()
    assert client.get(f"/api/actions/{r['id']}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
