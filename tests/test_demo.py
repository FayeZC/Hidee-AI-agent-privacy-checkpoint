from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.demo import ALL_STEPS, HISTORY, SCENARIOS, run_scenarios, seed_home
from hidee.executors import Executors, SimulatedExecutor


def test_demo_steps_reach_expected_outcomes_and_only_one_waits(tmp_path):
    tokens = seed_home(tmp_path)
    sim = SimulatedExecutor()
    app = create_app(tmp_path, executors=Executors(sim))
    client = TestClient(app)

    def submit(agent, payload):
        r = client.post("/api/actions", json=payload, headers={"Authorization": f"Bearer {tokens[agent]}"})
        r.raise_for_status()
        return r.json()

    results = run_scenarios(submit, interval=0, log=lambda *_: None)
    assert [r["status"] for r in results] == [s["expect"] for s in ALL_STEPS]
    assert [r["status"] for r in results].count("waiting") == 1
    assert client.get("/").text.count('class="card"') == 1

    delivery = results[-1]
    r = client.post(f"/decide/{delivery['id']}", data={"choice": "minimal", "csrf": app.state.csrf})
    assert r.status_code == 200
    assert app.state.engine.get(delivery["id"]).status == "minimized"
    assert "Tuesday" not in " ".join(sim.sent[-1].values.values())
    assert not any(cat == "routine" for _, cat, _ in app.state.store.disclosure_counts())


def test_demo_review_part_is_one_simple_card():
    assert len(SCENARIOS) == 1 and len(HISTORY) == 5
    s = SCENARIOS[0]
    assert s["expect"] == "waiting" and set(s["fields"]) == {"message", "note"} and set(s["minimal"]) == {"message"}
    assert {s["agent"] for s in ALL_STEPS} == {"assistant", "bookkeeper"}
