"""Once the student removes or rejects a detail, the same detail in a disguised form is still recognized."""
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.withheld import Withheld

EMAIL = "maria.lopez@example.org"


def held():
    w = Withheld()
    w.add("s1", [(EMAIL, "email"), ("917-555-0142", "phone")])
    return w


def test_disguised_emails_are_recognized():
    w = held()
    for text in (f"{EMAIL} dentist", '"maria.lopez" "example.org" dentist', "maria.lopez at example.org",
                 "m a r i a . l o p e z @ e x a m p l e . o r g", "maria.lopez [at] example [dot] org",
                 "MARIA.LOPEZ@EXAMPLE.ORG", "maria.lopez dentist"):
        assert w.match("s1", text) == (EMAIL, "email"), text


def test_disguised_phone_numbers_are_recognized():
    w = held()
    for text in ("call 917 555 0142", "(917) 555-0142", "9175550142"):
        assert w.match("s1", text) == ("917-555-0142", "phone"), text


def test_unrelated_text_and_other_sessions_pass():
    w = held()
    assert w.match("s1", "dental check-up reminder wording") is None
    assert w.match("s1", "example.org opening hours") is None        # the domain alone is not the person
    assert w.match("s2", EMAIL) is None


def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def search(client, app, query):
    return client.post("/hook/pretooluse", json={"tool_name": "WebSearch", "tool_input": {"query": query}},
                       headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": "s1"}).json()


def test_after_a_shorter_version_the_disguised_retry_is_stopped(tmp_path):
    client, app = make(tmp_path)

    def student():
        for _ in range(200):
            waiting = app.state.store.list_actions(status="waiting")
            if waiting:
                app.state.engine.decide(waiting[0].id, "minimal")
                return
            time.sleep(0.02)
    t = threading.Thread(target=student)
    t.start()
    first = search(client, app, f"{EMAIL} dentist")
    t.join()
    assert first["hookSpecificOutput"]["updatedInput"] == {"query": "[email] dentist"}
    retry = search(client, app, '"maria.lopez" "example.org" dentist')
    out = retry["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "held back earlier" in out["permissionDecisionReason"]
    assert EMAIL not in out["permissionDecisionReason"]              # the reason does not repeat the detail
    assert app.state.store.list_actions(status="waiting") == []      # no new question for the student
