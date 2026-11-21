"""The network gate: a local HTTP proxy that every session's connections go through.

The hook sees what a tool call says; the gate sees where a session actually connects, including scripts, package
managers, and MCP servers the session starts. For HTTPS it sees only the host (the content stays encrypted);
for plain HTTP it also sees the URL. Each session authenticates with its own id, so every connection is
attributed to a session. Connections the rules forbid are refused; everything else passes and is recorded.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import ipaddress
import re
import socket
import secrets
import threading
from typing import Callable
from urllib.parse import urlsplit

MODEL_HOSTS = re.compile(r"(?:(?:^|\.)(?:anthropic\.com|claude\.ai|oauth2\.googleapis\.com"
                         r"|generativelanguage\.googleapis\.com|cloudcode-pa\.googleapis\.com"
                         r"|openai\.com|chatgpt\.com)"
                         r"|(?:^|[.-])aiplatform\.googleapis\.com"
                         r"|(?:^|\.)bedrock(?:-runtime)?\.[\w-]+\.amazonaws\.com)$", re.I)
HEAD_LIMIT = 64 * 1024

# check(session, host, url) -> (allowed, reason); record(session, host, port, url, allowed)
Check = Callable[[str, str, str], tuple[bool, str]]
Record = Callable[[str, str, int, str, bool], None]


def is_model_host(host: str) -> bool:
    return bool(MODEL_HOSTS.search(host))


LOCAL_NAMES = re.compile(r"(?:^|\.)localhost$|^metadata\.google\.internal$", re.I)


def is_local_host(host: str) -> bool:
    """True for names and addresses that reach this computer or its cloud metadata service."""
    host = host.strip("[]").lower()
    if LOCAL_NAMES.search(host):
        return True
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(info[4][0].split("%")[0])
                         for info in socket.getaddrinfo(host, None)}
        except (OSError, ValueError):
            return False
    return any(a.is_loopback or a.is_link_local or a.is_unspecified for a in addresses)


class Gate:
    def __init__(self, token: str, sessions: Callable[[], set[str]], check: Check, record: Record) -> None:
        self.token, self.sessions, self.check, self.record = token, sessions, check, record
        self.port: int | None = None
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.ports: dict[str, int] = {}
        self.safe: set[str] = set()          # safe-mode sessions: local services refused on every port

    def proxy_url(self, session: str) -> str:
        self.start()
        return f"http://{session}:{self.token}@127.0.0.1:{self.port}"

    def session_port(self, session: str, refuse_local: bool = True) -> int:
        """A port of its own for one session, for a proxy setting that cannot carry credentials (a sandbox's, or a
        Gemini MCP server's). Connections to it count as that session's; local services are refused on it unless
        `refuse_local` is False (a session outside safe mode)."""
        self.start()
        if session not in self.ports:
            handler = functools.partial(self._handle, fixed=session, refuse_local=refuse_local)
            future = asyncio.run_coroutine_threadsafe(
                asyncio.start_server(handler, "127.0.0.1", 0, limit=HEAD_LIMIT), self._loop)
            self.ports[session] = future.result(5).sockets[0].getsockname()[1]
        return self.ports[session]

    def start(self) -> int:
        if self.port is None:
            threading.Thread(target=self._run, daemon=True, name="hidee-gate").start()
            self._ready.wait(5)
        return self.port

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        server = self._loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0, limit=HEAD_LIMIT))
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()

    def _session(self, headers: dict[str, str]) -> str | None:
        value = headers.get("proxy-authorization", "")
        if not value.lower().startswith("basic "):
            return None
        try:
            user, _, password = base64.b64decode(value[6:]).decode().partition(":")
        except ValueError:
            return None
        if secrets.compare_digest(password, self.token) and user in self.sessions():
            return user
        return None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      fixed: str | None = None, refuse_local: bool = False) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            return await _reply(writer, 400, "Bad request")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        session = fixed or self._session(headers)
        if session is None:
            return await _reply(writer, 407, "Hidee needs this session's proxy credentials",
                                'Proxy-Authenticate: Basic realm="hidee"\r\n')
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            host, port, url = host.strip("[]"), int(port or 443), f"https://{target}"
        else:
            parts = urlsplit(target)
            if not parts.hostname:
                return await _reply(writer, 400, "Bad request")
            host, port, url = parts.hostname, parts.port or 80, target
        if (refuse_local or session in self.safe) and await asyncio.get_running_loop().run_in_executor(None, is_local_host, host):
            self.record(session, host.lower(), port, url, False)
            return await _reply(writer, 403, "Hidee: services on this computer are off limits in safe mode")
        allowed, reason = self.check(session, host.lower(), url)
        self.record(session, host.lower(), port, url, allowed)
        if not allowed:
            return await _reply(writer, 403, reason)
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), 15)
        except (OSError, asyncio.TimeoutError):
            return await _reply(writer, 502, f"Could not reach {host}")
        if method.upper() == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer), return_exceptions=True)
            return
        # Plain HTTP: forward exactly this one request, checked above. A client may reuse the connection for more
        # requests (keep-alive); those would reach this host unchecked and carry the proxy credentials, so the
        # host is told to close, the body is forwarded by its length only, and nothing else is read.
        if "chunked" in headers.get("transfer-encoding", "").lower():
            up_writer.close()
            return await _reply(writer, 411, "Hidee's gate needs a Content-Length for plain HTTP requests")
        parts = urlsplit(target)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        drop = ("proxy-authorization:", "proxy-connection:", "connection:", "keep-alive:")
        kept = [l for l in lines[1:] if l and not l.lower().startswith(drop)] + ["Connection: close"]
        up_writer.write((f"{method} {path} {version}\r\n" + "\r\n".join(kept) + "\r\n\r\n").encode("latin-1"))
        try:
            length = int(headers.get("content-length", "0") or 0)
            if length:
                up_writer.write(await reader.readexactly(length))
            await up_writer.drain()
        except (ValueError, asyncio.IncompleteReadError, ConnectionError):
            up_writer.close()
            return await _reply(writer, 400, "Bad request")
        await _pipe(up_reader, writer)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def _reply(writer: asyncio.StreamWriter, code: int, text: str, extra: str = "") -> None:
    words = {400: "Bad Request", 403: "Forbidden", 407: "Proxy Authentication Required", 411: "Length Required",
             502: "Bad Gateway"}
    body = (text + "\n").encode()
    writer.write(f"HTTP/1.1 {code} {words[code]}\r\n{extra}Content-Type: text/plain; charset=utf-8\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    try:
        await writer.drain()
    finally:
        writer.close()
