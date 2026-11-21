import json

from hidee.executors import ExecutionRequest, Executors, LocalMemoryExecutor, SimulatedExecutor


def req(recipient="parcel"):
    return ExecutionRequest("a1", "send_message", recipient, {"msg": "hi"})


def test_simulated_records_requests():
    sim = SimulatedExecutor()
    sim(req())
    assert sim.sent == [req()]


def test_local_memory_appends_json_lines(tmp_path):
    path = tmp_path / "memory.jsonl"
    ex = LocalMemoryExecutor(path, clock=lambda: 5.0)
    ex(req("agent-memory"))
    ex(req("agent-memory"))
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert lines[0] == {"at": 5.0, "action_id": "a1", "recipient": "agent-memory", "values": {"msg": "hi"}}
    assert len(lines) == 2


def test_registry_routes_by_recipient():
    default, special = SimulatedExecutor(), SimulatedExecutor()
    ex = Executors(default, {"agent-memory": special})
    ex.run(req("parcel"))
    ex.run(req("agent-memory"))
    assert len(default.sent) == 1 and len(special.sent) == 1
