"""A live record of every tool call the sessions make, local or outbound, for the flow map. Kept in memory only."""
from __future__ import annotations

import os
import threading
import time
from collections import deque

from .detect import risks_of

LOCAL = "this-computer"


def summary(tool: str, tool_input: dict) -> str:
    """A short, readable description of a tool call. File paths show only the file name."""
    if not isinstance(tool_input, dict):
        return tool
    if tool in ("Grep", "Glob") and isinstance(tool_input.get("pattern"), str):
        return f"{tool} {tool_input['pattern'][:60]}"
    for key in ("file_path", "notebook_path", "path"):
        if isinstance(tool_input.get(key), str):
            return f"{tool} {os.path.basename(tool_input[key].rstrip('/')) or tool_input[key]}"
    for key in ("pattern", "description", "command", "query", "url", "prompt"):
        if isinstance(tool_input.get(key), str) and tool_input[key].strip():
            text = " ".join(tool_input[key].split())
            return f"{tool} {text[:60]}{'…' if len(text) > 60 else ''}"
    return tool


class Activity:
    def __init__(self, size: int = 300, clock=time.time) -> None:
        self.events: deque[dict] = deque(maxlen=size)
        self.seq, self.clock, self.lock = 0, clock, threading.Lock()

    def add(self, session: str, tool: str, kind: str, recipient: str, status: str, text: str,
            action_id: str | None = None, categories: list[str] | None = None) -> dict:
        with self.lock:
            self.seq += 1
            event = {"seq": self.seq, "at": self.clock(), "session": session, "tool": tool, "kind": kind,
                     "recipient": recipient, "status": status, "text": text, "action": action_id,
                     "categories": categories or [],
                     "risks": risks_of(categories or [])}
            self.events.append(event)
            return event

    def since(self, seq: int) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e["seq"] > seq]
