"""Reconciliation and forensic timeline: the evidence log read as a ledger to compare with independent records.

The customer's evidence log is one book; the model provider's usage, billing, and audit logs, the destination
services' logs, and network logs (firewall, proxy, DNS) are others. `reconcile` totals the log per day so it can be
matched against those books: model requests by message id, outbound actions by destination, and connections by
host. `timeline_csv` writes one row per record for timeline tools. Both state the chain's status, since a
comparison is only meaningful on an intact chain.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from .evidence import _records, verify
from .learn import _mask

CHOICES = ("approve", "minimal", "reject", "blocked", "timed_out")
CHOICE_WORDS = {"approve": "approve", "minimal": "shorter version", "reject": "reject", "blocked": "blocked",
                "timed_out": "timed out"}
TOKENS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


def chain_status(folder: Path) -> dict:
    """Whether the whole log's chain is intact, how many records it holds, and its head."""
    ok, count, bad = verify(folder)
    head_seq, head_hash = 0, "0" * 64
    for r in _records(folder):
        head_seq, head_hash = r.get("seq", 0), r.get("hash", "")
    return {"intact": ok, "records": count, "broken_at": bad, "head_seq": head_seq, "head_hash": head_hash}


def chain_line(chain: dict) -> str:
    if chain["intact"]:
        return f"Chain intact: {chain['records']} records, head (record {chain['head_seq']}) {chain['head_hash']}"
    return (f"Chain BROKEN at record {chain['broken_at']}; do not rely on this comparison. "
            f"Last record {chain['head_seq']}, hash {chain['head_hash']}")


def _in_range(day: str, day_from: str | None, day_to: str | None) -> bool:
    return (not day_from or day >= day_from) and (not day_to or day <= day_to)


def reconcile(folder: Path, day_from: str | None = None, day_to: str | None = None) -> dict:
    """Totals per UTC day: model usage per model, outbound decisions per destination, connections per host.

    A model request counts on the day of its transcript time (when the request was made), other records on the
    day of their own time. `requests` lists every model request for matching one by one."""
    models: dict[tuple, dict] = {}
    actions: dict[tuple, dict] = {}
    conns: dict[tuple, dict] = {}
    requests: list[dict] = []
    for r in _records(folder):
        data, event = r.get("data") or {}, r.get("event")
        day = r["at"][:10]
        if event == "model_request":
            at = str(data.get("at") or r["at"])
            day = at[:10]
            if not _in_range(day, day_from, day_to):
                continue
            model = str(data.get("model", ""))
            row = models.setdefault((day, model), {"day": day, "model": model, "requests": 0,
                                                   **{k: 0 for k in TOKENS}})
            row["requests"] += 1
            for k in TOKENS:
                row[k] += int(data.get(k) or 0)
            requests.append({"day": day, "utc_time": at, "session": r["session"], "model": model,
                             "request_id": data.get("request_id", ""),
                             "provider_request_id": data.get("provider_request_id", ""),
                             "subagent": data.get("subagent", ""),
                             **{k: int(data.get(k) or 0) for k in TOKENS}})
        elif not _in_range(day, day_from, day_to):
            continue
        elif event == "decision":
            dest = str(data.get("recipient", ""))
            row = actions.setdefault((day, dest), {"day": day, "destination": dest, **{c: 0 for c in CHOICES},
                                                   "by": {}})
            choice = data.get("choice")
            if choice in row:
                row[choice] += 1
            by = str(data.get("by", ""))
            row["by"][by] = row["by"].get(by, 0) + 1
        elif event == "connection":
            host = str(data.get("host", ""))
            row = conns.setdefault((day, host), {"day": day, "host": host, "allowed": 0, "refused": 0,
                                                 "model": bool(data.get("model"))})
            row["allowed" if data.get("allowed") else "refused"] += 1
    return {"chain": chain_status(folder), "from": day_from, "to": day_to,
            "models": [models[k] for k in sorted(models)], "actions": [actions[k] for k in sorted(actions)],
            "connections": [conns[k] for k in sorted(conns)], "requests": requests}


def _by(counts: dict) -> str:
    return ", ".join(f"{who} {n}" for who, n in sorted(counts.items()))


def report_markdown(rep: dict) -> str:
    """The reconciliation report as Markdown, led by the chain's status."""
    n = lambda x: f"{x:,}"  # noqa: E731
    span = f"{rep['from'] or 'first day'} to {rep['to'] or 'last day'}"
    out = ["# Hidee reconciliation report", "",
           f"Days (UTC): {span}. Generated {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}.", "",
           chain_line(rep["chain"]), "",
           "## Model usage per day and model", "",
           "Per-request usage is recorded for Claude Code sessions. Match each request id (msg_...) with the "
           "provider's usage or audit records; the CSV lists them one by one.", ""]
    if rep["models"]:
        out += ["| Day | Model | Requests | Input tokens | Cache read | Cache creation | Output tokens |",
                "| :- | :- | -: | -: | -: | -: | -: |"]
        out += [f"| {r['day']} | {r['model']} | {r['requests']} | {r['input_tokens']} | "
                f"{r['cache_read_input_tokens']} | {r['cache_creation_input_tokens']} | {r['output_tokens']} |"
                for r in rep["models"]]
        total = {k: sum(r[k] for r in rep["models"]) for k in ("requests", *TOKENS)}
        out += ["", f"Total: {n(total['requests'])} requests, {n(total['input_tokens'])} input tokens, "
                f"{n(total['cache_read_input_tokens'])} cache read, {n(total['cache_creation_input_tokens'])} "
                f"cache creation, {n(total['output_tokens'])} output tokens."]
    else:
        out.append("No model requests recorded.")
    out += ["", "## Outbound actions per day and destination", ""]
    if rep["actions"]:
        out += ["| Day | Destination | Approve | Shorter version | Reject | Blocked | Timed out | Decided by |",
                "| :- | :- | -: | -: | -: | -: | -: | :- |"]
        out += [f"| {r['day']} | {r['destination']} | " + " | ".join(str(r[c]) for c in CHOICES)
                + f" | {_by(r['by'])} |" for r in rep["actions"]]
    else:
        out.append("No outbound actions recorded.")
    out += ["", "## Network connections per day and host", ""]
    if rep["connections"]:
        out += ["| Day | Host | Allowed | Refused | Model provider |", "| :- | :- | -: | -: | :- |"]
        out += [f"| {r['day']} | {r['host']} | {r['allowed']} | {r['refused']} | {'yes' if r['model'] else 'no'} |"
                for r in rep["connections"]]
    else:
        out.append("No connections recorded.")
    return "\n".join(out) + "\n"


CSV_COLUMNS = ["section", "day", "subject", "status", "by", "count", "utc_time", "session", "request_id",
               "provider_request_id", "subagent", *TOKENS]


def report_csv(rep: dict) -> str:
    """The report as one CSV: a row per model request, and a row per day, destination or host, and outcome."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rep["requests"]:
        w.writerow({"section": "model_request", "subject": r["model"], "count": 1, **r})
    for r in rep["actions"]:
        for c in CHOICES:
            if r[c]:
                w.writerow({"section": "action", "day": r["day"], "subject": r["destination"],
                            "status": CHOICE_WORDS[c], "count": r[c], "by": _by(r["by"])})
    for r in rep["connections"]:
        for status in ("allowed", "refused"):
            if r[status]:
                w.writerow({"section": "connection", "day": r["day"], "subject": r["host"], "status": status,
                            "count": r[status]})
    return buf.getvalue()


# ---- forensic timeline ----

TIMELINE_COLUMNS = ["utc_time", "seq", "session", "event", "actor", "agent", "tool", "destination", "decision",
                    "request_id", "sha256", "record_hash", "prev_hash", "detail"]
DETAIL = 160                      # characters of the detail column


def _actor(event: str, data: dict, user: str) -> str:
    if event == "decision":
        by = str(data.get("by", ""))
        return f"person:{data.get('user', '')}" if by == "person" else by
    if event == "connection":
        return "gate"
    if event == "prompt":
        return f"person:{user}" if user else "person"
    if event in ("tool_call", "tool_result", "tool_failure", "answer", "model_request"):
        return "agent"
    return "hidee"                # session_start, session_end, executed


def _detail(event: str, data: dict) -> str:
    if event == "session_start":
        text = (f"{data.get('name', '')} in {data.get('folder', '')}; safe={data.get('safe')} "
                f"learn={data.get('learn')}; {data.get('sign_in', '')}")
    elif event == "session_end":
        text = f"exit code {data.get('exit_code')}"
    elif event == "decision":
        found = ", ".join(data.get("found") or [])
        text = f"{data.get('kind', '')}: {data.get('reason', '')}" + (f" Found: {found}." if found else "")
        if data.get("action"):
            text += f" action {data['action']}"
    elif event == "executed":
        text = f"action {data.get('action', '')}: " + json.dumps(data.get("input"), ensure_ascii=False)
    elif event == "connection":
        text = str(data.get("url", "")) + (" (model provider)" if data.get("model") else "")
    elif event == "model_request":
        text = (f"{data.get('model', '')} at {data.get('at', '?')}: in {data.get('input_tokens', 0)}, cache read "
                f"{data.get('cache_read_input_tokens', 0)}, cache creation "
                f"{data.get('cache_creation_input_tokens', 0)}, out {data.get('output_tokens', 0)}")
        if data.get("provider_request_id"):
            text += f"; {data['provider_request_id']}"
        if data.get("subagent"):
            text += f"; sub-agent {data['subagent']}"
    elif event == "tool_call":
        text = f"{data.get('where', '')}: " + str((data.get("input") or {}).get("excerpt", ""))
    else:
        text = str(data.get("excerpt", ""))
    text = " ".join(_mask(text).split())
    if len(text) > DETAIL:
        text = text[:DETAIL] + "…"
    if text[:1] in ("=", "+", "-", "@"):      # a spreadsheet would read it as a formula
        text = "'" + text
    return text


def timeline_rows(folder: Path, day_from: str | None = None, day_to: str | None = None) -> list[dict]:
    rows, agents, users = [], {}, {}
    for r in _records(folder):
        data, event, session = r.get("data") or {}, r.get("event", ""), r.get("session", "")
        if event == "session_start":
            agents[session], users[session] = data.get("agent", ""), data.get("user", "")
        if not _in_range(r["at"][:10], day_from, day_to):
            continue
        if event == "decision":
            dest = data.get("recipient", "")
        elif event == "connection":
            dest = f"{data.get('host', '')}:{data.get('port', '')}"
        else:
            dest = ""
        decision = data.get("choice", "") if event == "decision" else (
            ("allowed" if data.get("allowed") else "refused") if event == "connection" else "")
        inner = data.get("input") if event == "tool_call" else None      # tool_call keeps a digest of its input
        sha = data.get("sha256") or (inner.get("sha256", "") if isinstance(inner, dict) else "")
        rows.append({"utc_time": r["at"], "seq": r["seq"], "session": session, "event": event,
                     "actor": _actor(event, data, users.get(session, "")), "agent": agents.get(session, ""),
                     "tool": data.get("tool", ""), "destination": dest, "decision": decision,
                     "request_id": data.get("request_id", "") if event == "model_request" else "",
                     "sha256": sha or "", "record_hash": r.get("hash", ""), "prev_hash": r.get("prev", ""),
                     "detail": _detail(event, data)})
    return sorted(rows, key=lambda row: row["seq"])


def _timeline_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, TIMELINE_COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def timeline_csv(folder: Path, day_from: str | None = None, day_to: str | None = None) -> str:
    """The forensic timeline: RFC 4180 CSV (CRLF line ends), one row per record, ISO 8601 UTC times."""
    return _timeline_text(timeline_rows(folder, day_from, day_to))


def timeline_files(folder: Path, day_from: str | None = None, day_to: str | None = None,
                   name: str = "timeline.csv") -> dict[str, bytes]:
    """The timeline CSV and its manifest (chain status, head hash, the CSV's sha256)."""
    rows = timeline_rows(folder, day_from, day_to)
    body = _timeline_text(rows).encode("utf-8")
    info = manifest(folder, {name: body}, day_from, day_to, rows=len(rows))
    return {name: body, "manifest.json": json.dumps(info, indent=2).encode("utf-8")}


def reconcile_files(folder: Path, day_from: str | None = None, day_to: str | None = None) -> dict[str, bytes]:
    """The reconciliation report as Markdown and CSV, with a manifest."""
    rep = reconcile(folder, day_from, day_to)
    files = {"reconciliation.md": report_markdown(rep).encode("utf-8"),
             "reconciliation.csv": report_csv(rep).encode("utf-8")}
    info = manifest(folder, files, day_from, day_to, requests=len(rep["requests"]))
    return {**files, "manifest.json": json.dumps(info, indent=2).encode("utf-8")}


def manifest(folder: Path, files: dict[str, bytes], day_from: str | None, day_to: str | None, **extra) -> dict:
    """The chain's status and head, the days covered, and the sha256 of each file, kept beside a CSV so the CSV
    itself stays clean for timeline tools."""
    chain = chain_status(folder)
    return {"format": "hidee-reconcile/1", "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "from": day_from, "to": day_to, "chain_intact": chain["intact"], "chain_records": chain["records"],
            "broken_at": chain["broken_at"], "head_seq": chain["head_seq"], "head_hash": chain["head_hash"],
            **extra, "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
            "check": "Run `hidee evidence verify` on the log or on an exported bundle; its head hash must equal "
                     "head_hash."}
