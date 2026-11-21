"""Owner dashboard: plain words, concrete pictures, fixed colour meanings."""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .engine import ActionError, reached
from .icons import PATHS, icon

BASE = Path(__file__).parent / "webui"
STATIC_DIR = BASE / "static"
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["icon"] = icon


def asset(path: str) -> str:
    """A static URL that changes whenever the file changes, so browsers never keep an old copy."""
    try:
        stamp = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{path}?v={stamp}"


templates.env.globals["asset"] = asset
router = APIRouter()
ATTRIBUTION = re.compile(r"(?i)[^\n.]*\b(?:sent|generated|written|drafted|created|composed)\s+(?:with|using|by|via)\s+"
                         r"(?:claude(?: code)?|gemini(?: cli)?|codex|chatgpt|an? ai|ai assistant)[^\n.]*|"
                         r"co-authored-by:[^\n]*")

KIND_WORDS = {"search": "Search", "remember": "Save to memory", "send_message": "Send message", "recommend": "Suggest",
              "fetch": "Fetch page", "use_tool": "Use tool", "network": "Connect",
              "access": "Local access"}
MARKS = {"needed": ("ok", "✓", "allowed by your rules"), "check": ("warn", "⚠", "not covered by your rules"),
         "forbidden": ("bad", "⛔", "never allowed here")}
OUTCOMES = {
    "allowed": ("ok", "", "Allowed"),
    "minimized": ("ok", "", "Sent shorter version"),
    "blocked": ("bad", "", "Blocked"),
    "waiting": ("wait", "", "Waiting for you"),
    "timed_out": ("wait", "", "Timed out"),
    "execution_failed": ("bad", "", "Failed to run"),
    "received": ("wait", "", "Received"),
}


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M:%S")


def view(rec, rules, agent_names: dict, requested: set | None = None) -> dict:
    """`requested`: field keys of detected details the user's own request names; the shorter version keeps them."""
    rows = []
    requested = requested or set()
    for name, f in rec.fields.items():
        cls, icon, word = MARKS[rec.marks.get(f["category"], "check")]
        short = (rec.minimal or {}).get(name)
        if f["category"] != "plain" and rec.minimal:
            # A detected detail: removed in the shorter version unless its text still runs (a destination field).
            leaf = (rec.minimal.get(name.rsplit(": ", 1)[0]) or {}).get("value")
            if leaf is not None and f["value"] in leaf:
                short = f
        rows.append({"name": name, "category": rules.category_label(f["category"]), "cls": cls, "icon": icon,
                     "word": word, "original": f["value"], "minimal": short["value"] if short else None,
                     "dropped": rec.minimal is not None and short is None,
                     "kept_requested": rec.minimal is not None and name in requested and short is not None,
                     "kept_destination": (rec.minimal is not None and f["category"] != "plain" and short is not None
                                          and name not in requested),
                     "changed": short is not None and short["value"] != f["value"]})
    out_cls, out_icon, out_word = OUTCOMES.get(rec.status, ("wait", "?", rec.status))
    return {"id": rec.id, "agent": agent_names.get(rec.agent_id) or rules.agents.get(rec.agent_id, rec.agent_id),
            "kind_key": rec.kind if rec.kind in PATHS else "send_message", "kind": KIND_WORDS.get(rec.kind, rec.kind),
            "recipient": rules.recipient_label(rec.recipient), "recipient_id": rec.recipient,
            "rows": rows, "reason": rec.reason,
            "has_minimal": rec.minimal is not None, "status": rec.status, "out_cls": out_cls,
            "out_icon": out_icon, "out_word": out_word, "when": _when(rec.created_at),
            "decided_by": rec.decided_by, "executed": rec.executed,
            "reached": sorted(reached(rec))}      # field names whose content ran (see engine.reached)


def _names(state) -> dict:
    """Readable names for agents with tokens and for app sessions."""
    return {**{a["id"]: a["name"] for a in state.store.list_agents()}, **state.session_names}


def _views(request: Request, status: str | None = None) -> list[dict]:
    state = request.app.state
    names = _names(state)
    rules = state.rules()
    out = []
    for r in state.store.list_actions(status=status):
        v = view(r, rules, names, getattr(state, "kept_requested", {}).get(r.id))
        v["is_message"] = r.id in getattr(state, "message_actions", set())
        # Wording the user did not write that names an AI as author: shown as a neutral note, never removed.
        text = " ".join(f["value"] for f in r.fields.values() if f["category"] == "plain")
        v["attribution"] = ATTRIBUTION.findall(text) if v["is_message"] else []
        out.append(v)
    return out


def _page(request: Request, name: str, **context) -> HTMLResponse:
    state = request.app.state
    demos = None
    if state.demo:
        from .demo import DEMOS
        demos = [(key, d["title"]) for key, d in DEMOS.items()]
    embed = request.query_params.get("embed") == "1"
    return templates.TemplateResponse(request, name, {"csrf": state.csrf, "demo": state.demo, "demos": demos,
                                                      "demo_name": state.demo_name, "embed": embed,
                                                      "app_mode": state.app_mode, "online": getattr(state, "online", False),
                                                      "site_url": getattr(state, "site_url", "https://hidee.net"),
                                                      **context})


def _next_decision(request: Request) -> dict:
    """One decision per page: the oldest waiting action, and how many are waiting in total."""
    waiting = _views(request, "waiting")[::-1]
    return {"cards": waiting[:1], "total": len(waiting)}


@router.get("/", response_class=HTMLResponse)
def review_page(request: Request):
    if request.app.state.app_mode and request.query_params.get("embed") != "1":
        return RedirectResponse("/workspace", status_code=307)   # in the app, review lives in the checkpoint pane
    return _page(request, "review.html", active="review", **_next_decision(request))


@router.get("/fragments/review", response_class=HTMLResponse)
def review_fragment(request: Request):
    return _page(request, "_cards.html", **_next_decision(request))


@router.get("/app", response_class=HTMLResponse)
def app_preview(request: Request):
    """A scripted preview of the Hidee app: sessions, a terminal, and the checkpoint side by side."""
    return templates.TemplateResponse(request, "app_preview.html", {"csrf": request.app.state.csrf})


@router.get("/styles", response_class=HTMLResponse)
def style_board(request: Request):
    """Three visual directions with identical content, for choosing the app's look."""
    return templates.TemplateResponse(request, "styles.html", {})


@router.get("/history")
def history_page():
    return RedirectResponse("/dashboard", status_code=307)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, id: str | None = None):
    from .flow import SENSITIVE, render_flow, render_overview, timeline
    state = request.app.state
    cases = _views(request)[::-1]                     # oldest first
    if not cases and not id:
        return _page(request, "dashboard.html", cases=[], active="dashboard", rows=[], columns=[])
    sensitive = [c for c in cases if c["status"] in SENSITIVE]
    steps = {c["id"]: i for i, c in enumerate(sensitive, 1)}
    if id:
        rec = state.store.get_action(id)
        if rec is None:
            raise HTTPException(404, "No such case")
    else:
        waiting = [c for c in sensitive if c["status"] == "waiting"]
        pick = waiting[0] if waiting else (sensitive[-1] if sensitive else cases[-1])
        rec = state.store.get_action(pick["id"])
    rules = state.rules()
    names = _names(state)
    selected = view(rec, rules, names, getattr(state, "kept_requested", {}).get(rec.id))
    received = [(rules.category_label(cat), n) for who, cat, n in state.store.disclosure_counts()
                if who == rec.recipient]
    for c in sensitive:
        flagged = [r["category"] for r in c["rows"] if r["cls"] != "ok"] or [r["category"] for r in c["rows"]]
        flagged = list(dict.fromkeys(flagged))           # two fields of one kind name that kind once
        c["step"], c["flagged"] = steps[c["id"]], ", ".join(flagged)
    rows, columns = _knows(state)
    return _page(request, "dashboard.html", active="dashboard", cases=cases[::-1], sensitive=sensitive,
                 rows=rows, columns=columns,
                 step=steps.get(rec.id), selected=selected,
                 overview=render_overview(cases, steps, set(rules.recipients), rec.id),
                 flow=render_flow(rec, selected), events=timeline(state.store.audit_for(rec.id), selected, _when),
                 received=received)


def _knows(state) -> tuple[list[dict], list[tuple[str, str]]]:
    """Who knows what: every recipient, the kinds of information it has received, and how often."""
    rules = state.rules()
    summary = state.store.disclosure_summary()
    categories = sorted({r["category"] for r in summary}, key=rules.category_label)
    people: dict[str, dict] = {}
    for r in summary:
        p = people.setdefault(r["recipient"], {"key": r["recipient"], "label": rules.recipient_label(r["recipient"]),
                                               "cells": {}, "total": 0, "last_at": 0.0})
        p["cells"][r["category"]] = r
        p["total"] += r["n"]
        p["last_at"] = max(p["last_at"], r["last_at"])
    top = max((r["n"] for r in summary), default=1)
    for p in people.values():
        p["last"] = _when(p["last_at"])
        for c in p["cells"].values():
            c["level"] = 1 + min(2, (3 * (c["n"] - 1)) // max(top, 1))   # 1 light .. 3 dark
    rows = sorted(people.values(), key=lambda p: (-p["total"], p["label"]))
    return rows, [(c, rules.category_label(c)) for c in categories]


@router.get("/knows")
def knows_page():
    """Who knows what is now a section of the What left page."""
    return RedirectResponse("/dashboard#knows", status_code=307)


def _check_csrf(request: Request, csrf: str) -> None:
    if not secrets.compare_digest(csrf, request.app.state.csrf):
        raise HTTPException(403, "This form has expired. Reload the page and try again.")


@router.post("/demo/replay")
def replay_demo(request: Request, csrf: str = Form(...), name: str = Form("")):
    """Start the current demo (or the one named) again from a clean slate."""
    state = request.app.state
    if not state.demo:
        raise HTTPException(404, "Not found")
    _check_csrf(request, csrf)
    from .demo import DEMOS, load_demo
    name = name or state.demo_name or "bakery"
    if name not in DEMOS:
        raise HTTPException(400, "Unknown demo")
    load_demo(request.app, name)
    return RedirectResponse("/", status_code=303)


CONFIRM = {
    "draft": ("Save this message as a draft for you to send?", "Yes, save it as a draft"),
    "minimal": ("Send the shorter version to {recipient}?", "Yes, send the shorter version"),
    "approve": ("Send it as written to {recipient}?", "Yes, send it"),
    "reject": ("Reject this request?", "Yes, reject it"),
}


@router.get("/confirm/{action_id}", response_class=HTMLResponse)
def confirm_page(request: Request, action_id: str, choice: str = ""):
    """A second step before anything happens, so a slip of the mouse cannot send or reject by accident."""
    if choice not in CONFIRM:
        raise HTTPException(400, "Unknown choice")
    state = request.app.state
    rec = state.store.get_action(action_id)
    if rec is None:
        raise HTTPException(404, "No such request")
    problem = None
    if rec.status != "waiting":
        problem = "This request has already been decided."
    elif choice == "minimal" and rec.minimal is None:
        problem = "This request has no shorter version."
    if problem:
        return templates.TemplateResponse(request, "confirm.html", {
            "csrf": state.csrf, "demo": state.demo, "problem": problem,
            "embed": request.query_params.get("embed") == "1"}, status_code=409)
    rules = state.rules()
    case = view(rec, rules, _names(state), getattr(state, "kept_requested", {}).get(rec.id))
    rows = {r["name"]: r for r in case["rows"]}
    if choice in ("reject", "draft"):
        send, left = [], []
    elif choice == "approve":
        send, left = [(rows[n], rec.fields[n]["value"]) for n in rec.fields], []
    else:
        send = [(rows[n], v["value"]) for n, v in rec.minimal.items()]
        left = [(rows[n], rec.fields[n]["value"]) for n in rec.fields
                if n not in rec.minimal or rec.minimal[n]["value"] != rec.fields[n]["value"]]
    question, yes = CONFIRM[choice]
    if rec.kind == "access" and choice == "approve":   # a local step: nothing is sent, something runs here
        question, yes = "Allow this step on your computer?", "Yes, allow it"
    return _page(request, "confirm.html", case=case, choice=choice, send=send, left=left,
                 question=question.format(recipient=case["recipient"]),
                 yes=yes, active="review")


@router.post("/decide/{action_id}")
def decide(request: Request, action_id: str, choice: str = Form(...), csrf: str = Form(...), embed: str = Form(""),
           note: str = Form("")):
    _check_csrf(request, csrf)
    try:
        request.app.state.engine.decide(action_id, choice, decided_by="owner", note=note if choice == "reject" else "")
    except ActionError as e:
        raise HTTPException(409, str(e)) from None
    return RedirectResponse("/?embed=1" if embed == "1" else "/", status_code=303)


# ---- Step pop-up: one step as a data-flow picture, for the workspace and the dashboard ----

@router.get("/step/{action_id}", response_class=HTMLResponse)
def step_popup(request: Request, action_id: str):
    from .detect import risks_of
    from .flow import render_flow, timeline
    state = request.app.state
    rec = state.store.get_action(action_id)
    if rec is None:
        raise HTTPException(404, "No such step")
    rules = state.rules()
    case = view(rec, rules, _names(state), getattr(state, "kept_requested", {}).get(rec.id))
    executed = rec.executed or {}
    got = set(case["reached"])
    # Held back: details that did not run (a shorter version's removed details), and whole fields left out.
    left = [f["value"] for n, f in rec.fields.items()
            if rec.status in ("allowed", "minimized") and n not in got and (f["category"] != "plain" or n not in executed)]
    return templates.TemplateResponse(request, "_step.html", {
        "eyebrow": "Checked step", "title": f"{case['agent']} → {case['recipient']}",
        "out_cls": case["out_cls"], "out_word": case["out_word"], "when": case["when"],
        "risks": risks_of(f["category"] for f in rec.fields.values()),
        "flow": render_flow(rec, case), "reason": rec.reason,
        "events": timeline(state.store.audit_for(rec.id), case, _when),
        "sent_title": "What was sent", "sent": list(executed.values()), "left": left,
        "nothing_sent": "Nothing has been sent." if rec.status == "waiting" else "Nothing was sent.",
        "waiting": rec.status == "waiting",
        "decide_href": "/workspace" if state.app_mode else "/",
        "dashboard_href": f"/dashboard?id={rec.id}#zoom"})


@router.get("/step/event/{seq}", response_class=HTMLResponse)
def event_popup(request: Request, seq: int):
    """A step that was only recorded (local work, a connection): the same picture, without a decision."""
    from .detect import LABELS
    from .flow import render_event_flow
    state = request.app.state
    if not state.app_mode:
        raise HTTPException(404, "Not found")
    event = next((e for e in state.activity.since(seq - 1) if e["seq"] == seq), None)
    if event is None:
        raise HTTPException(404, "This step is no longer in memory")
    if event.get("action"):
        return step_popup(request, event["action"])
    names = _names(state)
    agent = names.get(f"session:{event['session']}", event["session"])
    place = {"this-computer": "This computer", "model-provider": "Model provider"}.get(
        event["recipient"], state.rules().recipient_label(event["recipient"]))
    word = {"local": ("wait", "Local work"), "allowed": ("ok", "Passed"), "blocked": ("bad", "Refused")}
    out_cls, out_word = word.get(event["status"], ("wait", event["status"]))
    return templates.TemplateResponse(request, "_step.html", {
        "eyebrow": "Recorded step", "title": event["text"], "out_cls": out_cls, "out_word": out_word,
        "when": _when(event["at"]), "risks": event.get("risks", []),
        "flow": render_event_flow(event, agent, place, LABELS),
        "reason": "" if event["kind"] != "connection" else
                  "Seen by the network gate: Hidee knows where this connection went, not what it carried.",
        "events": [{"when": _when(event["at"]), "text": f"{agent}: {event['text']}"}],
        "sent_title": "Details", "sent": [], "left": [],
        "nothing_sent": "Ordinary work on this computer; nothing left it." if event["kind"] == "local"
                        else "Content is not recorded for this kind of step.",
        "waiting": False, "decide_href": "", "dashboard_href": ""})
