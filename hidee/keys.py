"""API keys a student saves in Hidee, so an agent can start without any terminal setup.

Keys live in the system keychain (macOS Keychain through the `security` tool). Where there is none, they go to a
file only the user can read. Hidee shows a saved key only as its last four characters, never writes it to its
database or to the learn-mode timeline, and hands it to one agent session as an environment variable.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

SERVICE = "hidee"

PROVIDERS = {
    "anthropic": {"label": "Anthropic", "agent": "Claude Code", "env": "ANTHROPIC_API_KEY",
                  "hint": "Starts with sk-ant-. Create one at console.anthropic.com.",
                  # A saved key should win over Vertex AI, Bedrock, or a gateway set in the shell profile.
                  "unset": ("CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY",
                            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")},
    "gemini": {"label": "Gemini", "agent": "Gemini CLI", "env": "GEMINI_API_KEY",
               "hint": "Create one at aistudio.google.com.",
               "unset": ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_API_KEY")},
    "openai": {"label": "OpenAI", "agent": "Codex CLI", "env": "OPENAI_API_KEY",
               "hint": "Starts with sk-. Create one at platform.openai.com.",
               "unset": ("OPENAI_BASE_URL",)},
}

CHECKS = {
    "anthropic": ("https://api.anthropic.com/v1/models", lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"}),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models", lambda k: {"x-goog-api-key": k}),
    "openai": ("https://api.openai.com/v1/models", lambda k: {"Authorization": f"Bearer {k}"}),
}

KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-.:]{8,300}$")


class MemoryBackend:
    """For tests."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


class MacKeychain:
    """The login keychain, through /usr/bin/security. The key goes in on stdin, never on a command line."""

    def get(self, name: str) -> str | None:
        done = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                              capture_output=True, text=True)
        return done.stdout.strip() or None if done.returncode == 0 else None

    def set(self, name: str, value: str) -> None:
        command = f'add-generic-password -U -s {SERVICE} -a {name} -w "{value}"\n'
        subprocess.run(["security", "-i"], input=command, capture_output=True, text=True, check=True)

    def delete(self, name: str) -> None:
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name], capture_output=True)


class FileBackend:
    """A JSON file readable only by the user, for systems without a keychain tool."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        self._write({**self._read(), name: value})

    def delete(self, name: str) -> None:
        data = self._read()
        data.pop(name, None)
        self._write(data)


def default_backend(home: Path):
    if shutil.which("security") and os.uname().sysname == "Darwin":
        return MacKeychain()
    return FileBackend(Path(home) / "keys.json")


class KeyStore:
    def __init__(self, backend) -> None:
        self.backend = backend

    def save(self, provider: str, key: str) -> None:
        if provider not in PROVIDERS:
            raise KeyError(provider)
        key = key.strip()
        if not KEY_SHAPE.match(key):
            raise ValueError("That does not look like an API key.")
        self.backend.set(provider, key)

    def get(self, provider: str) -> str | None:
        return self.backend.get(provider) if provider in PROVIDERS else None

    def masked(self, provider: str) -> str | None:
        key = self.get(provider)
        return f"••••{key[-4:]}" if key else None

    def remove(self, provider: str) -> None:
        if provider in PROVIDERS:
            self.backend.delete(provider)


def check_key(provider: str, key: str, opener=urllib.request.urlopen) -> tuple[bool, str]:
    """Ask the provider whether the key works, with one small read-only request (list models)."""
    url, headers = CHECKS[provider]
    try:
        with opener(urllib.request.Request(url, headers=headers(key)), timeout=15) as response:
            response.read()
        return True, "The key works."
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return False, f"{PROVIDERS[provider]['label']} rejected this key ({e.code})."
        return False, f"{PROVIDERS[provider]['label']} answered with an error ({e.code}). Try again later."
    except (OSError, ValueError) as e:
        return False, f"Could not reach {PROVIDERS[provider]['label']} ({type(e).__name__})."


def launch_prefix(provider: str, key: str) -> tuple[dict, str]:
    """Environment and shell lines that give one session the saved key. The key travels in the environment of the
    session's shell, never on its command line, and is moved into the provider's variable just before the agent
    starts."""
    p = PROVIDERS[provider]
    prefix = (f"unset {' '.join(p['unset'])}; " if p["unset"] else "") + \
             f'export {p["env"]}="$HIDEE_KEY"; unset HIDEE_KEY; '
    return {"HIDEE_KEY": key}, prefix
