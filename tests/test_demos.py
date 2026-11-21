import pytest
from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.demo import DEMOS, create_demo_app, run_scenarios, seed_home
from hidee.executors import Executors, SimulatedExecutor
from hidee.rules import from_dict


@pytest.mark.parametrize("name", list(DEMOS))
def test_every_demo_has_valid_rules_and_exactly_one_step_for_review(name):
    demo = DEMOS[name]
    from_dict(demo["rules"])                                    # raises if the rules are inconsistent
    assert [s["expect"] for s in demo["steps"]].count("waiting") == 1
    assert {s["agent"] for s in demo["steps"]} <= set(demo["rules"]["agents"])


@pytest.mark.parametrize("name", list(DEMOS))
def test_every_demo_reaches_its_expected_outcomes_over_http(tmp_path, name):
    tokens = seed_home(tmp_path, name)
    app = create_app(tmp_path, executors=Executors(SimulatedExecutor()))
    client = TestClient(app)

    def submit(agent, payload):
        r = client.post("/api/actions", json=payload, headers={"Authorization": f"Bearer {tokens[agent]}"})
        r.raise_for_status()
        return r.json()

    results = run_scenarios(submit, interval=0, log=lambda *_: None, name=name)
    assert [r["status"] for r in results] == [s["expect"] for s in DEMOS[name]["steps"]]
    assert client.get("/").text.count('class="card"') == 1


def test_switching_demos_starts_clean(tmp_path):
    app = create_demo_app(tmp_path)
    client = TestClient(app)
    page = client.get("/").text
    for demo in DEMOS.values():
        assert demo["title"] in page
    r = client.post("/demo/replay", data={"csrf": app.state.csrf, "name": "clinic"}, follow_redirects=False)
    assert r.status_code == 303 and app.state.demo_name == "clinic"
    assert len(app.state.store.list_actions()) == len(DEMOS["clinic"]["steps"])
    page = client.get("/").text
    assert "Billing agent" in page and "Insurance company" in page and "Bluebird" not in page
    assert "Send shorter version" not in page                    # this one has no shorter version
    dash = client.get("/dashboard").text
    assert "Front-desk assistant" in dash and "Bluebird" not in dash


def test_replay_restarts_the_current_demo_and_unknown_demo_is_rejected(tmp_path):
    app = create_demo_app(tmp_path, "shop")
    client = TestClient(app)
    client.post("/demo/replay", data={"csrf": app.state.csrf})
    assert app.state.demo_name == "shop" and len(app.state.store.list_actions()) == len(DEMOS["shop"]["steps"])
    assert client.post("/demo/replay", data={"csrf": app.state.csrf, "name": "zoo"}).status_code == 400
