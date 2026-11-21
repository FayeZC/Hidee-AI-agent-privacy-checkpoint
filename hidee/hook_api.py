"""The endpoint Claude Code's PreToolUse hook calls. It answers only after the rules or the owner decide."""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from .detect import LABELS
from .engine import ActionError
from .live import LOCAL, summary
from .store import ActionRecord
from .toolmap import Mapped, map_access, map_call

router = APIRouter()

APP_RULES = """\
# Hidee app rules. Every web search, page fetch, MCP tool call, network command, and message sent from a Claude Code
# session is checked against this file, and so are local steps that reach for credentials, run privileged
# commands, or leave the session folder. "*" stands for any session or any site.
# Ordinary text goes through; anything else the detector finds waits for you; forbidden items are blocked.
agents:
  "*": Claude Code
recipients:
  "*": any site
  web-search: Web search
  this-computer: this computer
categories:
  plain: ordinary text
  email: email address
  phone: phone number
  card_number: card number
  api_key: API key or token
  government_id: government ID
  health: health information
  money: money or pay
  internal: internal information
  credential_file: credential file
  privileged_command: privileged command
  outside_folder: outside the session folder
  known_data: known data from your sensitive files
  mental_health: mental health
  sexual_orientation: sexual orientation or sex life
  religion: religion or beliefs
  political: political opinions
  trade_union: trade-union membership
  ethnic_origin: racial or ethnic origin
  genetic_biometric: genetic or biometric data
  finances: finances or debt
  legal: legal trouble
  immigration: immigration status
  family: family or relationship matters
  known_fact: restates your sensitive files
rules:
  - {agent: "*", kind: search, recipient: web-search, allow: [plain], forbid: [api_key, card_number, government_id]}
  - {agent: "*", kind: fetch, recipient: "*", allow: [plain], forbid: [card_number]}
  - {agent: "*", kind: use_tool, recipient: "*", allow: [plain]}
  - {agent: "*", kind: network, recipient: "*", allow: [plain], forbid: [card_number]}
  - {agent: "*", kind: send_message, recipient: "*", allow: [plain], forbid: [card_number]}
  - {agent: "*", kind: access, recipient: this-computer, allow: [plain, outside_folder]}
"""


def _decision(decision: str, reason: str, **extra) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason, **extra}}


DRAFT_NOTE = ("Hidee: the user chose to save this message as a draft and send it themselves. It was not sent. If a "
              "draft tool is available (for example save_draft), save the same message there; otherwise give the user "
              "the full text. Do not send it another way, and tell the user it is ready for them to send.")
HOLD_MESSAGE = ("A message to a person waits for you: send it now, save it as a draft for you to send, send a "
                "shorter version, or reject it.")


def reply(rec: ActionRecord, mapped: Mapped) -> dict:
    if rec.status == "allowed" and rec.decided_by == "rules":
        return {}                     # no opinion: Claude Code's own permission flow still applies
    if rec.status == "allowed":
        return _decision("allow", "Approved in Hidee.")
    if rec.status == "drafted":
        return _decision("deny", DRAFT_NOTE)
    if rec.status == "minimized":
        updated = mapped.rebuild(rec.minimal)
        choice_detail = removal_choice(mapped, rec)
        removed = (sorted({e["category"] for e in choice_detail["removed"]}) if choice_detail
                   else sorted({f["category"] for f in rec.fields.values()} - {"plain"}))
        labels = ", ".join(LABELS.get(c, c) for c in removed)
        context = (f"Hidee, the user's privacy checkpoint, removed sensitive details ({labels}) from this tool call "
                   f"before it ran, with the user's approval. The tool received this input instead: "
                   f"{json.dumps(updated, ensure_ascii=False)}. Placeholders such as [email] or REDACTED_EMAIL "
                   "mark the removed parts. Do not try to send the removed details another way.")
        return _decision("allow", "Approved in Hidee as a shorter version; sensitive parts were removed.",
                         updatedInput=updated, additionalContext=context)
    found = sorted({f["category"] for f in rec.fields.values()} - {"plain"})
    extra = f" It contained: {', '.join(LABELS.get(c, c) for c in found)}." if found else ""
    return _decision("deny", f"Hidee: {rec.reason}{extra}")


CHOICE_OF = {"allowed": "approve", "minimized": "minimal", "rejected": "reject", "drafted": "draft"}
DECIDERS = {"rules": "rules", "timeout": "timeout", None: "rules", "default": "default"}
def _evidence(state, session: str, event: str, data: dict) -> None:
    log = getattr(state, "evidence", None)
    if log is not None:
        log.append(session, event, data)


def removal_choice(mapped, rec) -> dict | None:
    """For a shorter version: which detected details were removed and which kept (field keys and categories, never
    values). Destination fields (toolmap.RECIPIENT_KEYS, RECIPIENT_CONTEXT) are kept; a detail is kept if its value
    is still in the text that ran."""
    if rec.status != "minimized" or not getattr(mapped, "details", None) or not rec.minimal:
        return None
    removed, kept = [], []
    requested = set(getattr(mapped, "kept_requested", None) or [])
    for key, d in mapped.details.items():
        leaf = (rec.minimal.get(d["leaf"]) or {}).get("value", "")
        entry = {"field": key, "category": d["category"], "recipient": d["recipient"], "requested": key in requested}
        (kept if mapped.fields[key]["value"] in leaf else removed).append(entry)
    return {"removed": removed, "kept": kept}


def held_back(state, rec, mapped) -> list[tuple[str, str]]:
    """What withheld memory remembers after a decision: only what was actually withheld.

    - shorter version: the removed details, never the kept ones (destinations, details the request names);
    - rejection naming categories (the decision's `withhold` list, or category names in the person's reason): the
      highlighted details of those categories, never destinations or details the user's request names;
    - rejection naming no detail (an empty `withhold`, such as a destination the user did not ask for): the
      destination, not the content;
    - rejection without names: the details the shorter version would have removed;
    - a block by the rules: every highlighted detail (as before)."""
    fields, details = rec.fields, getattr(mapped, "details", None) or {}
    requested = set(getattr(mapped, "kept_requested", None) or [])
    content = lambda k, f: (f["category"] not in ("plain", "outside_folder") and not details.get(k, {}).get("recipient")  # noqa: E731
                            and k not in requested)
    if rec.status == "minimized":
        choice_detail = removal_choice(mapped, rec)
        keep = {e["field"] for e in choice_detail["kept"]} if choice_detail else set()
        return [(f["value"], f["category"]) for k, f in fields.items()
                if f["category"] not in ("plain", "outside_folder") and k not in keep]
    by = DECIDERS.get(rec.decided_by, "person")
    if by != "person":
        return [(f["value"], f["category"]) for f in fields.values() if f["category"] not in ("plain", "outside_folder")]
    engine = state.engine
    named = getattr(engine, "withhold_named", {}).get(rec.id)
    if named is None:
        note = getattr(engine, "notes", {}).get(rec.id, "").lower()
        hits = {c for c, label in LABELS.items() if c != "plain" and (label.lower() in note or c.replace("_", " ") in note)}
        named = sorted(hits) if hits else None
    if named is not None and not named:
        dests = [f["value"] for k, f in fields.items() if details.get(k, {}).get("recipient")]
        if not dests and mapped.recipient and mapped.recipient not in ("this-computer", "web-search"):
            dests = [mapped.recipient]
        return [(v, "destination") for v in dests]
    if named:
        return [(f["value"], f["category"]) for k, f in fields.items() if f["category"] in named and content(k, f)]
    if details:
        remove = [k for k in mapped.default_remove() if k not in requested]
        return [(fields[k]["value"], fields[k]["category"]) for k in remove if k in fields]
    return [(f["value"], f["category"]) for f in fields.values() if f["category"] not in ("plain", "outside_folder")]


def _decided(state, session: str, rec, mapped, found: list[str]) -> None:
    """Evidence: who decided (the rules or a person), what they chose, how long it took, and what actually ran."""
    import getpass
    from .evidence import digest
    by = DECIDERS.get(rec.decided_by, "person")
    person = by == "person"
    choice = "reject" if person and rec.status == "blocked" else CHOICE_OF.get(rec.status, rec.status)
    data = {"action": rec.id, "kind": mapped.kind, "recipient": mapped.recipient, "found": found,
            "by": by, "choice": choice,
            "reason": rec.reason, "seconds": round((rec.decided_at or time.time()) - rec.created_at, 3)}
    if person:
        data["user"] = getpass.getuser()
    note = getattr(state.engine, "notes", {}).get(rec.id)
    if note:
        data["note"] = note           # the reason the person wrote with the rejection, as written
    if rec.status in ("rejected", "blocked") and getattr(state, "withheld", None) is not None and mapped.kind != "access":
        data["withheld"] = sorted({c for _, c in held_back(state, rec, mapped)})   # categories only, never values
    choice_detail = removal_choice(mapped, rec)
    _evidence(state, session, "decision", data)
    if rec.status == "minimized":
        executed = {"action": rec.id, "input": mapped.rebuild(rec.minimal),
                    "note": "the shorter version replaced the model's request"}
        if choice_detail is not None:
            executed["removed"] = sorted({e["category"] for e in choice_detail["removed"]})
            executed["kept_destination"] = sorted({e["category"] for e in choice_detail["kept"]
                                                   if e["recipient"] and not e["requested"]})
            executed["kept_requested"] = sorted({e["category"] for e in choice_detail["kept"] if e["requested"]})
            if executed["kept_destination"]:
                executed["note"] += "; destination fields (where the call is sent) were kept"
            if executed["kept_requested"]:
                executed["note"] += "; details the user's request names were kept"
        _evidence(state, session, "executed", executed)
    if rec.status == "drafted":
        # The message the user will send; nothing left this computer.
        _evidence(state, session, "drafted", {"action": rec.id, "input": mapped.tool_input,
                                                         "note": "saved as a draft for the user; nothing was sent"})


def request_for(state, session: str) -> str:
    """The user's own request in this session: the prompts the person typed (UserPromptSubmit). Tool results,
    fetched pages, and the model's text are never part of it, so injected text cannot name a detail."""
    return "\n".join(getattr(state, "user_requests", {}).get(session, []))


def known_for(state, session: str, cwd: str):
    """The session's known data: paths given when it started, else the project's .hidee/sensitive, else None."""
    cache = getattr(state, "known", None)
    if cache is None:
        return None
    if session not in cache:
        from .known import KnownData, designated
        paths = designated(Path(cwd)) if cwd else []
        cache[session] = KnownData.from_paths(paths) if paths else None
    return cache[session]


def check_call(state, session: str, tool: str, tool_input: dict, cwd_hint: str = ""):
    """Run one tool call through Hidee and wait for the outcome: (reply for Claude Code, record or None).

    Both the real hook and the scripted sessions of the online demo use this, so they share one engine."""
    activity = state.activity
    current = state.sessions.sessions.get(session)
    cwd = current.cwd if current else cwd_hint
    mapped = map_call(tool, tool_input, cwd, known_for(state, session, cwd), request_for(state, session),
                      freetext=getattr(state, "freetext", True))
    from .evidence import digest
    _evidence(state, session, "tool_call", {"tool": tool, "input": digest(tool_input),
                                            "where": "outside" if mapped is not None else "local"})
    withheld = getattr(state, "withheld", None)
    if mapped is not None and withheld is not None:
        hit = withheld.match(session, json.dumps(tool_input, ensure_ascii=False))
        if hit:
            label = LABELS.get(hit[1], hit[1])
            activity.add(session, tool, mapped.kind, mapped.recipient, "blocked", summary(tool, tool_input))
            _evidence(state, session, "decision", {"kind": mapped.kind, "recipient": mapped.recipient,
                                                   "found": [hit[1]], "by": "hidee", "choice": "reject",
                                                   "reason": "a detail the user held back earlier, written another way"})
            return _decision("deny", f"Hidee: this contains the {label} the user held back earlier in this "
                                     "session, written another way. It was not sent. Do not try to send it again; "
                                     "ask the user instead."), None
    if mapped is None:
        mapped = map_access(tool, tool_input, cwd)
    if mapped is None:
        activity.add(session, tool, "local", LOCAL, "local", summary(tool, tool_input))
        return {}, None
    engine = state.engine
    try:
        from .toolmap import is_person_message
        policy = getattr(state, "message_policy", "ask")
        message = policy != "off" and is_person_message(tool, mapped)
        rec = engine.submit(f"session:{session}", mapped.kind, mapped.recipient, mapped.fields, mapped.minimal,
                            hold=message, hold_reason=HOLD_MESSAGE)
        if message:
            getattr(state, "message_actions", set()).add(rec.id)
            if policy == "draft" and rec.status == "waiting":       # the user's default: always draft
                rec = engine.decide(rec.id, "draft", decided_by="default")
    except ActionError as e:
        activity.add(session, tool, mapped.kind, mapped.recipient, "blocked", summary(tool, tool_input))
        return _decision("deny", f"Hidee could not check this call: {e}"), None
    found = sorted({f["category"] for f in mapped.fields.values()} - {"plain"})
    kept = getattr(state, "kept_requested", None)
    if kept is not None and getattr(mapped, "kept_requested", None):
        kept[rec.id] = set(mapped.kept_requested)
    note = lambda r: activity.add(session, tool, mapped.kind, mapped.recipient, r.status,  # noqa: E731
                                  summary(tool, tool_input), r.id, found)
    if rec.status == "waiting":
        note(rec)
    deadline = time.monotonic() + engine.review_timeout_s + 5
    while rec.status == "waiting" and time.monotonic() < deadline:
        time.sleep(0.25)
        engine.expire_waiting()
        rec = engine.get(rec.id) or rec          # never lose the record we already have
    note(rec)
    _decided(state, session, rec, mapped, found)
    if withheld is not None and rec.status in ("minimized", "rejected", "blocked") and mapped.kind != "access":
        withheld.add(session, held_back(state, rec, mapped))
    return reply(rec, mapped), rec


@router.post("/hook/pretooluse")
async def pretooluse(request: Request, x_hidee_token: str = Header(""), x_hidee_session: str = Header(""),
                     x_hidee_agent: str = Header("")):
    state = request.app.state
    if not secrets.compare_digest(x_hidee_token, state.hook_token):
        raise HTTPException(401, "Unknown hook token")
    body = await request.json()
    tool, tool_input = str(body.get("tool_name", "")), body.get("tool_input") or {}
    gemini = x_hidee_agent == "gemini"
    if x_hidee_agent == "codex" and tool == "Bash" and isinstance(tool_input.get("command"), list):
        import shlex
        argv = [str(a) for a in tool_input["command"]]
        # Codex may send argv such as ["bash", "-lc", "<script>"]; check the script itself.
        script = argv[2] if len(argv) == 3 and argv[1] in ("-c", "-lc") else shlex.join(argv)
        tool_input = {**tool_input, "command": script}
    if gemini:
        from .dialects import gemini_call, gemini_mcp_name
        tool = gemini_mcp_name(tool, body.get("mcp_context"))
        tool, tool_input = gemini_call(tool, tool_input)
    session = x_hidee_session or "unknown"
    if state.learn.enabled(session):
        state.learn.call(session, tool, tool_input, str(body.get("cwd") or ""))
    answer, _ = await run_in_threadpool(check_call, state, x_hidee_session or "unknown", tool, tool_input,
                                        str(body.get("cwd") or ""))
    if gemini:
        from .dialects import gemini_reply
        note = (answer.get("hookSpecificOutput") or {}).get("additionalContext")
        if note:
            state.notes[session] = note
        return gemini_reply(tool, tool_input, answer)
    out = answer.get("hookSpecificOutput") or {}
    if x_hidee_agent == "codex" and out.get("permissionDecision") == "allow" and "updatedInput" not in out:
        # Codex accepts "allow" only together with updatedInput; a bare allow fails the hook. After an approval
        # Hidee has no objection, so it answers as it does when the rules allow a call.
        return {}
    return answer


@router.post("/hook/aftertool")
async def aftertool(request: Request, x_hidee_token: str = Header(""), x_hidee_session: str = Header("")):
    """Gemini CLI AfterTool: attach the note Hidee owes the model about a shorter version, once."""
    state = request.app.state
    if not secrets.compare_digest(x_hidee_token, state.hook_token):
        raise HTTPException(401, "Unknown hook token")
    note = state.notes.pop(x_hidee_session or "unknown", None)
    return {"hookSpecificOutput": {"additionalContext": note}} if note else {}


@router.post("/hook/learn")
async def learn_event(request: Request, x_hidee_token: str = Header(""), x_hidee_session: str = Header(""),
                      x_hidee_agent: str = Header("")):
    """Learn mode: a prompt, a tool result, or an answer from a learn session. Never a decision."""
    state = request.app.state
    if not secrets.compare_digest(x_hidee_token, state.hook_token):
        raise HTTPException(401, "Unknown hook token")
    body = await request.json()
    if isinstance(body, dict):
        if x_hidee_agent == "gemini":
            from .dialects import gemini_event
            body = gemini_event(body)
        session = x_hidee_session or "unknown"
        requests = getattr(state, "requests", None)
        if requests is not None and x_hidee_agent not in ("gemini", "codex"):
            # Claude Code: the model requests behind this turn, from its transcript, before the answer.
            requests.note(session, body.get("transcript_path"))
            if body.get("hook_event_name") == "Stop":
                await run_in_threadpool(requests.at_stop, session)
        _record_turn(state, session, body)
        if state.learn.enabled(session):
            state.learn.record(session, body)
    return {}


def _record_turn(state, session: str, body: dict) -> None:
    """Evidence for every session: the prompt, what each tool returned, failures, and the answer."""
    from .evidence import digest
    event = body.get("hook_event_name")
    tool = str(body.get("tool_name", ""))
    if event == "UserPromptSubmit":
        _evidence(state, session, "prompt", digest(body.get("prompt", "")))
        requests = getattr(state, "user_requests", None)
        if requests is not None:
            requests.setdefault(session, []).append(str(body.get("prompt", "")))
    elif event == "PostToolUse":
        _evidence(state, session, "tool_result", {"tool": tool, **digest(body.get("tool_response", ""))})
    elif event == "PostToolUseFailure":
        _evidence(state, session, "tool_failure", {"tool": tool, **digest(body.get("error", ""))})
    elif event == "Stop":
        _evidence(state, session, "answer", digest(body.get("last_assistant_message", "")))


@router.get("/behind/{session}", response_class=HTMLResponse)
def behind_page(request: Request, session: str):
    """Learn mode: what happens behind the agent, step by step, beside the terminal."""
    from .web import templates
    state = request.app.state
    from .learn import GLOSSARY
    current = state.sessions.sessions.get(session)
    return templates.TemplateResponse(request, "behind.html", {
        "session": session, "name": current.name if current else session, "glossary": GLOSSARY,
        "agent": {"claude": "Claude", "gemini": "Gemini", "codex": "Codex",
                  "practice": "the practice agent"}.get(current.kind if current else "", "the agent")})


@router.get("/learn/{session}")
def learn_timeline(request: Request, session: str, since: int = 0):
    """The learn-mode timeline of one session, after `since`."""
    learn = request.app.state.learn
    return {"events": learn.since(session, since), "usage": learn.usage(session)}


@router.get("/learn/{session}/export.{fmt}")
def learn_export(request: Request, session: str, fmt: str):
    """The whole timeline as a file to keep or hand in: Markdown or JSON, with the same masked excerpts."""
    from fastapi.responses import Response
    from .learn import export_json, export_markdown
    from .workspace import KINDS
    if fmt not in ("md", "json"):
        raise HTTPException(404, "Not found")
    state = request.app.state
    current = state.sessions.sessions.get(session)
    name = current.name if current else session
    agent = KINDS.get(current.kind, current.kind) if current else "unknown"
    make, media = (export_markdown, "text/markdown") if fmt == "md" else (export_json, "application/json")
    body = make(state.learn.since(session, 0), state.learn.usage(session), name=name, agent=agent, session=session)
    slug = "-".join("".join(c if c.isascii() and c.isalnum() else " " for c in name.lower()).split()) or "session"
    filename = f"hidee-behind-the-agent-{slug}-{session}.{fmt}"
    return Response(body, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/live")
def live(request: Request, since: int = 0):
    """New flow events after `since`, the names of the sessions, and the steps waiting for a decision now (the
    waiting notice follows the store, so it always agrees with the checkpoint, even if an event was missed)."""
    state = request.app.state
    sessions = {sid: {"name": s.name, "kind": s.kind} for sid, s in state.sessions.sessions.items()}
    waiting = [a.id for a in state.store.list_actions(status="waiting")]
    return {"events": state.activity.since(since), "sessions": sessions, "waiting": waiting}
