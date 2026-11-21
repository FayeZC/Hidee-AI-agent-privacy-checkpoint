"""Known data: values from files the user marks sensitive are found in outbound calls, highlighted, and removed by
the shorter version; nothing is found unless files are designated; no values go to the evidence log."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.known import KnownData, designated, personal
from hidee.sessions import SessionManager, codex_overrides
from hidee.toolmap import map_call

CSV = ("name,email,dob,member_id,account,note\n"
       "Maria Velkoran,maria.v.1a2b@example.org,1981-05-29,971-79-1316,ACCT-DQ4H-108850,dyslexia plan HN-S6D0D\n"
       "Samir Tordris,samir.t.9z8y@example.org,1975-02-11,955-73-0042,ACCT-ZU00-555772,none\n")


def known(tmp_path):
    (tmp_path / "patients.csv").write_text(CSV)
    return KnownData.from_paths(["patients.csv"], root=tmp_path)


def test_values_that_count():
    assert personal("Maria Velkoran") and personal("1981-05-29") and personal("ACCT-DQ4H-108850")
    assert personal("HN-S6D0D")
    assert not personal("none") and not personal("overdue") and not personal("Mon")


def test_finds_names_dates_ids_with_other_separators(tmp_path):
    k = known(tmp_path)
    text = "Roster: MARIA  VELKORAN born 1981/05/29, acct ACCT DQ4H 108850, ref hn-s6d0d; Samir Tordris"
    found = {f.value for f in k.find(text)}
    assert {"MARIA  VELKORAN", "1981/05/29", "ACCT DQ4H 108850", "hn-s6d0d", "Samir Tordris"} <= found
    assert k.find("Maria Velkorans") == []                 # word boundary: not part of another word


def test_shorter_version_removes_known_data_but_keeps_destination(tmp_path):
    k = known(tmp_path)
    m = map_call("mcp__office__send_email", {"to": "samir.t.9z8y@example.org",
                                             "text": "Patients: Maria Velkoran, 1981-05-29, ACCT-DQ4H-108850"},
                 known=k)
    cats = {f["category"] for f in m.fields.values()}
    assert "known_data" in cats
    assert m.minimal["to"]["value"] == "samir.t.9z8y@example.org"
    assert m.minimal["text"]["value"] == "Patients: [known_data], [known_data], [known_data]"
    plain = map_call("mcp__office__send_email", {"to": "x@y.org", "text": "Maria Velkoran"})      # no known data
    assert "known_data" not in {f["category"] for f in plain.fields.values()}


def test_designation_file(tmp_path):
    (tmp_path / "patients.csv").write_text(CSV)
    (tmp_path / ".hidee").mkdir()
    (tmp_path / ".hidee" / "sensitive").write_text("# customer records\npatients.csv\n")
    assert designated(tmp_path) == [tmp_path / "patients.csv"]
    assert designated(tmp_path / "nothing") == []


def make(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",), network_gate=False)
    return TestClient(app), app


def test_hook_holds_known_data_only_in_a_designated_project_and_logs_no_values(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "patients.csv").write_text(CSV)
    client, app = make(tmp_path / "home")
    call = lambda session: client.post("/hook/pretooluse", json={  # noqa: E731
        "tool_name": "WebSearch", "tool_input": {"query": "Maria Velkoran 1981-05-29"}, "cwd": str(project)},
        headers={"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": session}).json()
    assert call("s1") == {}                                 # not designated: plain text, allowed
    (project / ".hidee").mkdir()
    (project / ".hidee" / "sensitive").write_text("patients.csv\n")

    def decide():
        for _ in range(200):
            w = app.state.store.list_actions(status="waiting")
            if w:
                app.state.engine.decide(w[0].id, "minimal")
                return
            time.sleep(0.02)
    t = threading.Thread(target=decide)
    t.start()
    out = call("s2")
    t.join()
    assert out["hookSpecificOutput"]["updatedInput"] == {"query": "[known_data] [known_data]"}
    records = [json.loads(l) for p in (tmp_path / "home" / "evidence").glob("*.jsonl") for l in p.read_text().splitlines()]
    decision = [r["data"] for r in records if r["event"] == "decision"][-1]
    assert decision["found"] == ["known_data"]
    executed = [r["data"] for r in records if r["event"] == "executed"][-1]
    assert executed["removed"] == ["known_data"]
    without_inputs = json.dumps([{k: v for k, v in r["data"].items() if k not in ("input",)} for r in records
                                 if r["event"] in ("decision", "executed")])
    assert "Velkoran" not in without_inputs and "1981-05-29" not in without_inputs
    assert app.state.withheld.match("s2", "maria velkoran") is not None


class FakeGate:
    def __init__(self):
        self.calls, self.safe, self.port = [], set(), 5000

    def session_port(self, sid, refuse_local=True):
        self.calls.append((sid, refuse_local))
        return 6001

    def proxy_url(self, sid):
        return f"http://{sid}:secret@127.0.0.1:5000"

    def start(self):
        return 5000


def test_gemini_sessions_use_a_credential_free_gate_port(tmp_path):
    import sys
    gate = FakeGate()
    m = SessionManager(tmp_path / "h", "http://127.0.0.1:8770", "tok", gate=gate, user_home=tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    s = m.create("g", str(work), "gemini", argv=[sys.executable, "-c", "import os; print(os.environ['HTTPS_PROXY'])"])
    time.sleep(0.5)
    assert gate.calls == [(s.id, False)]                    # outside safe mode, local services are not refused
    assert b"http://127.0.0.1:6001" in bytes(s.scrollback) and b"secret" not in bytes(s.scrollback)
    m.close_all()


def test_codex_safe_mode_skips_host_skills():
    args = codex_overrides(safe=True)
    assert "skip_host_skill_discovery" in args and "--enable" in args
    assert "skip_host_skill_discovery" not in codex_overrides(safe=False)
