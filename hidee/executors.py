"""Executors carry out approved actions. They only ever receive approved, unchanged content."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ExecutionRequest:
    action_id: str
    kind: str
    recipient: str
    values: dict[str, str]


Executor = Callable[[ExecutionRequest], None]


class SimulatedExecutor:
    """Records what would have been sent. Performs no external effect."""

    def __init__(self) -> None:
        self.sent: list[ExecutionRequest] = []
        self._lock = threading.Lock()

    def __call__(self, request: ExecutionRequest) -> None:
        with self._lock:
            self.sent.append(request)


class PassThrough:
    """For tool calls: approval itself is the effect, because the agent runs the tool."""

    def __call__(self, request: ExecutionRequest) -> None:
        return None


class LocalMemoryExecutor:
    """Appends approved memory entries to a local JSON Lines file."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock

    def __call__(self, request: ExecutionRequest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": self.clock(), "action_id": request.action_id, "recipient": request.recipient,
                 "values": request.values}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class Executors:
    """Route each approved action to the executor registered for its recipient."""

    def __init__(self, default: Executor | None = None, by_recipient: dict[str, Executor] | None = None) -> None:
        self.default = default or SimulatedExecutor()
        self.by_recipient = dict(by_recipient or {})

    def run(self, request: ExecutionRequest) -> None:
        (self.by_recipient.get(request.recipient) or self.default)(request)
