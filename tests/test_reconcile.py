"""Reconciliation: per-request model usage in the evidence log, the reconciliation report, the forensic timeline."""
import csv
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from hidee import learn as learn_mod
from hidee.app import create_app
from hidee.evidence import EvidenceLog, _records, verify
from hidee.hook_api import APP_RULES


def line(mid, out, at, cache_read=0, cache_create=0, model="claude-x", req=None):
    entry = {"type": "assistant", "timestamp": at,
             "message": {"id": mid, "model": model, "usage": {
                 "input_tokens": 3, "cache_read_input_tokens": cache_read,
                 "cache_creation_input_tokens": cache_create, "output_tokens": out}}}
    if req:
        entry["requestId"] = req
    return json.dumps(entry)


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    root = tmp_path / ".claude" / "projects"
    (root / "p").mkdir(parents=True)
    monkeypatch.setattr(learn_mod, "TRANSCRIPTS", root)
    path = root / "p" / "t.jsonl"
    path.write_text("\n".join([json.dumps({"type": "user"}),
                               line("msg_1", 10, "2026-11-04T10:00:01.000Z", cache_create=500, req="req_1"),
                               line("msg_1", 12, "2026-11-04T10:00:02.000Z", cache_create=500, req="req_1"),
                               line("msg_2", 20, "2026-11-04T10:00:05.000Z", cache_read=500)]) + "\n")
    return path


def make(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "rules.yaml").write_text(APP_RULES)
    app = create_app(home, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def H(app, session="s1", agent=""):
    return {"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": session, "X-Hidee-Agent": agent}


def requests_in(app, session="s1"):
    return [r["data"] for r in _records(app.state.home / "evidence")
            if r["event"] == "model_request" and r["session"] == session]


# ---- reading the transcript ----

def test_model_requests_are_read_once_per_message_id(transcript):
    reqs = learn_mod.model_requests(transcript)
    assert [r["request_id"] for r in reqs] == ["msg_1", "msg_2"]
    first = reqs[0]
    assert first == {"request_id": "msg_1", "provider_request_id": "req_1", "model": "claude-x",
                     "input_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 500,
                     "output_tokens": 12, "at": "2026-11-04T10:00:01.000Z"}
    assert "provider_request_id" not in reqs[1]


def test_only_transcripts_under_the_claude_folder_are_read(tmp_path, transcript):
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text(transcript.read_text())
    assert learn_mod.safe_transcript(str(outside)) is None
    assert learn_mod.safe_transcript(str(transcript.with_suffix(".txt"))) is None
    assert learn_mod.safe_transcript(str(transcript)) == transcript.resolve()


# ---- recording model_request evidence ----

def test_stop_records_each_new_request_once_for_every_session(tmp_path, transcript):
    client, app = make(tmp_path)                                    # not a learn session
    stop = {"hook_event_name": "Stop", "last_assistant_message": "ok", "transcript_path": str(transcript)}
    client.post("/hook/learn", json=stop, headers=H(app))
    client.post("/hook/learn", json=stop, headers=H(app))           # the same turn reported again
    assert [r["request_id"] for r in requests_in(app)] == ["msg_1", "msg_2"]
    with transcript.open("a") as f:
        f.write(line("msg_3", 7, "2026-11-04T10:01:00.000Z") + "\n")
    client.post("/hook/learn", json=stop, headers=H(app))
    reqs = requests_in(app)
    assert [r["request_id"] for r in reqs] == ["msg_1", "msg_2", "msg_3"]
    assert reqs[0]["output_tokens"] == 12 and reqs[0]["cache_creation_input_tokens"] == 500
    kinds = [r["event"] for r in _records(app.state.home / "evidence")]
    assert kinds.index("model_request") < kinds.index("answer")   # the requests come before the answer
    assert verify(app.state.home / "evidence")[0]


def test_a_last_message_written_after_stop_is_recorded_shortly_after(tmp_path, transcript):
    import time
    client, app = make(tmp_path)
    app.state.requests.follow_up = 0.2
    client.post("/hook/learn", json={"hook_event_name": "Stop", "transcript_path": str(transcript)}, headers=H(app))
    with transcript.open("a") as f:
        f.write(line("msg_late", 5, "2026-11-04T10:02:00.000Z") + "\n")
    for _ in range(50):
        if len(requests_in(app)) == 3:
            break
        time.sleep(0.05)
    assert [r["request_id"] for r in requests_in(app)] == ["msg_1", "msg_2", "msg_late"]


def test_sub_agent_requests_are_recorded_and_marked(tmp_path, transcript):
    client, app = make(tmp_path)
    sub = transcript.with_suffix("") / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.jsonl").write_text(line("msg_sub", 9, "2026-11-04T10:00:03.000Z") + "\n")
    client.post("/hook/learn", json={"hook_event_name": "Stop", "transcript_path": str(transcript)}, headers=H(app))
    reqs = requests_in(app)
    assert [r["request_id"] for r in reqs] == ["msg_1", "msg_2", "msg_sub"]
    assert reqs[2]["subagent"] == "agent-a1" and "subagent" not in reqs[0]


def test_a_restarted_app_does_not_record_a_request_twice(tmp_path, transcript):
    client, app = make(tmp_path)
    stop = {"hook_event_name": "Stop", "last_assistant_message": "ok", "transcript_path": str(transcript)}
    client.post("/hook/learn", json=stop, headers=H(app))
    again = TestClient(create_app(app.state.home, app_mode=True, allowed_hosts=("testserver",)))
    again.post("/hook/learn", json=stop, headers=H(again.app))
    assert len(requests_in(app)) == 2


def test_no_requests_from_outside_transcripts_or_other_agents(tmp_path, transcript):
    client, app = make(tmp_path)
    outside = tmp_path / "x.jsonl"
    outside.write_text(transcript.read_text())
    client.post("/hook/learn", json={"hook_event_name": "Stop", "transcript_path": str(outside)}, headers=H(app))
    client.post("/hook/learn", json={"hook_event_name": "Stop", "transcript_path": str(transcript)},
                headers=H(app, "s2", agent="codex"))
    assert requests_in(app) == [] and requests_in(app, "s2") == []


def test_session_end_records_requests_still_missing(tmp_path, transcript):
    client, app = make(tmp_path)
    client.post("/hook/learn", json={"hook_event_name": "UserPromptSubmit", "prompt": "hi",
                                     "transcript_path": str(transcript)}, headers=H(app))
    app.state.sessions.on_event("s1", "session_end", {"exit_code": 0})
    kinds = [r["event"] for r in _records(app.state.home / "evidence")]
    assert kinds == ["prompt", "model_request", "model_request", "session_end"]


# ---- the reconciliation report ----

DAY1, DAY2 = 1_793_800_000.0, 1_793_800_000.0 + 86_400      # 2026-11-04 and 2026-11-05 (UTC)


def fill(folder):
    log = EvidenceLog(folder, clock=lambda: DAY1)
    log.append("s1", "session_start", {"name": "Lab", "agent": "claude", "user": "maria"})
    log.append("s1", "model_request", {"request_id": "msg_a", "model": "claude-x", "input_tokens": 3,
                                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 900,
                                       "output_tokens": 40, "at": "2026-11-04T09:00:00.000Z"})
    log.append("s1", "model_request", {"request_id": "msg_b", "model": "claude-x", "input_tokens": 5,
                                       "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0,
                                       "output_tokens": 60, "at": "2026-11-04T09:00:09.000Z"})
    log.append("s1", "prompt", {"excerpt": "find maria", "length": 10, "sha256": "ab" * 32, "found": []})
    log.append("s1", "tool_call", {"tool": "WebFetch", "input": {"excerpt": "{}", "length": 2,
                                                                 "sha256": "cd" * 32, "found": []},
                                   "where": "outside"})
    log.append("s1", "decision", {"action": "a1", "kind": "fetch", "recipient": "files.example.com",
                                  "found": ["email"], "by": "person", "choice": "minimal", "reason": "ok",
                                  "seconds": 4.2, "user": "maria"})
    log.append("s1", "decision", {"action": "a2", "kind": "fetch", "recipient": "files.example.com",
                                  "found": [], "by": "rules", "choice": "approve", "reason": "ok", "seconds": 0})
    log.append("s1", "connection", {"host": "files.example.com", "port": 443, "url": "files.example.com:443",
                                    "allowed": True, "model": False})
    log.append("s1", "connection", {"host": "evil.example", "port": 443, "url": "evil.example:443",
                                    "allowed": False, "model": False})
    log = EvidenceLog(folder, clock=lambda: DAY2)
    log.append("s1", "decision", {"action": "a3", "kind": "search", "recipient": "web-search", "found": [],
                                  "by": "timeout", "choice": "timed_out", "reason": "no answer", "seconds": 300})
    log.append("s1", "model_request", {"request_id": "msg_c", "model": "claude-y", "input_tokens": 1,
                                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                                       "output_tokens": 2, "at": "2026-11-05T01:00:00.000Z"})
    log.append("s1", "session_end", {"exit_code": 0})


def test_reconcile_counts_per_day(tmp_path):
    from hidee.reconcile import reconcile
    fill(tmp_path / "ev")
    rep = reconcile(tmp_path / "ev")
    assert rep["chain"]["intact"] and rep["chain"]["head_seq"] == 12
    m = {(r["day"], r["model"]): r for r in rep["models"]}
    assert m[("2026-11-04", "claude-x")] == {"day": "2026-11-04", "model": "claude-x", "requests": 2,
                                              "input_tokens": 8, "cache_read_input_tokens": 900,
                                              "cache_creation_input_tokens": 900, "output_tokens": 100}
    assert m[("2026-11-05", "claude-y")]["requests"] == 1
    a = {(r["day"], r["destination"]): r for r in rep["actions"]}
    files = a[("2026-11-04", "files.example.com")]
    assert files["minimal"] == 1 and files["approve"] == 1 and files["by"] == {"person": 1, "rules": 1}
    assert a[("2026-11-05", "web-search")]["timed_out"] == 1
    c = {(r["day"], r["host"]): r for r in rep["connections"]}
    assert c[("2026-11-04", "files.example.com")]["allowed"] == 1
    assert c[("2026-11-04", "evil.example")]["refused"] == 1
    only = reconcile(tmp_path / "ev", day_from="2026-11-05", day_to="2026-11-05")
    assert [r["model"] for r in only["models"]] == ["claude-y"] and len(only["requests"]) == 1


def test_cli_reconcile_markdown_and_csv(tmp_path, capsys):
    from hidee.cli import main
    home = tmp_path / "home"
    fill(home / "evidence")
    head = EvidenceLog(home / "evidence").head()[1]
    main(["evidence", "reconcile", "--home", str(home)])
    md = capsys.readouterr().out
    assert "Chain intact" in md and head in md
    assert "| 2026-11-04 | claude-x | 2 | 8 | 900 | 900 | 100 |" in md
    assert "files.example.com" in md and "evil.example" in md
    main(["evidence", "reconcile", "--home", str(home), "--format", "csv", "--from", "2026-11-04",
          "--to", "2026-11-04"])
    out = capsys.readouterr()
    rows = list(csv.DictReader(io.StringIO(out.out)))
    ids = [r["request_id"] for r in rows if r["section"] == "model_request"]
    assert ids == ["msg_a", "msg_b"]
    assert any(r["section"] == "action" and r["subject"] == "files.example.com" for r in rows)
    assert "Chain intact" in out.err


# ---- the forensic timeline ----

COLUMNS = ["utc_time", "seq", "session", "event", "actor", "agent", "tool", "destination", "decision",
           "request_id", "sha256", "record_hash", "prev_hash", "detail"]


def test_timeline_csv_has_one_row_per_record(tmp_path):
    from hidee.reconcile import timeline_csv
    fill(tmp_path / "ev")
    text = timeline_csv(tmp_path / "ev")
    assert "\r\n" in text                                              # RFC 4180 line ends
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == COLUMNS
    body = [dict(zip(COLUMNS, r)) for r in rows[1:]]
    assert [int(r["seq"]) for r in body] == list(range(1, 13))
    recs = list(_records(tmp_path / "ev"))
    assert all(r["record_hash"] == x["hash"] and r["prev_hash"] == x["prev"] and r["utc_time"] == x["at"]
               for r, x in zip(body, recs))
    by_event = lambda e: [r for r in body if r["event"] == e]  # noqa: E731
    assert by_event("session_start")[0]["actor"] == "hidee"
    assert by_event("prompt")[0]["actor"] == "person:maria" and by_event("prompt")[0]["sha256"] == "ab" * 32
    assert by_event("tool_call")[0]["sha256"] == "cd" * 32 and by_event("tool_call")[0]["tool"] == "WebFetch"
    d = by_event("decision")
    assert d[0]["actor"] == "person:maria" and d[0]["decision"] == "minimal"
    assert d[0]["destination"] == "files.example.com" and d[1]["actor"] == "rules" and d[2]["actor"] == "timeout"
    conn = by_event("connection")
    assert conn[0]["actor"] == "gate" and conn[0]["destination"] == "files.example.com:443"
    assert conn[0]["decision"] == "allowed" and conn[1]["decision"] == "refused"
    mr = by_event("model_request")
    assert mr[0]["request_id"] == "msg_a" and mr[0]["actor"] == "agent"
    assert {r["agent"] for r in body} == {"claude"}
    days = list(csv.reader(io.StringIO(timeline_csv(tmp_path / "ev", "2026-11-05", "2026-11-05"))))
    assert [r[1] for r in days[1:]] == ["10", "11", "12"] and days[1][5] == "claude"   # agent from day one


def test_timeline_detail_cannot_start_a_spreadsheet_formula(tmp_path):
    from hidee.reconcile import timeline_csv
    log = EvidenceLog(tmp_path / "ev")
    log.append("s1", "prompt", {"excerpt": "=HYPERLINK(\"http://x\")", "length": 5, "sha256": "0" * 64,
                                "found": []})
    rows = list(csv.reader(io.StringIO(timeline_csv(tmp_path / "ev"))))
    assert rows[1][-1].startswith("'=")


def test_cli_timeline_writes_csv_and_manifest(tmp_path, capsys):
    import hashlib
    from hidee.cli import main
    home = tmp_path / "home"
    fill(home / "evidence")
    out = tmp_path / "t.csv"
    main(["evidence", "timeline", "--home", str(home), "--out", str(out)])
    assert "Chain intact" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "t.csv.manifest.json").read_text())
    assert manifest["chain_intact"] is True and manifest["rows"] == 12
    assert manifest["head_hash"] == EvidenceLog(home / "evidence").head()[1]
    assert manifest["files"]["t.csv"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert out.read_text().splitlines()[0] == ",".join(COLUMNS)


# ---- the Evidence page ----

def test_evidence_page_reconcile_section_and_downloads(tmp_path):
    client, app = make(tmp_path)
    fill(app.state.evidence.folder)
    app.state.evidence = EvidenceLog(app.state.evidence.folder)
    page = client.get("/evidence").text
    assert "Reconcile" in page and "claude-x" in page and "files.example.com" in page
    page = client.get("/evidence?from=2026-11-05&to=2026-11-05").text
    assert "claude-y" in page and "claude-x" not in page
    r = client.post("/evidence/reconcile", data={"csrf": app.state.csrf, "day_from": "2026-11-04", "day_to": ""})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert {"reconciliation.md", "reconciliation.csv", "manifest.json"} <= set(z.namelist())
        assert json.loads(z.read("manifest.json"))["chain_intact"] is True
    r = client.post("/evidence/timeline", data={"csrf": app.state.csrf})
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.read("timeline.csv").decode().startswith("utc_time,seq,")
    assert client.post("/evidence/timeline", data={"csrf": "wrong"}).status_code == 403
    assert client.post("/evidence/reconcile", data={"csrf": app.state.csrf, "day_from": "bad"}).status_code == 400
