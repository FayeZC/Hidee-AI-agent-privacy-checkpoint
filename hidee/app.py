"""Application factory: one local service with the agent API and the owner dashboard."""
from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .executors import Executors, LocalMemoryExecutor, PassThrough, SimulatedExecutor
from .rules import RuleSet, load_rules
from .store import Store


class RulesFile:
    """Reload rules.yaml whenever it changes on disk."""

    def __init__(self, path: Path) -> None:
        self.path, self._mtime, self._rules = Path(path), None, RuleSet()

    def __call__(self) -> RuleSet:
        mtime = self.path.stat().st_mtime_ns if self.path.exists() else None
        if mtime != self._mtime:
            self._rules = load_rules(self.path) if mtime is not None else RuleSet()
            self._mtime = mtime
        return self._rules


def create_app(home: Path | str, clock=time.time, executors: Executors | None = None,
               review_timeout_s: float = 600, demo: bool = False, app_mode: bool = False,
               app_url: str = "http://127.0.0.1:8770", allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
               claude: str = "claude", network_gate: bool = True, online: bool = False, gemini: str = "gemini", codex: str = "codex",
               online_pace: float = 1.6, message_policy: str = "ask", freetext: bool = True) -> FastAPI:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    store = Store(home / "hidee.db")
    if app_mode and not online:
        from .privacy import make_private
        make_private(home)                 # Hidee's records are readable only by the user
    rules = RulesFile(home / "rules.yaml")
    if app_mode and executors is None:
        executors = Executors(PassThrough())   # approval is the effect: Claude Code runs the tool itself
    executors = executors or Executors(SimulatedExecutor(),
                                       {"agent-memory": LocalMemoryExecutor(home / "memory.jsonl")})
    engine = Engine(store, rules, executors, clock=clock, review_timeout_s=review_timeout_s)

    # The agent API's interactive docs are for local use; the public online demo does not publish them.
    app = FastAPI(title="Hidee", docs_url=None if online else "/api/docs", redoc_url=None,
                  openapi_url=None if online else "/openapi.json")
    app.state.store, app.state.rules, app.state.engine, app.state.clock = store, rules, engine, clock
    app.state.executors = executors
    app.state.demo = demo
    app.state.home = home
    app.state.demo_name = None
    app.state.csrf = secrets.token_urlsafe(32)
    app.state.app_mode = app_mode
    app.state.online = online
    app.state.hook_token = secrets.token_urlsafe(32)
    app.state.session_names = {}

    @app.middleware("http")
    async def expire_old_requests(request: Request, call_next):
        engine.expire_waiting()
        return await call_next(request)

    from . import api, web
    app.include_router(api.router)
    app.include_router(web.router)
    if app_mode:
        from fastapi.middleware.trustedhost import TrustedHostMiddleware

        from . import hook_api, workspace
        from .live import Activity
        from .sessions import SessionManager
        app.state.activity = Activity()
        from .learn import Timeline
        app.state.learn = Timeline()
        from .withheld import Withheld
        app.state.withheld = Withheld()
        from .evidence import EvidenceLog, RequestLedger
        app.state.evidence = EvidenceLog(home / "evidence")
        app.state.requests = RequestLedger(app.state.evidence)
        app.state.notes = {}                # session -> note for the model after a shorter version (Gemini)
        # Messages to people: "ask" (hold and ask how it goes out), "draft" (always save as a draft for the user),
        # or "off" (the rules decide, as for any call).
        app.state.message_policy = message_policy
        app.state.freetext = freetext          # free-text facts (hidee/freetext.py); off: structured details only
        app.state.message_actions = set()   # action ids that are messages to people
        app.state.known = {}                # session -> known.KnownData (designated sensitive files) or None
        app.state.user_requests = {}        # session -> the prompts the person typed (never tool results)
        app.state.kept_requested = {}       # action id -> detected details the user's request names (kept)
        if online:                     # the online demo: scripted sessions, no shell, no network gate
            from .online import ScriptedSessionManager
            app.state.sessions = ScriptedSessionManager(home, pace=online_pace)
            app.state.sessions.state = app.state
        else:
            gate = _network_gate(app) if network_gate else None
            app.state.sessions = SessionManager(home, app_url, app.state.hook_token, claude=claude, gate=gate,
                                                gemini=gemini, codex=codex)
            from .online import PRACTICE_BANNER, ScriptedSessionManager
            practice = ScriptedSessionManager(home, pace=online_pace, banner=PRACTICE_BANNER)
            practice.state = app.state
            app.state.sessions.practice = practice
            from .keys import KeyStore, default_backend
            app.state.keys = KeyStore(default_backend(home))
            app.state.key_checks = {}
            from .update import Updates
            app.state.updates = Updates(home)      # reads the user's answer; never checks on its own
            app.state.session_names.update(app.state.sessions.known_names())
        if hasattr(app.state.sessions, "on_event"):
            app.state.sessions.on_event = _session_event(app.state)
        app.include_router(hook_api.router)
        app.include_router(workspace.router)
        # A page on another site may resolve its own name to 127.0.0.1; refuse any Host that is not ours.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    if web.STATIC_DIR:
        app.mount("/static", StaticFiles(directory=web.STATIC_DIR), name="static")
    return app


def _session_event(state):
    """Session start and end go to the evidence log; at the end, first any model requests still unrecorded."""
    def on_event(session: str, event: str, data: dict) -> None:
        if event == "session_end":
            state.requests.record(session)
        state.evidence.append(session, event, data)
    return on_event


def _network_gate(app: FastAPI):
    """Wire the network gate to the rules and the live map. Model traffic always passes and shows as its own flow."""
    from .detect import find
    from .gate import Gate, is_model_host
    from .rules import evaluate
    state = app.state

    def check(session: str, host: str, url: str) -> tuple[bool, str]:
        if is_model_host(host):
            return True, ""
        cats = ["plain"] + sorted({f.category for f in find(url)})
        outcome = evaluate(state.rules(), f"session:{session}", "network", host, cats)
        return outcome.verdict != "block", f"Hidee refused this connection: {outcome.reason}"

    def record(session: str, host: str, port: int, url: str, allowed: bool) -> None:
        model = is_model_host(host)
        from .evidence import digest
        state.evidence.append(session, "connection", {"host": host, "port": port, "url": digest(url)["excerpt"],
                                                      "allowed": allowed, "model": model})
        recipient = "model-provider" if model else host
        now = state.clock()
        for e in reversed(state.activity.since(0)[-60:]):
            if now - e["at"] > 20:
                break
            # Already on the map: the hook reported this call a moment ago, or the same connection repeats.
            if e["session"] == session and e["recipient"] == recipient:
                return
        cats = sorted({f.category for f in find(url)}) if not model else []
        state.activity.add(session, "connection", "model" if model else "connection", recipient,
                           "allowed" if allowed else "blocked", f"connection {host}:{port}", categories=cats)

    return Gate(state.hook_token, lambda: set(state.sessions.sessions), check, record)
