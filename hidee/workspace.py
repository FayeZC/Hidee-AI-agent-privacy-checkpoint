"""The app workspace: sessions on the left, a real terminal in the middle, the checkpoint on the right."""
from __future__ import annotations

import asyncio
import codecs
import json
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from .keys import PROVIDERS
from .web import _check_csrf, templates

router = APIRouter()
KINDS = {"claude": "Claude Code", "gemini": "Gemini CLI", "codex": "Codex CLI", "shell": "Shell",
         "practice": "Practice lesson"}


@router.get("/workspace", response_class=HTMLResponse)
def workspace(request: Request, s: str | None = None, new: int = 0):
    state = request.app.state
    sessions = list(state.sessions.sessions.values())
    current = state.sessions.sessions.get(s) if s else (sessions[-1] if sessions else None)
    keys = getattr(state, "keys", None)
    return templates.TemplateResponse(request, "workspace.html", {
        "csrf": state.csrf, "sessions": sessions, "current": current,
        "starting": not state.online and (bool(new) or current is None),
        "saved_keys": [(KINDS[k], PROVIDERS[p]["label"], keys.masked(p)) for k, p in KEY_FOR.items()
                       if keys and keys.masked(p)],
        "kinds": getattr(state.sessions, "kinds", KINDS), "online": state.online,
        "site_url": getattr(state, "site_url", "https://hidee.net"),
        "download_url": getattr(state, "site_url", "https://hidee.net") + "/download",
        "default_cwd": "" if state.online else str(Path.home()),
        # A newer version found by the last check the user allowed (reading it needs no network).
        "update": state.updates.available() if getattr(state, "updates", None) is not None else None,
        "bundled": _bundled()})


PROJECTS = Path.home() / "Hidee Projects"
KEY_FOR = {"claude": "anthropic", "gemini": "gemini", "codex": "openai"}          # which saved key each agent signs in with


def project_folder(root: Path, name: str) -> Path:
    """A folder for a new project, named after it, kept inside the projects folder."""
    clean = " ".join("".join(ch for ch in name if ch.isalnum() or ch in " -_").split())
    return Path(root) / (clean or "Untitled project")


@router.post("/sessions")
def create_session(request: Request, csrf: str = Form(...), name: str = Form(""), cwd: str = Form(""),
                   kind: str = Form("claude"), learn: str = Form(""), where: str = Form("open"),
                   project: str = Form(""), signin: str = Form("terminal"), safe: str = Form("")):
    _check_csrf(request, csrf)
    state = request.app.state
    if kind not in getattr(state.sessions, "kinds", KINDS):
        raise HTTPException(400, "Unknown session kind")
    if kind == "practice":
        # A practice lesson uses its own fictional folder, needs no sign-in, and always shows Behind the agent.
        folder, learn, name = Path.home(), "1", name or "Practice lesson"
    elif where == "new" and getattr(state.sessions, "needs_folder", True):
        folder = project_folder(getattr(state, "projects_dir", PROJECTS), project)
        folder.mkdir(parents=True, exist_ok=True)
        name = name or folder.name
    else:
        folder = Path(cwd or Path.home()).expanduser()
        if getattr(state.sessions, "needs_folder", True) and not folder.is_dir():
            raise HTTPException(400, f"{folder} is not a folder")
        if cwd and not name:
            name = folder.name
    extra = {"learn": True} if learn else {}
    if safe and kind in KEY_FOR:                # agents only; a plain shell is the user's own
        home = Path.home().resolve()
        if folder.resolve() in (home, *home.parents):
            # The sandbox lets the agent write its project folder; that must not include Hidee's own records.
            raise HTTPException(400, "Safe mode needs a project folder, not your home folder. "
                                     "Start a new project or open a folder inside it.")
        if kind in ("gemini", "codex") and not shutil.which("sandbox-exec"):
            raise HTTPException(400, f"Safe mode for {KINDS.get(kind, kind)} uses the macOS sandbox, which this "
                                     "computer does not have. Turn off Safe mode, or use Claude Code.")
        extra["safe"] = True
    if signin == "key" and kind in KEY_FOR:
        from .keys import PROVIDERS, launch_prefix
        provider = KEY_FOR[kind]
        key = state.keys.get(provider) if getattr(state, "keys", None) else None
        if not key:
            raise HTTPException(400, f"No {PROVIDERS[provider]['label']} key is saved. Add one in Settings.")
        extra["env"], extra["prefix"] = launch_prefix(provider, key)
        if kind == "gemini":
            extra["env"]["HIDEE_GEMINI_AUTH"] = "key"
    try:
        session = state.sessions.create(name.strip(), str(folder), kind, **extra)
    except FileNotFoundError:
        agent = KINDS.get(kind, kind)
        raise HTTPException(400, f"{agent} was not found. Install it, or start a shell session.") from None
    state.session_names[f"session:{session.id}"] = session.name
    if learn:
        state.learn.enable(session.id)
    return RedirectResponse(f"/workspace?s={session.id}", status_code=303)


@router.post("/sessions/{sid}/close")
def close_session(request: Request, sid: str, csrf: str = Form(...)):
    _check_csrf(request, csrf)
    request.app.state.sessions.close(sid)
    return RedirectResponse("/workspace", status_code=303)


def _origin_ok(ws: WebSocket) -> bool:
    """Only the app's own page may open a terminal; any other site could otherwise drive a local shell."""
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and bool(host) and parts.netloc == host


@router.websocket("/term/{sid}")
async def terminal(ws: WebSocket, sid: str, key: str = ""):
    state = ws.app.state
    manager = state.sessions
    if not secrets.compare_digest(key, state.csrf) or not _origin_ok(ws) or sid not in manager.sessions:
        await ws.close(code=1008)
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def listener(data: bytes) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, data)

    backlog = manager.subscribe(sid, listener)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    async def pump():
        if backlog:
            await ws.send_text(decoder.decode(backlog))
        while True:
            data = await queue.get()
            await ws.send_text(decoder.decode(data))

    sender = asyncio.create_task(pump())
    try:
        while True:
            message = json.loads(await ws.receive_text())
            if sid not in manager.sessions:
                break
            if message.get("type") == "input":
                manager.write(sid, str(message.get("data", "")))
            elif message.get("type") == "resize":
                manager.resize(sid, int(message.get("rows", 24)), int(message.get("cols", 80)))
    except (WebSocketDisconnect, ValueError, KeyError):
        pass
    finally:
        sender.cancel()
        manager.unsubscribe(sid, listener)


# ---- Settings: API keys saved in Hidee ----

def _keys(request: Request):
    keys = getattr(request.app.state, "keys", None)
    if keys is None:
        raise HTTPException(404, "Not found")
    return keys


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    from .keys import PROVIDERS
    from .web import _page
    keys = _keys(request)
    checks = getattr(request.app.state, "key_checks", {})
    rows = [{"id": pid, **p, "saved": keys.masked(pid), "check": checks.get(pid)} for pid, p in PROVIDERS.items()]
    from .privacy import data_inventory
    state = request.app.state
    updates = getattr(state, "updates", None)
    update = None
    if updates is not None:
        import time as _time
        last = updates.last_check
        update = {"consent": updates.consent, "available": updates.available(),
                  "last": _time.strftime("%b %d, %Y", _time.localtime(last)) if last else None,
                  "note": getattr(state, "update_note", None)}
        state.update_note = None
    from . import __version__
    return _page(request, "settings.html", active="settings", rows=rows, error=request.query_params.get("error"),
                 data=data_inventory(state.home), home=str(state.home), update=update, version=__version__,
                 bundled=_bundled())


@router.post("/settings/reveal")
def reveal_data(request: Request, csrf: str = Form(...)):
    """Open Hidee's data folder in Finder (or the system file manager)."""
    import platform
    import subprocess
    _check_csrf(request, csrf)
    _keys(request)                                        # the desktop app only
    opener = "open" if platform.system() == "Darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, str(request.app.state.home)])
    except OSError:
        pass
    return RedirectResponse("/settings#data", status_code=303)


@router.post("/settings/updates")
def set_updates(request: Request, csrf: str = Form(...), check: str = Form("")):
    """Turn the two-weekly update check on or off."""
    _updates(request)
    _check_csrf(request, csrf)
    request.app.state.updates.set_consent(check == "on")
    return RedirectResponse("/settings#updates", status_code=303)


@router.post("/settings/updates/check")
def check_updates_now(request: Request, csrf: str = Form(...)):
    """Check now, because the user clicked: the one network request is theirs, whatever the setting."""
    from .update import UpdateError
    _updates(request)
    _check_csrf(request, csrf)
    try:
        found = request.app.state.updates.check()
        request.app.state.update_note = None if found else "You have the newest version."
    except (OSError, ValueError, UpdateError) as e:
        request.app.state.update_note = f"Could not check: {e}"
    return RedirectResponse("/settings#updates", status_code=303)


@router.post("/settings/keys/{provider}")
def save_key(request: Request, provider: str, csrf: str = Form(...), key: str = Form("")):
    _check_csrf(request, csrf)
    keys = _keys(request)
    try:
        keys.save(provider, key)
    except KeyError:
        raise HTTPException(404, "Unknown provider") from None
    except ValueError:
        return RedirectResponse(f"/settings?error={provider}#{provider}", status_code=303)
    request.app.state.key_checks.pop(provider, None)
    return RedirectResponse(f"/settings#{provider}", status_code=303)


@router.post("/settings/keys/{provider}/test")
def test_key(request: Request, provider: str, csrf: str = Form(...)):
    from .keys import check_key
    _check_csrf(request, csrf)
    key = _keys(request).get(provider)
    if key:
        request.app.state.key_checks[provider] = check_key(provider, key)
    return RedirectResponse(f"/settings#{provider}", status_code=303)


@router.post("/settings/keys/{provider}/remove")
def remove_key(request: Request, provider: str, csrf: str = Form(...)):
    _check_csrf(request, csrf)
    _keys(request).remove(provider)
    request.app.state.key_checks.pop(provider, None)
    return RedirectResponse(f"/settings#{provider}", status_code=303)


@router.post("/pick-folder")
def pick_folder(request: Request, csrf: str = Form(...)):
    """Open the system's own folder chooser on this computer and return the chosen path."""
    import platform
    import shutil
    import subprocess
    _check_csrf(request, csrf)
    if getattr(request.app.state, "online", False):
        raise HTTPException(404, "Not found")
    if platform.system() == "Darwin":
        cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a folder to work in")']
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--title=Choose a folder to work in"]
    else:
        return {"path": None, "unsupported": True}
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return {"path": None}
    path = done.stdout.strip().rstrip("/") if done.returncode == 0 else ""
    return {"path": path or None}


# ---- Evidence: the customer-side record ----

def _days(day_from: str | None, day_to: str | None) -> tuple[str | None, str | None]:
    """An optional range of UTC days; anything that is not YYYY-MM-DD is refused."""
    import datetime
    out = []
    for day in (day_from, day_to):
        day = (day or "").strip() or None
        if day is not None:
            try:
                if datetime.date.fromisoformat(day).isoformat() != day:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, f"{day} is not a day in the form YYYY-MM-DD") from None
        out.append(day)
    return out[0], out[1]


def _bundled() -> bool:
    from .update import is_app_bundle
    return is_app_bundle()


def _updates(request: Request):
    """Updates exist only in the desktop app, not in the online demo."""
    updates = getattr(request.app.state, "updates", None)
    if updates is None:
        raise HTTPException(404, "Not found")
    return updates


def _local_only(request: Request) -> None:
    """The evidence log is the user's own record on their computer; the online demo does not serve it."""
    if request.app.state.online:
        raise HTTPException(404, "Not found")


@router.get("/evidence", response_class=HTMLResponse)
def evidence_page(request: Request):
    _local_only(request)
    from .evidence import sessions, verify
    from .reconcile import CHOICE_WORDS, CHOICES, reconcile
    from .web import _page
    state = request.app.state
    folder = state.evidence.folder
    day_from, day_to = _days(request.query_params.get("from"), request.query_params.get("to"))
    ok, count, bad = verify(folder)
    seq, head = state.evidence.head()
    return _page(request, "evidence.html", active="evidence", ok=ok, count=count, bad=bad, head=head, seq=seq,
                 folder=str(folder), rows=sessions(folder)[:200], agents=KINDS,
                 rec=reconcile(folder, day_from, day_to), day_from=day_from or "", day_to=day_to or "",
                 choices=[(c, CHOICE_WORDS[c]) for c in CHOICES])


def _zip(files: dict[str, bytes], filename: str):
    import io
    import zipfile
    from fastapi.responses import Response
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _range_name(day_from: str | None, day_to: str | None) -> str:
    return f"-{day_from or 'start'}-to-{day_to or 'now'}" if day_from or day_to else ""


@router.post("/evidence/reconcile")
def evidence_reconcile(request: Request, csrf: str = Form(...), day_from: str = Form(""), day_to: str = Form("")):
    _local_only(request)
    """The reconciliation report (Markdown and CSV) for the chosen days, with a manifest of the chain's state."""
    from .reconcile import reconcile_files
    _check_csrf(request, csrf)
    day_from, day_to = _days(day_from, day_to)
    files = reconcile_files(request.app.state.evidence.folder, day_from, day_to)
    return _zip(files, f"hidee-reconciliation{_range_name(day_from, day_to)}.zip")


@router.post("/evidence/timeline")
def evidence_timeline(request: Request, csrf: str = Form(...), day_from: str = Form(""), day_to: str = Form("")):
    _local_only(request)
    """The forensic timeline CSV for the chosen days, with a manifest of the chain's state."""
    from .reconcile import timeline_files
    _check_csrf(request, csrf)
    day_from, day_to = _days(day_from, day_to)
    files = timeline_files(request.app.state.evidence.folder, day_from, day_to)
    return _zip(files, f"hidee-timeline{_range_name(day_from, day_to)}.zip")


@router.post("/evidence/export")
def evidence_export(request: Request, csrf: str = Form(...)):
    _local_only(request)
    import tempfile
    from fastapi.responses import FileResponse
    from .evidence import export
    _check_csrf(request, csrf)
    out = Path(tempfile.mkdtemp()) / "hidee-evidence.zip"
    export(request.app.state.evidence.folder, out)
    return FileResponse(out, media_type="application/zip", filename="hidee-evidence.zip")
