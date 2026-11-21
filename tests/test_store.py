from hidee.store import ActionRecord, Store


def record(**over):
    base = dict(id="a1", agent_id="shop", kind="send_message", recipient="parcel",
                fields={"msg": {"value": "hi", "category": "delivery"}}, minimal=None, status="waiting",
                reason="r", marks={"delivery": "needed"}, decided_by=None, executed=None,
                created_at=1.0, decided_at=None, expires_at=10.0)
    base.update(over)
    return ActionRecord(**base)


def test_action_roundtrip_and_update(tmp_path):
    s = Store(tmp_path / "d.db")
    s.insert_action(record())
    s.update_action("a1", status="allowed", executed={"msg": "hi"})
    got = s.get_action("a1")
    assert got.status == "allowed" and got.executed == {"msg": "hi"} and got.fields["msg"]["value"] == "hi"
    assert [r.id for r in s.list_actions(status="allowed")] == ["a1"]
    assert s.get_action("missing") is None


def test_expired_waiting_ids(tmp_path):
    s = Store(tmp_path / "d.db")
    s.insert_action(record(id="old", expires_at=5.0))
    s.insert_action(record(id="new", expires_at=50.0))
    assert s.expired_waiting_ids(now=10.0) == ["old"]


def test_grant_is_used_once(tmp_path):
    s = Store(tmp_path / "d.db")
    s.insert_grant("g1", "a1", "original", "abc", 99.0)
    assert s.use_grant("g1") is True
    assert s.use_grant("g1") is False
    assert s.get_grant("g1")["used"] == 1
    assert [g["id"] for g in s.grants_for("a1")] == ["g1"]


def test_disclosure_counts(tmp_path):
    s = Store(tmp_path / "d.db")
    s.add_disclosures("a1", "parcel", ["delivery", "routine"], 1.0)
    s.add_disclosures("a2", "parcel", ["delivery"], 2.0)
    assert s.disclosure_counts() == [("parcel", "delivery", 2), ("parcel", "routine", 1)]


def test_audit_keeps_metadata(tmp_path):
    s = Store(tmp_path / "d.db")
    s.audit("submitted", "a1", {"kind": "send_message"}, 1.0)
    assert s.audit_log()[0]["detail"] == {"kind": "send_message"}


def test_data_survives_reopen(tmp_path):
    Store(tmp_path / "d.db").insert_action(record())
    assert Store(tmp_path / "d.db").get_action("a1") is not None


def test_disclosure_summary_keeps_last_time_and_action(tmp_path):
    s = Store(tmp_path / "d.db")
    s.add_disclosures("a1", "parcel", ["delivery", "routine"], 1.0)
    s.add_disclosures("a2", "parcel", ["delivery"], 2.0)
    s.add_disclosures("a3", "mail", ["email"], 3.0)
    rows = {(r["recipient"], r["category"]): r for r in s.disclosure_summary()}
    assert rows[("parcel", "delivery")] == {"recipient": "parcel", "category": "delivery", "n": 2,
                                            "last_at": 2.0, "last_action": "a2"}
    assert rows[("mail", "email")]["n"] == 1 and len(rows) == 3


def test_reads_are_safe_while_other_threads_write(tmp_path):
    # Several sessions wait on the checkpoint at once, each polling its action from its own thread. Every read must
    # return the whole row: a read that came back empty once crashed a waiting thread and lost its timeout event.
    import threading
    import time as _time
    from hidee.store import ActionRecord, Store
    store = Store(tmp_path / "s.db")
    ids, problems = [], []

    def rec(i):
        return ActionRecord(id=f"a{i}", agent_id="s", kind="search", recipient="r", fields={"q": {"value": "x"}},
                            minimal=None, status="waiting", reason="", marks={}, decided_by=None, executed=None,
                            created_at=_time.time(), decided_at=None, expires_at=_time.time() + 600)
    for i in range(20):
        store.insert_action(rec(i))
        ids.append(f"a{i}")

    def reader(action_id):
        for _ in range(300):
            r = store.get_action(action_id)
            if r is None or r.created_at is None or r.id != action_id:
                problems.append((action_id, r and r.id))
            store.list_actions(status="waiting")

    def writer():
        for n in range(300):
            store.update_action(f"a{n % 20}", reason=f"r{n}")
            store.audit("tick", None, {}, _time.time())
    threads = [threading.Thread(target=reader, args=(i,)) for i in ids[:8]] + [threading.Thread(target=writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert problems == []
