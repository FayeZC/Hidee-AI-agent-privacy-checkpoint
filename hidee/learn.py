"""Learn mode: a per-session timeline of what happens behind the agent, for students.

Each event is one step of the agent loop: the student's prompt, a tool call the model chose, what the tool returned
(or that it failed), and the model's answer. Every event says where its data went:

- "model": part of the conversation, which goes to the model provider with each request
- "local": the step ran on this computer
- "outside": the step sent data to a service other than the model provider

Learn mode only watches. It never changes what the checkpoint decides.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque

from .detect import find, redact
from .live import summary
from .toolmap import map_call

from pathlib import Path

TRANSCRIPTS = Path.home() / ".claude" / "projects"   # the only place a transcript path may point
EXCERPT = 600                                   # characters of each text kept for display
MASKED = {"api_key", "card_number", "government_id"}   # never shown, even on the student's own screen


TOOL_WORDS = {
    "Read": "The agent asked to read a file on this computer.",
    "Write": "The agent asked to write a file on this computer.",
    "Edit": "The agent asked to change part of a file on this computer.",
    "Glob": "The agent asked for a list of files on this computer.",
    "Grep": "The agent asked to search inside files on this computer.",
    "Bash": "The agent asked to run a command on this computer.",
    "WebSearch": "The agent asked to search the web. The search words leave this computer.",
    "WebFetch": "The agent asked to open a web page. The address leaves this computer.",
}


def explain(kind: str, tool: str, where: str) -> str:
    """One plain sentence about a step, for students."""
    if kind == "prompt":
        return "Your message becomes part of the conversation, which goes to the model provider."
    if kind == "answer":
        return "The answer comes from the model provider and joins the conversation, so it is sent back with every later request."
    if kind == "failure":
        return "The tool failed. The error message goes back to the model, which decides what to try next."
    if kind == "result":
        return ("The tool's output is added to the conversation. With the next request, the whole conversation, "
                "including this output, goes to the model provider.")
    base = TOOL_WORDS.get(tool)
    if base is None:
        base = (f"The agent asked to use a tool from another program ({tool})." if tool.startswith("mcp__")
                else f"The agent asked to use the tool {tool}.")
    if where == "outside" and tool not in ("WebSearch", "WebFetch"):     # those two already say so
        base += " This step sends data off this computer."
    return base


def _text(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
        return str(value)


def _clean(text: str) -> tuple[str, list[str]]:
    """Mask secrets, cut to an excerpt, and list the kinds of sensitive information found."""
    findings = find(text)
    shown = redact(text, [f for f in findings if f.category in MASKED])
    if len(shown) > EXCERPT:
        shown = shown[:EXCERPT] + "…"
    return shown, sorted({f.category for f in findings})


def _mask(text: str) -> str:
    return redact(text, [f for f in find(text) if f.category in MASKED])


def brief(tool: str, tool_input) -> str:
    """The one-line summary of a tool call, with secrets masked before the line is cut short."""
    if isinstance(tool_input, dict):
        tool_input = {k: _mask(v) if isinstance(v, str) else v for k, v in tool_input.items()}
    return summary(tool, tool_input)


def safe_transcript(value) -> Path | None:
    """The transcript path a hook reported, if it is a Claude Code transcript (.jsonl under ~/.claude/projects)."""
    if not value:
        return None
    try:
        path = Path(str(value)).resolve()
        if path.suffix == ".jsonl" and path.is_relative_to(TRANSCRIPTS.resolve()):
            return path
    except (OSError, ValueError):
        pass
    return None


TOKEN_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


def model_requests(path: Path) -> list[dict] | None:
    """Each model request in a Claude Code transcript, once per message id, in the order they appear.

    Claude Code writes one line per content block, and every line of a message repeats its id and usage; the last
    line's usage is kept, and the first line's timestamp. `request_id` is the message id (msg_...), which the
    provider also holds; `provider_request_id` is the API request id (req_...) when the transcript has it.
    None if the file cannot be read."""
    seen: dict[str, dict] = {}
    try:
        with Path(path).open(encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                usage, mid = message.get("usage"), message.get("id")
                if not isinstance(usage, dict) or not mid:
                    continue
                row = seen.get(mid) or {"request_id": str(mid)}
                if entry.get("requestId"):
                    row["provider_request_id"] = str(entry["requestId"])
                row["model"] = str(message.get("model", ""))
                for k in TOKEN_FIELDS:
                    value = usage.get(k, 0)
                    row[k] = value if isinstance(value, int) else 0
                if "at" not in row and entry.get("timestamp"):
                    row["at"] = str(entry["timestamp"])
                seen[mid] = row
    except OSError:
        return None
    return [{k: r[k] for k in ("request_id", "provider_request_id", "model", *TOKEN_FIELDS, "at") if k in r}
            for r in seen.values()]


class Timeline:
    def __init__(self, size: int = 500, clock=time.time) -> None:
        self.size, self.clock = size, clock
        self.lock = threading.Lock()
        self.events: dict[str, deque] = {}
        self.seq: dict[str, int] = {}
        self.learning: set[str] = set()
        self.transcripts: dict[str, Path] = {}

    # Which sessions are in learn mode
    def enable(self, session: str) -> None:
        self.learning.add(session)

    def enabled(self, session: str) -> bool:
        return session in self.learning

    # Recording
    def _add(self, session: str, kind: str, where: str, text, tool: str = "", detail: str = "") -> None:
        raw = _text(text)
        shown, found = _clean(raw)
        detail = _mask(detail)
        with self.lock:
            seq = self.seq.get(session, 0) + 1
            self.seq[session] = seq
            self.events.setdefault(session, deque(maxlen=self.size)).append(
                {"seq": seq, "at": self.clock(), "kind": kind, "where": where, "tool": tool, "detail": detail,
                 "text": shown, "size": len(raw), "found": found, "why": explain(kind, tool, where)})

    def call(self, session: str, tool: str, tool_input: dict, cwd: str) -> None:
        """A tool call the model chose. It goes outside if Hidee would check it as outbound."""
        outbound = map_call(tool, tool_input or {}, cwd) is not None
        self._add(session, "call", "outside" if outbound else "local", tool_input or {}, tool,
                  brief(tool, tool_input or {}))

    def usage(self, session: str) -> dict | None:
        """Token usage of the session's model requests, read from Claude Code's transcript: how many requests,
        what the first and the latest request sent to the model, and what the model wrote back."""
        path = self.transcripts.get(session)
        messages = model_requests(path) if path is not None else None
        if not messages:
            return None
        sent = lambda u: (u["input_tokens"] + u["cache_read_input_tokens"]  # noqa: E731
                          + u["cache_creation_input_tokens"])
        return {"requests": len(messages), "first_input": sent(messages[0]), "last_input": sent(messages[-1]),
                "output": sum(m["output_tokens"] for m in messages), "model": messages[-1]["model"]}

    def _note_transcript(self, session: str, value) -> None:
        path = safe_transcript(value)
        if path is not None:
            self.transcripts[session] = path

    def record(self, session: str, body: dict) -> None:
        """One hook event from Claude Code. Unknown events are ignored."""
        self._note_transcript(session, body.get("transcript_path"))
        event = body.get("hook_event_name")
        tool, tool_input = str(body.get("tool_name", "")), body.get("tool_input") or {}
        if event == "UserPromptSubmit":
            self._add(session, "prompt", "model", body.get("prompt", ""))
        elif event == "PostToolUse":
            # What a tool returns is added to the conversation, so the model provider receives it next.
            self._add(session, "result", "model", body.get("tool_response", ""), tool, brief(tool, tool_input))
        elif event == "PostToolUseFailure":
            self._add(session, "failure", "model", body.get("error", ""), tool, brief(tool, tool_input))
        elif event == "Stop":
            self._add(session, "answer", "model", body.get("last_assistant_message", ""))

    def since(self, session: str, seq: int) -> list[dict]:
        with self.lock:
            return [e for e in self.events.get(session, ()) if e["seq"] > seq]


# ---- Glossary and export ----

GLOSSARY = {
    "Tool call": "A request from the model to do something: read a file, run a command, or search the web. "
                 "The agent program on your computer carries it out.",
    "Conversation (context)": "Everything said so far: your messages, the agent's instructions, each tool's "
                              "output, and the model's answers. The whole conversation is sent with each request.",
    "Token": "The unit a model reads and writes: a short piece of text, about four characters of English. "
             "Providers count and bill by tokens.",
    "Model provider": "The company that runs the model, such as Anthropic, Google, or OpenAI. The model runs on "
                      "their computers, not on yours.",
    "Hook": "A small program the agent runs at set moments, for example before each tool call. Hidee uses hooks "
            "to see each step.",
    "Checkpoint": "The place in Hidee where a step that would send sensitive data waits. You decide: send it, "
                  "send a shorter version, or stop it.",
    "Shorter version": "The same step with the sensitive parts removed, such as an email address. Hidee sends "
                       "it instead of the original when you choose it.",
    "Sandbox": "A fence around the agent's commands. They can change only the project folder, cannot read your "
               "keys, and must connect through Hidee.",
}

TITLES = {"prompt": "You asked", "call": "The model chose a tool", "result": "The tool returned",
          "failure": "The tool failed", "answer": "The model answered"}
WHERE_WORDS = {"local": "Stays on this computer", "model": "Goes to the model provider",
               "outside": "Leaves this computer"}
NOTE = ("Hidee masked API keys, card numbers, and government IDs, and kept only the first part of long texts. "
        "Hidee did not send this file anywhere.")


def _label(category: str) -> str:
    from .detect import LABELS
    return LABELS.get(category, category)


def _summary(events: list[dict]) -> dict:
    where = {"local": 0, "model": 0, "outside": 0}
    found: list[str] = []
    for e in events:
        if e["where"] in where:
            where[e["where"]] += 1
        found += [f for f in e.get("found", []) if f not in found]
    return {"steps": len(events), "turns": sum(e["kind"] == "prompt" for e in events), "where": where,
            "found": found}


def export_json(events: list[dict], usage: dict | None, name: str, agent: str, session: str) -> str:
    """The timeline as JSON. The texts are the same masked excerpts the page shows."""
    return json.dumps({"session": session, "name": name, "agent": agent,
                       "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "note": NOTE,
                       "summary": _summary(events), "usage": usage, "events": events, "glossary": GLOSSARY},
                      ensure_ascii=False, indent=2)


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks in the text, so the text cannot close it."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def export_markdown(events: list[dict], usage: dict | None, name: str, agent: str, session: str) -> str:
    """The timeline as Markdown that reads like a homework appendix: a summary, then each turn step by step."""
    n = lambda x: f"{x:,}"  # noqa: E731
    s = _summary(events)
    out = [f"# Behind the agent: {name}", "",
           f"Agent: {agent} · Session {session} · Exported {time.strftime('%Y-%m-%d %H:%M')}", "",
           "Every step the agent took, and where its data went. " + NOTE, "",
           "## Summary", "", "| | |", "| :- | -: |",
           f"| Steps | {s['steps']} |", f"| Turns | {s['turns']} |",
           f"| Stayed on this computer | {s['where']['local']} |",
           f"| Went to the model provider | {s['where']['model']} |",
           f"| Left this computer for another service | {s['where']['outside']} |"]
    if usage:
        out += [f"| Model requests | {n(usage['requests'])} |",
                f"| First request sent | {n(usage['first_input'])} tokens |",
                f"| Latest request sent | {n(usage['last_input'])} tokens |",
                f"| The model wrote | {n(usage['output'])} tokens |"]
    if s["found"]:
        out.append(f"| Sensitive information found | {', '.join(_label(f) for f in s['found'])} |")
    out.append("")
    if usage:
        out += ["Token counts come from the agent's own transcript. The whole conversation goes to the model "
                "provider with each request, so later requests are larger.", ""]
    if not events:
        out += ["No steps were recorded in this session.", ""]
    turn = 0
    for step, e in enumerate(events, 1):
        if e["kind"] == "prompt" or turn == 0:
            turn += 1
            out += [f"## Turn {turn}", ""]
        title = TITLES.get(e["kind"], e["kind"])
        if e.get("tool") and e["kind"] in ("call", "result", "failure"):
            title += f": {e['tool']}"
        where = "Comes from the model provider" if e["kind"] == "answer" else WHERE_WORDS.get(e["where"], e["where"])
        out += [f"### {step}. {title}", "",
                f"*{where}* · {time.strftime('%H:%M:%S', time.localtime(e['at']))}", "", e["why"], ""]
        if e.get("detail") and e["kind"] == "call":
            out += [f"Step: `{e['detail'].replace('`', chr(39))}`", ""]
        if e.get("text") and e["kind"] != "call":
            fence = _fence(e["text"])
            out += [fence + "text", e["text"].rstrip("\n"), fence, ""]
            if e.get("size", 0) > len(e["text"]):
                out += [f"{n(e['size'])} characters in all; the first part is shown.", ""]
        if e.get("found"):
            out += ["Contains: " + ", ".join(_label(f) for f in e["found"]), ""]
    out += ["## Words used here", ""]
    out += [f"- **{term}**: {text}" for term, text in GLOSSARY.items()]
    return "\n".join(out) + "\n"
