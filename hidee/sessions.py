"""Terminal sessions: each one is a child process on its own PTY, kept alive while browsers come and go."""
from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .privacy import make_private

SCROLLBACK = 256 * 1024
HOOK_MATCHER = "*"   # every call, so the flow map shows local work too; only outbound calls can wait
HOOK_TIMEOUT_S = 660
# Markers a running Claude Code sets for its own child processes. A session started from inside Claude Code
# (for example while developing Hidee) must not inherit them, or it behaves like a sub-session.
SESSION_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                   "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                   "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID", "CLAUDE_EFFORT")


LEARN_EVENTS = ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Stop")


# Safe mode: where credentials usually live, and names that reach services on this computer (a browser-control
# proxy on localhost once let an agent reach the web without passing Hidee's gate).
SECRET_PATHS = ("~/.ssh", "~/.aws", "~/.config/gcloud", "~/.gnupg", "~/.kube", "~/.netrc", "~/.docker/config.json",
                "~/.git-credentials", "~/.npmrc", "~/.pypirc", "~/Library/Keychains")
LOCAL_HOSTS = ("localhost", "*.localhost", "127.0.0.1", "[::1]", "0.0.0.0", "169.254.169.254",
               "metadata.google.internal")


def safe_settings(proxy_port: int | None = None) -> dict:
    """A strict Claude Code sandbox: commands may write only inside the project and temp, cannot read credential
    folders or touch Hidee's own records, and cannot reach local services; outside traffic passes Hidee's gate
    (the session's HTTPS_PROXY), which records and checks it. The permission mode is pinned to the default, so a
    user's auto mode does not apply to the session."""
    return {"permissions": {"defaultMode": "default"},
            "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                        "filesystem": {"denyRead": list(SECRET_PATHS), "denyWrite": ["~/.hidee"]},
                        "network": {"allowedDomains": ["*"], "deniedDomains": list(LOCAL_HOSTS),
                                    # HTTP(S) from sandboxed commands goes to the session's own gate port, so
                                    # Hidee records it; the gate refuses local services there itself.
                                    **({"httpProxyPort": proxy_port} if proxy_port else {})}}}


def hook_settings(learn: bool = False, safe: bool = False, proxy_port: int | None = None) -> dict:
    """Claude Code settings that send each tool call to Hidee first. Learn sessions also report the student's
    prompts, what each tool returned, and the model's answers, through a hook that only watches. Safe sessions
    also run their commands in a strict sandbox."""
    command = f"{shlex.quote(sys.executable)} -m hidee.hook"
    hooks = {"PreToolUse": [{"matcher": HOOK_MATCHER,
                             "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}]}]}
    # Every session reports its turns: the evidence log needs them; the learn view is optional.
    watch = f"{shlex.quote(sys.executable)} -m hidee.learn_hook"
    for event in LEARN_EVENTS:
        hooks[event] = [{"matcher": HOOK_MATCHER, "hooks": [{"type": "command", "command": watch, "timeout": 5}]}]
    return {"hooks": hooks, **(safe_settings(proxy_port) if safe else {})}


def user_shell() -> str:
    """The user's login shell: $SHELL, then the account's shell, then the first of zsh, bash, sh that exists."""
    for shell in (os.environ.get("SHELL"), _account_shell(), "/bin/zsh", "/bin/bash"):
        if shell and os.access(shell, os.X_OK):
            return shell
    return "/bin/sh"


def _account_shell() -> str | None:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_shell or None
    except (ImportError, KeyError):
        return None


@dataclass
class Session:
    id: str
    name: str
    cwd: str
    kind: str
    argv: list[str]
    process: subprocess.Popen
    fd: int
    started_at: float
    scrollback: bytearray = field(default_factory=bytearray)
    listeners: list[Callable[[bytes], None]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    display_cwd: str = ""
    learn: bool = False
    safe: bool = False

    @property
    def alive(self) -> bool:
        return self.process.poll() is None


GEMINI_LEARN = ("BeforeAgent", "AfterTool", "AfterAgent")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _user_hooks(user: dict) -> list[str]:
    """Names (or, failing that, commands) of the hooks in a user's own Gemini settings."""
    out = []
    for groups in (user.get("hooks") or {}).values():
        for group in groups if isinstance(groups, list) else []:
            for hook in (group or {}).get("hooks") or []:
                name = hook.get("name") or hook.get("command")
                if name and name not in out:
                    out.append(name)
    return out


def gemini_settings(learn: bool = False, auth: str | None = None, safe: bool = False,
                    user: dict | None = None) -> dict:
    """Gemini CLI system settings for one session: Hidee's hooks, and the sign-in when Hidee must choose it.
    Gemini reads them through GEMINI_CLI_SYSTEM_SETTINGS_PATH; they take precedence over the user's own."""
    check = f"{shlex.quote(sys.executable)} -m hidee.hook"
    hooks = {"BeforeTool": [{"matcher": ".*", "hooks": [{"type": "command", "name": "hidee-checkpoint",
                                                        "command": check, "timeout": HOOK_TIMEOUT_S * 1000}]}],
             # Gemini's BeforeTool cannot add context, so the note about a shorter version rides on the result.
             "AfterTool": [{"matcher": ".*", "hooks": [{"type": "command", "name": "hidee-note",
                                                       "command": f"{check} --after", "timeout": 5000}]}]}
    # Every session reports its turns, for the evidence log.
    watch = f"{shlex.quote(sys.executable)} -m hidee.learn_hook"
    for event in GEMINI_LEARN:
        entry = {"matcher": ".*" if event == "AfterTool" else "*",
                 "hooks": [{"type": "command", "name": "hidee-learn", "command": watch, "timeout": 5000}]}
        hooks.setdefault(event, []).append(entry)
    settings: dict = {"hooks": hooks}
    if safe:
        # The student's session should not run the user's own hooks, skills, MCP servers, or extensions:
        # in testing, a globally installed skill let Gemini reach the web around Hidee's gate.
        user = user or {}
        settings["skills"] = {"enabled": False}
        settings["hooksConfig"] = {"disabled": _user_hooks(user)}
        settings["mcp"] = {"excluded": sorted((user.get("mcpServers") or {}).keys())}
    if auth:
        settings["security"] = {"auth": {"selectedType": auth}}
    return settings


def _seatbelt_profile(app_port: int, gate_port: int, writable: tuple[str, ...], readable: tuple[str, ...] = (),
                      listable: tuple[str, ...] = ()) -> str:
    """A Seatbelt profile for an agent that runs whole inside it. Writes: the project, temp, and the agent's own
    folders (`writable`, relative to home). Reads: everything except the credential folders, plus single files
    in them that sign-in needs (`readable`). Network: only Hidee (hooks) and the gate, so model calls and every
    other connection pass the gate."""
    home = "(param \"HOME_DIR\")"
    at = lambda p: f'(string-append {home} "{p}")'  # noqa: E731
    own = "\n".join(f"    (subpath {at(p)})" for p in writable)
    secret = "\n".join(f"    (subpath {at(p[1:])})" for p in SECRET_PATHS)
    extra = ""
    if listable:
        extra += "(allow file-read-metadata " + " ".join(f"(literal {at(p)})" for p in listable) + ")\n"
    if readable:
        extra += "(allow file-read* " + " ".join(f"(literal {at(p)})" for p in readable) + ")\n"
    return f"""(version 1)
(allow default)
(deny file-write*)
(allow file-write*
    (subpath (param "TARGET_DIR"))
    (subpath (param "TMP_DIR"))
    (subpath "/private/tmp")
    (subpath "/private/var/folders")
{own}
    (regex #"^/dev/"))
(deny file-write* (subpath {at("/.hidee")}))
(deny file-read*
{secret})
{extra}(deny network-outbound)
(allow network-outbound
    (remote tcp "localhost:{app_port}")
    (remote tcp "localhost:{gate_port}"))
"""


def gemini_profile(app_port: int, gate_port: int) -> str:
    """A Seatbelt profile for a safe Gemini CLI session. Gemini runs whole inside it (its own proxied profiles
    allow only port 8877, which would cut off Hidee's hooks). It may read the one gcloud file Vertex AI sign-in
    needs."""
    return _seatbelt_profile(app_port, gate_port, ("/.gemini", "/.cache", "/.npm"),
                             readable=("/.config/gcloud/application_default_credentials.json",),
                             listable=("/.config", "/.config/gcloud"))


def codex_profile(app_port: int, gate_port: int) -> str:
    """A Seatbelt profile for a safe Codex CLI session. Codex runs whole inside it with its own sandbox off, because
    macOS cannot nest sandbox-exec; Codex's network-less sandbox also made every outbound command fail and ask to
    rerun outside any sandbox. Codex keeps its sign-in and session files in ~/.codex."""
    return _seatbelt_profile(app_port, gate_port, ("/.codex", "/.cache"))


CODEX_EVENTS = ("UserPromptSubmit", "PostToolUse", "Stop")


def _codex_user_setup(codex_home: Path) -> tuple[list[str], list[str], list[str]]:
    """The user's own Codex hooks (as hooks.state keys), inline config.toml hook events, and MCP server names."""
    import tomllib
    keys, inline, servers = [], [], []
    hooks = _read_json(codex_home / "hooks.json").get("hooks")
    if isinstance(hooks, dict):
        snake = lambda e: re.sub(r"(?<!^)(?=[A-Z])", "_", e).lower()  # noqa: E731
        for event, groups in hooks.items():
            for gi, group in enumerate(groups if isinstance(groups, list) else []):
                for hi, _ in enumerate(group.get("hooks", []) if isinstance(group, dict) else []):
                    keys.append(f"{codex_home / 'hooks.json'}:{snake(event)}:{gi}:{hi}")
    try:
        config = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    inline = [e for e, v in (config.get("hooks") or {}).items() if e != "state" and isinstance(v, list)]
    servers = list((config.get("mcp_servers") or {}).keys())
    return keys, inline, servers


def codex_overrides(safe: bool = False, codex_home: Path | None = None) -> list[str]:
    """Codex CLI arguments for one session: Hidee's hooks as inline config (Codex's hook format is Claude Code's),
    run without persisted trust review because Hidee writes them itself. Safe sessions run inside Hidee's Seatbelt
    profile instead of Codex's sandbox; skip the user's own hooks, MCP servers, plugins, and apps; and turn off
    hosted web search, which runs on OpenAI's side where no local hook can see it."""
    check = f"{sys.executable} -m hidee.hook"
    watch = f"{sys.executable} -m hidee.learn_hook"
    # json.dumps gives a valid TOML basic string, escaping quotes and backslashes in odd Python paths.
    hook = lambda command, timeout: f"[{{hooks=[{{type='command',command={json.dumps(command)},timeout={timeout}}}]}}]"  # noqa: E731
    args = ["--dangerously-bypass-hook-trust", "-c", f"hooks.PreToolUse={hook(check, HOOK_TIMEOUT_S)}"]
    for event in CODEX_EVENTS:
        args += ["-c", f"hooks.{event}={hook(watch, 5)}"]
    if safe:
        # Hidee's Seatbelt profile (codex_profile) is the sandbox: Codex's own is off, and every connection,
        # the model's included, can only go through the session's gate.
        args += ["--sandbox", "danger-full-access", "-c", 'web_search="disabled"',
                 "--disable", "plugins", "--disable", "apps",
                 # the user's host skills (~/.agents/skills) can reach the web around the gate
                 "--enable", "skip_host_skill_discovery", "-c", "skills.include_instructions=false",
                 "-c", "suppress_unstable_features_warning=true"]
        keys, inline, servers = _codex_user_setup(codex_home or Path.home() / ".codex")
        if keys:
            state = ", ".join(f"{json.dumps(k)}={{enabled=false}}" for k in keys)
            args += ["-c", f"hooks.state={{{state}}}"]
        for event in inline:
            if event != "PreToolUse" and event not in CODEX_EVENTS:     # those are Hidee's own already
                args += ["-c", f"hooks.{event}=[]"]
        for name in servers:
            if re.fullmatch(r"[A-Za-z0-9_-]+", name):       # a bare TOML key; anything else is skipped, not mangled
                args += ["-c", f"mcp_servers.{name}.enabled=false"]
    return args


class SessionManager:
    def __init__(self, home: Path, app_url: str, hook_token: str, claude: str = "claude", gate=None,
                 gemini: str = "gemini", user_home: Path | None = None, codex: str = "codex") -> None:
        self.home, self.app_url, self.hook_token, self.claude = Path(home), app_url, hook_token, claude
        self.gemini = gemini
        self.codex = codex
        self.user_home = Path(user_home) if user_home else Path.home()
        self.on_event: Callable[[str, str, dict], object] | None = None    # evidence: session start and end
        self.gate = gate
        self.sessions: dict[str, Session] = {}
        # Practice lessons: a scripted agent (the online demo's player) that needs no sign-in. Set by create_app.
        self.practice = None

    def known_names(self) -> dict[str, str]:
        """Names of every session this home has run, so past steps keep readable labels after a restart."""
        names = {}
        for meta in (self.home / "sessions").glob("*/meta.json"):
            try:
                names[f"session:{meta.parent.name}"] = json.loads(meta.read_text(encoding="utf-8"))["name"]
            except (OSError, ValueError, KeyError):
                continue
        return names

    @staticmethod
    def agent_found(program: str) -> bool:
        """True if the program runs here, either on Hidee's own PATH or on the PATH the user's shell sets up."""
        if shutil.which(program):
            return True
        if os.sep in program:
            return False
        shell = user_shell()
        try:
            done = subprocess.run([shell, "-l", "-i", "-c", f"command -v {shlex.quote(program)}"],
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0

    def argv_for(self, kind: str, settings: Path, prefix: str = "", safe: bool = False) -> list[str]:
        if kind == "codex":
            shell = user_shell()
            codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else self.user_home / ".codex"
            args = " ".join(shlex.quote(a) for a in codex_overrides(safe, codex_home))
            run = f"{shlex.quote(self.codex)} {args}"
            if safe:
                profile = shlex.quote(str(settings.parent / "codex.sb"))
                run = (f'sandbox-exec -f {profile} -D TARGET_DIR="$PWD" -D HOME_DIR="$HOME" '
                       f'-D TMP_DIR="${{TMPDIR:-/tmp}}" {run}')
            return [shell, "-l", "-i", "-c", f"{prefix}exec {run}"]
        if kind == "gemini":
            # The settings file is chosen in the shell, after the profile has run: Vertex AI when the profile
            # turns it on, a saved key when the session was started with one, otherwise the user's own sign-in.
            folder = shlex.quote(str(settings.parent))
            pick = ('case "$HIDEE_GEMINI_AUTH" in key) f=key;; *) if [ "$GOOGLE_GENAI_USE_VERTEXAI" = true ]; '
                    'then f=vertex; else f=default; fi;; esac; '
                    f'export GEMINI_CLI_SYSTEM_SETTINGS_PATH={folder}/gemini-$f.json; ')
            shell = user_shell()
            run = shlex.quote(self.gemini) + (" -e none" if safe else "")
            if safe:
                profile = shlex.quote(str(settings.parent / "gemini.sb"))
                run = (f'exec sandbox-exec -f {profile} -D TARGET_DIR="$PWD" -D HOME_DIR="$HOME" '
                       f'-D TMP_DIR="${{TMPDIR:-/tmp}}" {run}')
                return [shell, "-l", "-i", "-c", f"{prefix}{pick}{run}"]
            return [shell, "-l", "-i", "-c", f"{prefix}{pick}exec {run}"]
        if kind == "claude":
            # Start the agent the way the user's own terminal would, so the same sign-in applies: a Claude
            # subscription, an API key, or Vertex AI variables set in the shell profile.
            shell = user_shell()
            # Safe sessions skip the user's own settings (hooks, plugins), skills, and MCP servers.
            alone = " --setting-sources project,local --disable-slash-commands --strict-mcp-config" if safe else ""
            return [shell, "-l", "-i", "-c",
                    f"{prefix}exec {shlex.quote(self.claude)} --settings {shlex.quote(str(settings))}{alone}"]
        return [user_shell(), "-l"]

    def create(self, name: str, cwd: str, kind: str = "claude", argv: list[str] | None = None,
               learn: bool = False, env: dict | None = None, prefix: str = "", safe: bool = False) -> Session:
        if kind == "practice":
            return self._practice(name)
        sid = uuid.uuid4().hex[:8]
        folder = self.home / "sessions" / sid
        folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)   # Hidee's records: only the user
        folder.mkdir(mode=0o700, exist_ok=True)
        settings = folder / "settings.json"
        port = self.gate.session_port(sid) if safe and self.gate is not None else None
        if kind == "gemini" and self.gate is not None:
            # Gemini CLI removes proxy URLs that carry credentials from the environment of the MCP servers it
            # starts, so they would connect around the gate. Gemini sessions use a credential-free port of their own.
            port = self.gate.session_port(sid, refuse_local=safe)
        claude_settings = hook_settings(learn=learn, safe=safe, proxy_port=port)
        if safe:
            # The user's own settings file is skipped in safe sessions; keep only its sign-in variables.
            user_env = _read_json(self.user_home / ".claude" / "settings.json").get("env")
            if isinstance(user_env, dict) and user_env:
                claude_settings["env"] = user_env
        settings.write_text(json.dumps(claude_settings, indent=2), encoding="utf-8")
        program = {"claude": self.claude, "gemini": self.gemini, "codex": self.codex}.get(kind)
        if argv is None and program and not self.agent_found(program):
            raise FileNotFoundError(program)
        if kind == "gemini":
            user = _read_json(self.user_home / ".gemini" / "settings.json") if safe else None
            for variant, auth in (("default", None), ("vertex", "vertex-ai"), ("key", "gemini-api-key")):
                (folder / f"gemini-{variant}.json").write_text(
                    json.dumps(gemini_settings(learn, auth, safe, user), indent=2), encoding="utf-8")
        if safe and getattr(self.gate, "safe", None) is not None:
            self.gate.safe.add(sid)
        if kind in ("gemini", "codex") and safe:
            gate_port = self.gate.start() if self.gate is not None else 0
            if kind == "gemini" and port:
                gate_port = port
            app_port = int(self.app_url.rsplit(":", 1)[-1].split("/")[0])
            profile = gemini_profile if kind == "gemini" else codex_profile
            (folder / f"{kind}.sb").write_text(profile(app_port, gate_port), encoding="utf-8")
        argv = argv or self.argv_for(kind, settings, prefix, safe)
        extra = env or {}
        env = {k: v for k, v in os.environ.items() if k not in SESSION_MARKERS}
        if kind in ("gemini", "codex"):
            extra = {**extra, "HIDEE_AGENT": kind}
        env = {**env, **extra, "TERM": "xterm-256color", "COLORTERM": "truecolor",
               "HIDEE_URL": self.app_url, "HIDEE_TOKEN": self.hook_token, "HIDEE_SESSION": sid}
        if self.gate is not None:
            # Every HTTP(S) connection the session makes, including scripts and MCP servers, goes through the gate.
            proxy = f"http://127.0.0.1:{port}" if kind == "gemini" and port else self.gate.proxy_url(sid)
            env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "http_proxy": proxy, "https_proxy": proxy,
                        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
                        "NODE_USE_ENV_PROXY": "1"})
        master, slave = pty.openpty()
        _set_size(master, 32, 100)
        process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                                   start_new_session=True, close_fds=True)
        os.close(slave)
        session = Session(sid, name or f"Session {len(self.sessions) + 1}", cwd, kind, argv, process, master,
                          time.time(), learn=learn, safe=safe)
        (folder / "meta.json").write_text(json.dumps({"name": session.name, "cwd": cwd, "kind": kind, "learn": learn, "safe": safe,
                                                      "started_at": session.started_at}), encoding="utf-8")
        make_private(folder)
        self.sessions[sid] = session
        if self.on_event:
            import getpass
            self.on_event(sid, "session_start", {
                "name": session.name, "agent": kind, "folder": cwd, "learn": learn, "safe": safe,
                "sign_in": "key saved in Hidee" if extra.get("HIDEE_KEY") else "the user's own sign-in",
                "user": getpass.getuser()})
        threading.Thread(target=self._pump, args=(session,), daemon=True).start()
        return session

    def _practice(self, name: str):
        """A practice lesson: a fictional clinic task played through the real checkpoint, always in learn mode.
        No process runs and nothing leaves this computer."""
        if self.practice is None:
            raise FileNotFoundError("practice")
        self.practice.on_event = self.on_event
        session = self.practice.create(name or "Practice lesson", kind="practice", learn=True)
        self.sessions[session.id] = session
        return session

    def _scripted(self, sid: str) -> bool:
        return getattr(self.sessions.get(sid), "kind", "") == "practice"

    def _pump(self, s: Session) -> None:
        while True:
            try:
                data = os.read(s.fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            with s.lock:
                s.scrollback += data
                del s.scrollback[:-SCROLLBACK]
                listeners = list(s.listeners)
            for listener in listeners:
                listener(data)
        s.process.wait()
        if self.on_event:
            self.on_event(s.id, "session_end", {"exit_code": s.process.returncode})
        note = f"\r\n[process exited with code {s.process.returncode}]\r\n".encode()
        with s.lock:
            s.scrollback += note
            listeners = list(s.listeners)
        for listener in listeners:
            listener(note)

    def subscribe(self, sid: str, listener: Callable[[bytes], None]) -> bytes:
        """Add a listener and return the scrollback so far, atomically, so no output is lost or doubled."""
        s = self.sessions[sid]
        with s.lock:
            s.listeners.append(listener)
            return bytes(s.scrollback)

    def unsubscribe(self, sid: str, listener) -> None:
        s = self.sessions.get(sid)
        if s:
            with s.lock:
                if listener in s.listeners:
                    s.listeners.remove(listener)

    def write(self, sid: str, data: str) -> None:
        if self._scripted(sid):
            return                      # a practice lesson follows its script; typing changes nothing
        s = self.sessions[sid]
        if s.alive:
            os.write(s.fd, data.encode("utf-8"))

    def resize(self, sid: str, rows: int, cols: int) -> None:
        if self._scripted(sid):
            return
        s = self.sessions[sid]
        if s.alive:
            _set_size(s.fd, max(2, min(rows, 500)), max(2, min(cols, 1000)))
            try:
                os.killpg(s.process.pid, signal.SIGWINCH)
            except OSError:
                pass

    def close(self, sid: str) -> None:
        if self._scripted(sid):
            self.sessions.pop(sid)
            self.practice.close(sid)
            return
        s = self.sessions.pop(sid, None)
        if s is None:
            return
        if s.alive:
            try:
                os.killpg(s.process.pid, signal.SIGHUP)
                s.process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                s.process.kill()
        try:
            os.close(s.fd)
        except OSError:
            pass

    def close_all(self) -> None:
        for sid in list(self.sessions):
            self.close(sid)


def _set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
