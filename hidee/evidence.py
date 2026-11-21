"""The evidence log: what happened in each session, kept by the party calling the AI service.

Hooks and the network gate write it, outside the model's context, so it does not depend on what the agent
remembers or reports. It records the person's judgment at each checkpoint and what each tool actually ran.

One JSON Lines file per day under ~/.hidee/evidence/. Every record carries the hash of the one before it, so an
edited, deleted, or reordered record breaks the chain from that point on. Long text is kept as an excerpt with
secrets masked, plus the length and sha256 of the whole, so a full copy produced later can be checked.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .learn import _clean, _text

GENESIS = "0" * 64


def _canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(record: dict) -> str:
    return hashlib.sha256(_canonical(record)).hexdigest()


def digest(value) -> dict:
    """An excerpt (secrets masked), the length, the sha256 of the whole text, and the kinds of data found."""
    raw = _text(value)
    excerpt, found = _clean(raw)
    return {"excerpt": excerpt, "length": len(raw), "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "found": found}


def _records(folder: Path):
    for path in sorted(Path(folder).glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def verify(folder: Path) -> tuple[bool, int, int | None]:
    """Recompute the chain: (intact, records read, seq of the first broken record or None)."""
    prev, expected, count = GENESIS, 1, 0
    for record in _records(folder):
        count += 1
        if record.get("prev") != prev or record.get("seq") != expected or record.get("hash") != _hash(record):
            return False, sum(1 for _ in _records(folder)), record.get("seq")
        prev, expected = record["hash"], expected + 1
    return True, count, None


class EvidenceLog:
    def __init__(self, folder: Path, clock=time.time) -> None:
        self.folder, self.clock = Path(folder), clock
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.seq, self.prev = 0, GENESIS
        for record in _records(self.folder):          # continue the chain after a restart
            self.seq, self.prev = record["seq"], record["hash"]

    def append(self, session: str, event: str, data: dict) -> dict:
        with self.lock:
            now = datetime.fromtimestamp(self.clock(), timezone.utc)
            record = {"seq": self.seq + 1, "at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                      "session": session, "event": event, "data": data, "prev": self.prev}
            record["hash"] = _hash(record)
            path = self.folder / f"{now:%Y-%m-%d}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)   # readable only by the user
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.seq, self.prev = record["seq"], record["hash"]
            return record

    def head(self) -> tuple[int, str]:
        with self.lock:
            return self.seq, self.prev


# ---- export, verify, summary ----

def _check(records, prev: str, expected: int) -> tuple[bool, int, int | None]:
    count = 0
    for record in records:
        count += 1
        if record.get("prev") != prev or record.get("seq") != expected or record.get("hash") != _hash(record):
            return False, count, record.get("seq")
        prev, expected = record["hash"], expected + 1
    return True, count, None


def export(folder: Path, out: Path, day_from: str | None = None, day_to: str | None = None) -> Path:
    """Write the records of the chosen days (YYYY-MM-DD, inclusive) to a zip with a manifest."""
    import zipfile
    from . import __version__
    files = [p for p in sorted(Path(folder).glob("*.jsonl"))
             if (not day_from or p.stem >= day_from) and (not day_to or p.stem <= day_to)]
    records = [json.loads(line) for p in files for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = {"format": "hidee-evidence/1", "hidee": __version__,
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "days": [p.stem for p in files], "records": len(records),
                "first_seq": records[0]["seq"] if records else None,
                "first_prev": records[0]["prev"] if records else None,
                "head_seq": records[-1]["seq"] if records else None,
                "head_hash": records[-1]["hash"] if records else None,
                "how_to_verify": "Each record's hash is sha256 of its canonical JSON without 'hash' (sorted keys, "
                                 "no spaces); 'prev' is the previous record's hash. Run: hidee evidence verify "
                                 "<this zip>."}
    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.name)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return out


def verify_bundle(path: Path) -> tuple[bool, int, int | None, str]:
    """Check an exported zip: the chain inside it, and that it ends at the manifest's head hash."""
    import zipfile
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
        records = [json.loads(line) for name in sorted(n for n in z.namelist() if n.endswith(".jsonl"))
                   for line in z.read(name).decode("utf-8").splitlines() if line.strip()]
    if not records:
        return True, 0, None, "The bundle has no records."
    ok, count, bad = _check(records, manifest["first_prev"], manifest["first_seq"])
    if not ok:
        return False, count, bad, f"The chain breaks at record {bad}."
    if count != manifest["records"] or records[-1]["hash"] != manifest["head_hash"]:
        return False, count, None, "The records do not end at the manifest's head hash."
    return True, count, None, f"Chain intact: {count} records, head {manifest['head_hash'][:16]}…"


def sessions(folder: Path) -> list[dict]:
    """One row per session, newest first: name, agent, when it started and ended, and what was recorded."""
    rows: dict[str, dict] = {}
    for r in _records(folder):
        row = rows.setdefault(r["session"], {"session": r["session"], "name": r["session"], "agent": "",
                                             "started": r["at"], "ended": None, "records": 0, "decisions": 0,
                                             "person_decisions": 0, "connections": 0})
        row["records"] += 1
        if r["event"] == "session_start":
            row.update(name=r["data"].get("name", r["session"]), agent=r["data"].get("agent", ""), started=r["at"])
        elif r["event"] == "session_end":
            row["ended"] = r["at"]
        elif r["event"] == "decision":
            row["decisions"] += 1
            row["person_decisions"] += r["data"].get("by") == "person"
        elif r["event"] == "connection":
            row["connections"] += 1
        row["last"] = r["at"]
    return sorted(rows.values(), key=lambda row: row["last"], reverse=True)


def _session_requests(path: Path) -> list[dict]:
    """The session's model requests: its own transcript, then each sub-agent's (Claude Code keeps those in
    <session>/subagents/agent-*.jsonl beside the transcript), marked with the sub-agent's file name."""
    from .learn import model_requests, safe_transcript
    out = list(model_requests(path) or [])
    for sub in sorted(path.with_suffix("").glob("subagents/*.jsonl")):
        if safe_transcript(sub) is not None:
            out += [{**r, "subagent": sub.stem} for r in model_requests(sub) or []]
    return out


class RequestLedger:
    """Writes one `model_request` record per model request of a Claude Code session, read from its transcript.

    The message id is the key the model provider also holds, so the customer can match each request against the
    provider's usage and billing records. Ids already recorded for a session are remembered (and read back from
    the log after a restart), so a turn reported twice does not record a request twice."""

    FOLLOW_UP_S = 3.0      # Claude Code may write a turn's last message to the transcript just after Stop

    def __init__(self, log: EvidenceLog) -> None:
        self.log = log
        self.follow_up = self.FOLLOW_UP_S
        self.lock = threading.Lock()
        self.paths: dict[str, Path] = {}
        self.seen: dict[str, set[str]] = {}

    def note(self, session: str, value) -> None:
        """Remember the session's transcript if the hook reported a safe one (see learn.safe_transcript)."""
        from .learn import safe_transcript
        path = safe_transcript(value)
        if path is not None:
            self.paths[session] = path

    def at_stop(self, session: str) -> int:
        """Record now, and once more shortly after, for a last message written after the Stop hook ran."""
        added = self.record(session)
        timer = threading.Timer(self.follow_up, self.record, args=(session,))
        timer.daemon = True
        timer.start()
        return added

    def record(self, session: str) -> int:
        """Append the session's requests not recorded before; return how many were added."""
        path = self.paths.get(session)
        if path is None:
            return 0
        with self.lock:
            if session not in self.seen:
                self.seen[session] = {r["data"].get("request_id") for r in _records(self.log.folder)
                                      if r["session"] == session and r["event"] == "model_request"}
            seen = self.seen[session]
            added = 0
            for request in _session_requests(path):
                if request["request_id"] not in seen:
                    self.log.append(session, "model_request", request)
                    seen.add(request["request_id"])
                    added += 1
            return added
