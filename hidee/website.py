"""hidee.net: the product site, the download, and the entry to the online demo.

One process serves both hosts: the site (hidee.net) and the online demo (try.hidee.net), chosen by the Host
header. Locally, http://localhost:PORT is the site and http://try.localhost:PORT is the demo.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .web import STATIC_DIR, templates

INSTALL_SH = """#!/bin/sh
# Install Hidee {version}: a local privacy checkpoint for AI agents.
# It installs one command, `hidee`, for your user only. Nothing else on your computer changes.
set -eu
NAME="{wheel}"
URL="{site}/download/{wheel}"
SHA256="{sha256}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$URL" -o "$TMP/$NAME"
if command -v shasum >/dev/null 2>&1; then GOT=$(shasum -a 256 "$TMP/$NAME" | cut -d' ' -f1)
else GOT=$(sha256sum "$TMP/$NAME" | cut -d' ' -f1); fi
if [ "$GOT" != "$SHA256" ]; then
  echo "The download does not match its published SHA-256. Nothing was installed." >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$TMP/$NAME"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$TMP/$NAME"
else
  # No uv or pipx: Hidee gets its own virtual environment, so the system Python is left alone.
  PY=""
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Hidee needs Python 3.11 or newer, or uv (https://docs.astral.sh/uv/)." >&2
    exit 1
  fi
  "$PY" -m venv --clear "$HOME/.hidee/venv"
  "$HOME/.hidee/venv/bin/python" -m pip install --quiet --upgrade "$TMP/$NAME"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$HOME/.hidee/venv/bin/hidee" "$HOME/.local/bin/hidee"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo 'Add ~/.local/bin to your PATH to run hidee: export PATH="$HOME/.local/bin:$PATH"' ;;
  esac
fi
echo
echo "Hidee is installed. Start it with:  hidee app"
"""


class Release:
    """The newest wheel in the release folder, with its SHA-256, its release signature, and its notes."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    @property
    def wheel(self) -> Path | None:
        from .update import version_key
        wheels = sorted(self.folder.glob("hidee-*-py3-none-any.whl"), key=lambda w: version_key(w.name.split("-")[1]))
        return wheels[-1] if wheels else None      # by version, so 0.10.0 comes after 0.9.0

    @property
    def dmg(self) -> Path | None:
        """The newest Mac app disk image (Hidee-<version>-macos-<arch>.dmg), if one was built."""
        from .update import version_key
        images = sorted(self.folder.glob("Hidee-*-macos-*.dmg"), key=lambda d: version_key(d.name.split("-")[1]))
        return images[-1] if images else None

    def mac_app(self) -> dict | None:
        image = self.dmg
        if image is None:
            return None
        return {"name": image.name, "size_mb": round(image.stat().st_size / 1024 / 1024),
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "version": image.name.split("-")[1], "arch": image.stem.rsplit("-", 1)[-1]}

    def info(self) -> dict | None:
        wheel = self.wheel
        if wheel is None:
            return None
        sig, notes = wheel.with_name(wheel.name + ".sig"), wheel.with_name(wheel.name + ".notes.json")
        try:
            note_list = json.loads(notes.read_text(encoding="utf-8")) if notes.exists() else []
        except ValueError:
            note_list = []
        return {"name": wheel.name, "size_kb": round(wheel.stat().st_size / 1024),
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "version": wheel.name.split("-")[1],
                "signature": sig.read_text().strip() if sig.exists() else None,
                "notes": [n for n in note_list if isinstance(n, str)][:6]}


def specimen() -> dict:
    """One real checked step, made by the real engine from fictional data, drawn the way the product draws it."""
    import tempfile
    import yaml

    from .detect import risks_of
    from .engine import Engine
    from .executors import Executors, PassThrough
    from .flow import render_flow, timeline
    from .hook_api import APP_RULES
    from .rules import from_dict
    from .store import Store
    from .toolmap import map_call
    from .web import _when, view
    rules = from_dict(yaml.safe_load(APP_RULES))
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "specimen.db")
        engine = Engine(store, lambda: rules, Executors(PassThrough()))
        query = "reminder wording for maria.lopez@example.org, root canal follow-up, call 917-555-0142"
        m = map_call("WebSearch", {"query": query})
        rec = engine.decide(engine.submit("session:demo", m.kind, m.recipient, m.fields, m.minimal).id, "minimal")
        case = view(rec, rules, {"session:demo": "Clinic assistant"})
        events = timeline(store.audit_for(rec.id), case, _when)
        store.close()
    executed = rec.executed or {}
    return {"title": f"{case['agent']} → {case['recipient']}", "out_word": case["out_word"],
            "risks": risks_of(f["category"] for f in rec.fields.values()), "flow": render_flow(rec, case),
            "reason": "You chose the shorter version: the search ran without her email address and phone number.",
            "asked": query, "sent": list(executed.values()),
            "left": [f["value"] for n, f in rec.fields.items() if f["category"] != "plain"],
            "events": [e["text"] for e in events]}


def create_site_app(site_url: str, try_url: str, release_dir: Path) -> FastAPI:
    app = FastAPI(title="Hidee", docs_url=None, redoc_url=None, openapi_url=None)
    release = Release(release_dir)
    sample = specimen()

    def page(request: Request, name: str, **extra):
        return templates.TemplateResponse(request, name, {
            "site_url": site_url, "try_url": try_url, "release": release.info(), "mac_app": release.mac_app(), "version": __version__,
            **extra})

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page(request, "site_home.html", active="home", sample=sample)

    @app.get("/download", response_class=HTMLResponse)
    def download(request: Request):
        return page(request, "site_download.html", active="download")

    @app.get("/docs", response_class=HTMLResponse)
    def docs(request: Request):
        return page(request, "site_docs.html", active="docs")

    @app.get("/install.sh", response_class=PlainTextResponse)
    def install_sh():
        info = release.info()
        if info is None:
            raise HTTPException(503, "No release is available yet.")
        return INSTALL_SH.format(version=info["version"], site=site_url, wheel=info["name"], sha256=info["sha256"])

    @app.get("/latest.json")
    def latest():
        """What `hidee update` and the opt-in check read: the newest version, where it is, and how to verify it."""
        info = release.info()
        if info is None:
            raise HTTPException(503, "No release is available yet.")
        return JSONResponse({"version": info["version"], "wheel": info["name"],
                             "url": f"{site_url}/download/{info['name']}", "sha256": info["sha256"],
                             "signature": info["signature"], "notes": info["notes"]},
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/download/{name}")
    def wheel(name: str):
        for f in (release.wheel, release.dmg):          # only the current wheel and disk image, by exact name
            if f is not None and name == f.name:
                return FileResponse(f, media_type="application/octet-stream", filename=f.name)
        raise HTTPException(404, "Not found")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


class ByHost:
    """Send try.* hosts to the online demo and everything else to the site."""

    def __init__(self, site, online) -> None:
        self.site, self.online = site, online

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.site(scope, receive, send)
        host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1").lower()
        target = self.online if host.startswith("try.") else self.site
        await target(scope, receive, send)


def create_web_app(site_url: str, try_url: str, release_dir: Path, pace: float = 1.6):
    from .online import create_online_app
    online = create_online_app(pace=pace, site_url=site_url)
    return ByHost(create_site_app(site_url, try_url, release_dir), online), online
