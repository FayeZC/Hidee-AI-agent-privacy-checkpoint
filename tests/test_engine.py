import pytest

from hidee.core import digest
from hidee.engine import ActionError, Engine
from hidee.executors import Executors, SimulatedExecutor
from hidee.rules import from_dict
from hidee.store import Store

RULES = from_dict({
    "agents": {"shop": "Shopping assistant"},
    "recipients": {"parcel": "Parcel Co."},
    "categories": {"delivery": "delivery instructions", "routine": "daily routine", "address": "home address"},
    "rules": [{"agent": "shop", "kind": "send_message", "recipient": "parcel",
               "allow": ["delivery"], "forbid": ["address"]}],
})
MSG = {"value": "Leave it at the back door", "category": "delivery"}
ROUTINE = {"value": "I am away every Tuesday night", "category": "routine"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock, sim = Clock(), SimulatedExecutor()
    engine = Engine(Store(tmp_path / "d.db"), lambda: RULES, Executors(sim), clock=clock, review_timeout_s=600)
    return engine, sim, clock


def submit_review(engine, minimal=True):
    return engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE},
                         {"msg": MSG} if minimal else None)


def test_allowed_by_rules_runs_immediately(env):
    engine, sim, _ = env
    rec = engine.submit("shop", "send_message", "parcel", {"msg": MSG})
    assert rec.status == "allowed" and rec.decided_by == "rules"
    assert rec.executed == {"msg": MSG["value"]}
    assert sim.sent[0].values == {"msg": MSG["value"]}
    assert engine.store.disclosure_counts() == [("parcel", "delivery", 1)]


def test_forbidden_is_blocked_without_running(env):
    engine, sim, _ = env
    rec = engine.submit("shop", "send_message", "parcel", {"msg": MSG, "addr": {"value": "14 Elm", "category": "address"}})
    assert rec.status == "blocked" and sim.sent == []
    assert engine.store.disclosure_counts() == []


def test_unclear_waits_for_owner(env):
    engine, sim, clock = env
    rec = submit_review(engine)
    assert rec.status == "waiting" and rec.expires_at == clock.t + 600 and sim.sent == []
    assert rec.marks == {"delivery": "needed", "routine": "check"}


def test_owner_sends_minimal_version(env):
    engine, sim, _ = env
    rec = engine.decide(submit_review(engine).id, "minimal")
    assert rec.status == "minimized" and rec.decided_by == "owner"
    assert sim.sent[0].values == {"msg": MSG["value"]}
    assert ("parcel", "routine", 1) not in engine.store.disclosure_counts()


def test_owner_approves_original(env):
    engine, sim, _ = env
    rec = engine.decide(submit_review(engine).id, "approve")
    assert rec.status == "allowed" and set(sim.sent[0].values) == {"msg", "note"}


def test_owner_rejects(env):
    engine, sim, _ = env
    rec = engine.decide(submit_review(engine).id, "reject")
    assert rec.status == "blocked" and sim.sent == []


def test_cannot_decide_twice(env):
    engine, sim, _ = env
    rec = submit_review(engine)
    engine.decide(rec.id, "approve")
    with pytest.raises(ActionError, match="already been decided"):
        engine.decide(rec.id, "reject")
    assert len(sim.sent) == 1


def test_minimal_needs_a_minimal_version(env):
    engine, _, _ = env
    with pytest.raises(ActionError, match="no minimal version"):
        engine.decide(submit_review(engine, minimal=False).id, "minimal")


def test_waiting_times_out(env):
    engine, sim, clock = env
    rec = submit_review(engine)
    clock.t += 601
    assert engine.expire_waiting() == 1
    assert engine.get(rec.id).status == "timed_out"
    with pytest.raises(ActionError):
        engine.decide(rec.id, "approve")
    assert sim.sent == []


def test_grant_runs_only_once(env):
    engine, sim, _ = env
    rec = engine.decide(submit_review(engine).id, "approve")
    grant_id = engine.store.grants_for(rec.id)[0]["id"]
    engine.execute_grant(grant_id, "allowed")
    assert len(sim.sent) == 1


def test_changed_content_is_not_run(env):
    engine, sim, clock = env
    rec = submit_review(engine)
    engine.store.insert_grant("g1", rec.id, "original", digest(rec.fields), clock.t + 60)
    engine.store.update_action(rec.id, fields={"msg": MSG, "note": {"value": "changed", "category": "routine"}})
    out = engine.execute_grant("g1", "allowed")
    assert out.status == "blocked" and "changed" in out.reason and sim.sent == []


def test_expired_grant_is_not_run(env):
    engine, sim, clock = env
    rec = submit_review(engine)
    engine.store.insert_grant("g1", rec.id, "original", digest(rec.fields), clock.t - 1)
    assert engine.execute_grant("g1", "allowed").status == "blocked" and sim.sent == []


def test_executor_failure_is_recorded_and_not_retried(tmp_path):
    calls = []

    def broken(request):
        calls.append(request)
        raise RuntimeError("smtp down")

    engine = Engine(Store(tmp_path / "d.db"), lambda: RULES, Executors(broken), clock=Clock())
    rec = engine.submit("shop", "send_message", "parcel", {"msg": MSG})
    assert rec.status == "execution_failed" and len(calls) == 1
    assert "smtp down" not in rec.reason
    assert engine.store.disclosure_counts() == [("parcel", "delivery", 1)]  # possible exposure is kept


def test_minimal_that_rules_forbid_is_not_offered(env):
    engine, _, _ = env
    rec = engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE},
                        {"msg": {"value": "14 Elm", "category": "address"}})
    assert rec.status == "waiting" and rec.minimal is None


@pytest.mark.parametrize("fields, minimal", [
    ({}, None),
    ({"msg": {"value": "x"}}, None),
    ({"msg": MSG}, {"other": MSG}),
    ({"": MSG}, None),
])
def test_invalid_requests_are_rejected(env, fields, minimal):
    engine, _, _ = env
    with pytest.raises(ActionError):
        engine.submit("shop", "send_message", "parcel", fields, minimal)


def test_audit_never_stores_payload_values(env):
    engine, _, _ = env
    engine.decide(submit_review(engine).id, "approve")
    assert "Tuesday" not in str(engine.store.audit_log())
