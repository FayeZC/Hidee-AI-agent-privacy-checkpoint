"""Updates, only with the user's consent.

The first time `hidee app` runs in a terminal it asks once whether Hidee may check for updates every two weeks;
the answer is kept in update.json in Hidee's home and can be changed in Settings. Without a yes, Hidee never
checks on its own. A check is one plain request for a small JSON file (the newest version, its file, SHA-256,
signature, and notes); it carries no account, id, or usage data. Nothing installs without the user saying yes,
and an update installs only if its SHA-256 matches and its Ed25519 signature verifies against the release key
built into this code, so a changed file or a compromised download server cannot push code to users.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__
from .ed25519 import verify

CHECK_EVERY_S = 14 * 24 * 3600
DEFAULT_URL = "https://hidee.net/latest.json"
# The release signing key's public half. The private half stays on the maker's computer (scripts/release_key.py).
PUBLIC_KEY = bytes.fromhex("4ffa47939163e1a47fd8b0aa0a834b4609a0d5ea1ca5eaea125d2fb0d7784ff6")
TIMEOUT_S = 5
MAX_WHEEL = 50 * 1024 * 1024

Fetch = Callable[[str, int], bytes]          # (url, size limit) -> body


class UpdateError(Exception):
    pass


def update_url() -> str:
    return os.environ.get("HIDEE_UPDATE_URL", DEFAULT_URL)


def fetch(url: str, limit: int = 1024 * 1024) -> bytes:
    """One plain GET. The only header Hidee adds is a User-Agent with its version."""
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise UpdateError("Updates come only over HTTPS.")
    request = urllib.request.Request(url, headers={"User-Agent": f"hidee/{__version__}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:   # noqa: S310 (scheme checked above)
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("The download is larger than expected.")
    return data


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def is_newer(candidate: str, current: str = __version__) -> bool:
    return version_key(candidate) > version_key(current)


def signed_message(version: str, sha256: str) -> bytes:
    """What the release key signs: the version and the file's SHA-256, so neither can be swapped."""
    return f"hidee-release\n{version}\n{sha256}\n".encode()


class Updates:
    """The user's answer, when Hidee last checked, and what it found. Kept in <home>/update.json (0600)."""

    def __init__(self, home: Path, clock=time.time, get: Fetch = fetch, public_key: bytes = PUBLIC_KEY) -> None:
        self.path = Path(home) / "update.json"
        self.clock, self.get, self.public_key = clock, get, public_key

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @property
    def consent(self) -> bool | None:
        """True or False once the user has answered; None before."""
        value = self._load().get("check")
        return value if isinstance(value, bool) else None

    def set_consent(self, check: bool) -> None:
        data = self._load()
        data["check"] = bool(check)
        self._save(data)

    @property
    def last_check(self) -> float | None:
        return self._load().get("last_check")

    def due(self) -> bool:
        """Only with consent, and at most once every two weeks."""
        return self.consent is True and self.clock() - float(self.last_check or 0) >= CHECK_EVERY_S

    def check(self) -> dict | None:
        """Ask for the newest version now. Returns its details if it is newer than this one, otherwise None."""
        info = json.loads(self.get(update_url(), 1024 * 1024))
        for key in ("version", "url", "sha256"):
            if not isinstance(info.get(key), str):
                raise UpdateError("The update information is incomplete.")
        data = self._load()
        data["last_check"] = self.clock()
        data["latest"] = {k: info.get(k) for k in ("version", "wheel", "url", "sha256", "signature", "notes")}
        self._save(data)
        return data["latest"] if is_newer(info["version"]) else None

    def available(self) -> dict | None:
        """The newer version found by the last check, if any (no network)."""
        latest = self._load().get("latest")
        return latest if isinstance(latest, dict) and is_newer(str(latest.get("version", ""))) else None

    def download(self, info: dict) -> Path:
        """Fetch the wheel and accept it only if its SHA-256 matches and the release key signed it."""
        signature = info.get("signature")
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{128}", signature):
            raise UpdateError("This release is not signed, so Hidee will not install it.")
        data = self.get(info["url"], MAX_WHEEL)
        sha = hashlib.sha256(data).hexdigest()
        if sha != info["sha256"]:
            raise UpdateError("The download does not match its published SHA-256. Nothing was installed.")
        if not verify(self.public_key, signed_message(info["version"], sha), bytes.fromhex(signature)):
            raise UpdateError("The download is not signed by Hidee's release key. Nothing was installed.")
        name = info.get("wheel") or f"hidee-{info['version']}-py3-none-any.whl"
        if not re.fullmatch(r"hidee-[\w.]+-py3-none-any\.whl", name):
            raise UpdateError("The update has an unexpected file name.")
        folder = Path(tempfile.mkdtemp(prefix="hidee-update-"))
        wheel = folder / name
        wheel.write_bytes(data)
        return wheel


def is_app_bundle() -> bool:
    """True inside Hidee.app (the .dmg), where a new version comes as a new app, not as a wheel."""
    return bool(getattr(sys, "frozen", False))


DOWNLOAD_PAGE = "https://hidee.net/download"


def is_development_checkout() -> bool:
    """True when Hidee runs from a source folder (an editable install), which `git pull` updates instead."""
    here = Path(__file__).resolve()
    return not any(part in ("site-packages", "dist-packages") for part in here.parts)


def install(wheel: Path, run=subprocess.run) -> None:
    """Install the verified wheel into the Python environment Hidee runs in: pip if it has it, else uv."""
    if is_app_bundle():
        raise UpdateError(f"This is the Hidee app. Download the new version from {DOWNLOAD_PAGE} and replace it.")
    if is_development_checkout():
        raise UpdateError("This Hidee runs from a source folder; update it with git pull instead.")
    if run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        command = [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", str(wheel)]
    elif shutil.which("uv"):
        command = ["uv", "pip", "install", "--quiet", "--python", sys.executable, "--upgrade", str(wheel)]
    else:
        raise UpdateError("Hidee could not find pip or uv to install the update.")
    if run(command).returncode != 0:
        raise UpdateError("The update did not install. Your current version is unchanged.")


def describe(info: dict) -> str:
    lines = [f"Hidee {info['version']} is available (you have {__version__})."]
    lines += [f"  - {note}" for note in (info.get("notes") or [])[:6] if isinstance(note, str)]
    return "\n".join(lines)


def first_run(updates: Updates, ask: Callable[[str], str], say: Callable[[str], None]) -> None:
    """Ask once, the first time Hidee runs in a terminal. An empty answer means yes, as the [Y/n] shows."""
    if updates.consent is not None:
        return
    answer = ask("Check for Hidee updates every 2 weeks? [Y/n] ").strip().lower()
    updates.set_consent(answer not in ("n", "no"))
    say("You can change this any time in Settings." if updates.consent
        else "Hidee will not check for updates. You can turn this on in Settings, or run `hidee update`.")


def offer(updates: Updates, ask: Callable[[str], str], say: Callable[[str], None], install_fn=install,
          force: bool = False, assume_yes: bool = False) -> bool:
    """Check (when due, or now if forced), show what is new, and install only on a yes. True if it installed."""
    if not force and not updates.due():
        return False
    try:
        info = updates.check()
    except (OSError, ValueError, UpdateError) as e:
        if force:
            say(f"Could not check for updates: {e}")
        return False
    if info is None:
        if force:
            say(f"Hidee {__version__} is the newest version.")
        return False
    say(describe(info))
    if not assume_yes and ask("Update now? [y/N] ").strip().lower() not in ("y", "yes"):
        say("Not now. Run `hidee update` when you are ready.")
        return False
    try:
        install_fn(updates.download(info))
    except (OSError, UpdateError) as e:
        say(str(e))
        return False
    say(f"Updated to Hidee {info['version']}. Start it again with: hidee app")
    return True
