"""The evidence log: an append-only, hash-chained record kept by the party calling the AI service."""
import json
import threading
import time

from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.evidence import EvidenceLog, digest, verify
from hidee.hook_api import APP_RULES


def clock_at(t):
    return lambda: t


def test_records_chain_together(tmp_path):
    log = EvidenceLog(tmp_path, clock=clock_at(1_793_800_000.0))
    a = log.append("s1", "prompt", {"text": "hi"})
    b = log.append("s1", "tool_call", {"tool": "Read"})
    assert a["seq"] == 1 and b["seq"] == 2 and b["prev"] == a["hash"]
    assert verify(tmp_path) == (True, 2, None)
    assert log.head() == (2, b["hash"])


def test_editing_or_deleting_a_record_breaks_the_chain(tmp_path):
    log = EvidenceLog(tmp_path, clock=clock_at(1_793_800_000.0))
    for i in range(4):
        log.append("s1", "prompt", {"text": str(i)})
    day = next(tmp_path.glob("*.jsonl"))
    lines = day.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["data"]["text"] = "changed"
    day.write_text("\n".join([lines[0], json.dumps(edited)] + lines[2:]) + "\n")
    assert verify(tmp_path) == (False, 4, 2)
    day.write_text("\n".join([lines[0]] + lines[2:]) + "\n")            # record 2 removed
    assert verify(tmp_path)[0] is False and verify(tmp_path)[2] == 3


def test_the_chain_continues_after_a_restart_and_across_days(tmp_path):
    first = EvidenceLog(tmp_path, clock=clock_at(1_793_800_000.0))
    first.append("s1", "prompt", {"text": "day one"})
    second = EvidenceLog(tmp_path, clock=clock_at(1_793_800_000.0 + 86_400))
    rec = second.append("s1", "prompt", {"text": "day two"})
    assert rec["seq"] == 2 and len(list(tmp_path.glob("*.jsonl"))) == 2
    assert verify(tmp_path) == (True, 2, None)


def test_concurrent_appends_keep_one_chain(tmp_path):
    log = EvidenceLog(tmp_path)
    threads = [threading.Thread(target=lambda: [log.append("s", "x", {}) for _ in range(25)]) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert verify(tmp_path) == (True, 100, None)


def test_digest_keeps_an_excerpt_and_a_hash_of_the_whole_text():
    key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    d = digest(f"API_KEY={key}\n" + "x" * 3000)
    assert key not in d["excerpt"] and d["length"] > 3000 and len(d["sha256"]) == 64
    assert "api_key" in d["found"]


# ---- what the app records ----

def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    return TestClient(app), app


def records(app):
    out = []
    for f in sorted((app.state.home / "evidence").glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text().splitlines()]
    return out


def H(app, session="s1"):
    return {"X-Hidee-Token": app.state.hook_token, "X-Hidee-Session": session}


def test_every_session_records_calls_checks_and_decisions(tmp_path):
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
    client.post("/hook/pretooluse", json={"tool_name": "WebSearch",
                                          "tool_input": {"query": "maria.lopez@example.org dentist"}}, headers=H(app))
    t.join()
    client.post("/hook/pretooluse", json={"tool_name": "Read", "tool_input": {"file_path": "notes.txt"}},
                headers=H(app))
    events = [(r["event"], r["session"]) for r in records(app)]
    assert ("tool_call", "s1") in events and ("decision", "s1") in events and ("executed", "s1") in events
    decision = next(r for r in records(app) if r["event"] == "decision")["data"]
    assert decision["by"] == "person" and decision["choice"] == "minimal" and "email" in decision["found"]
    assert decision["seconds"] >= 0 and decision["user"]
    executed = next(r for r in records(app) if r["event"] == "executed")["data"]
    assert executed["input"] == {"query": "[email] dentist"}
    local = [r for r in records(app) if r["event"] == "tool_call" and r["data"]["tool"] == "Read"]
    assert local and local[0]["data"]["where"] == "local"
    assert verify(app.state.home / "evidence") == (True, len(records(app)), None)


def test_prompts_results_and_answers_are_recorded_for_every_session(tmp_path):
    client, app = make(tmp_path)                                   # not a learn session
    client.post("/hook/learn", json={"hook_event_name": "UserPromptSubmit", "prompt": "hello"}, headers=H(app))
    client.post("/hook/learn", json={"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {},
                                     "tool_response": "file text"}, headers=H(app))
    client.post("/hook/learn", json={"hook_event_name": "Stop", "last_assistant_message": "done"}, headers=H(app))
    kinds = [r["event"] for r in records(app)]
    assert kinds == ["prompt", "tool_result", "answer"]
    assert records(app)[1]["data"]["sha256"] and records(app)[1]["data"]["tool"] == "Read"
    assert client.get("/learn/s1").json()["events"] == []          # the learn view stays off


def test_every_session_registers_the_watching_hooks():
    from hidee.sessions import LEARN_EVENTS, gemini_settings, hook_settings
    assert set(LEARN_EVENTS) <= set(hook_settings()["hooks"])
    assert {"BeforeAgent", "AfterAgent"} <= set(gemini_settings()["hooks"])


def test_session_start_and_end_are_recorded(tmp_path):
    from hidee.sessions import SessionManager
    log = EvidenceLog(tmp_path / "ev")
    m = SessionManager(tmp_path, "http://x", "t")
    m.on_event = log.append
    s = m.create("Lab 1", str(tmp_path), "claude", argv=["/bin/sh", "-c", "exit 3"], safe=True,
                 env={"HIDEE_KEY": "secret-key-1234"})
    for _ in range(100):
        if any(r["event"] == "session_end" for r in __import__("hidee.evidence", fromlist=["x"])._records(tmp_path / "ev")):
            break
        time.sleep(0.05)
    recs = [r for r in __import__("hidee.evidence", fromlist=["x"])._records(tmp_path / "ev")]
    start = next(r for r in recs if r["event"] == "session_start")["data"]
    assert start["agent"] == "claude" and start["safe"] is True and start["sign_in"] == "key saved in Hidee"
    assert "secret-key-1234" not in json.dumps(recs)
    assert next(r for r in recs if r["event"] == "session_end")["data"]["exit_code"] == 3


# ---- export, verify, summary ----

def fill(folder):
    days = [1_793_800_000.0, 1_793_800_000.0 + 86_400, 1_793_800_000.0 + 2 * 86_400]
    for i, t in enumerate(days):
        log = EvidenceLog(folder, clock=clock_at(t))
        log.append(f"s{i}", "session_start", {"name": f"Lab {i}", "agent": "claude"})
        log.append(f"s{i}", "decision", {"by": "person", "choice": "minimal"})
    return sorted(p.stem for p in folder.glob("*.jsonl"))


def test_export_a_range_and_verify_the_bundle(tmp_path):
    from hidee.evidence import export, verify_bundle
    days = fill(tmp_path / "ev")
    bundle = export(tmp_path / "ev", tmp_path / "out.zip", day_from=days[1], day_to=days[2])
    ok, count, bad, why = verify_bundle(bundle)
    assert ok and count == 4 and bad is None, why
    import zipfile
    with zipfile.ZipFile(bundle) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["first_seq"] == 3 and manifest["head_seq"] == 6 and manifest["records"] == 4


def test_a_tampered_bundle_fails(tmp_path):
    import zipfile
    from hidee.evidence import export, verify_bundle
    fill(tmp_path / "ev")
    bundle = export(tmp_path / "ev", tmp_path / "out.zip")
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        files = {n: z.read(n) for n in names}
    day = sorted(n for n in names if n.endswith(".jsonl"))[0]
    files[day] = files[day].replace(b'"minimal"', b'"approve"')
    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    ok, _, bad, _ = verify_bundle(tmp_path / "bad.zip")
    assert not ok and bad == 2


def test_session_summary(tmp_path):
    from hidee.evidence import sessions
    fill(tmp_path / "ev")
    rows = sessions(tmp_path / "ev")
    assert [r["session"] for r in rows] == ["s2", "s1", "s0"]                  # newest first
    assert rows[0]["name"] == "Lab 2" and rows[0]["records"] == 2 and rows[0]["person_decisions"] == 1


def test_cli_verify_and_export(tmp_path, capsys):
    from hidee.cli import main
    home = tmp_path / "home"
    fill(home / "evidence")
    main(["evidence", "verify", "--home", str(home)])
    assert "intact" in capsys.readouterr().out
    main(["evidence", "export", "--home", str(home), "--out", str(tmp_path / "b.zip")])
    main(["evidence", "verify", str(tmp_path / "b.zip")])
    assert "intact" in capsys.readouterr().out


def test_evidence_page_and_download(tmp_path):
    client, app = make(tmp_path)
    app.state.evidence.append("s1", "session_start", {"name": "Lab 1", "agent": "claude"})
    page = client.get("/evidence").text
    assert "Chain intact" in page and "Lab 1" in page and app.state.evidence.head()[1][:12] in page
    r = client.post("/evidence/export", data={"csrf": app.state.csrf})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert client.post("/evidence/export", data={"csrf": "wrong"}).status_code == 403
