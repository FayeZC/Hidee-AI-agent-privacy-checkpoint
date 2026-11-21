# Developing Hidee

## 1. Setup

Hidee needs Python 3.11 or newer. The project uses [uv](https://docs.astral.sh/uv/) and a virtual environment in
`.venv`:

```bash
git clone https://github.com/FayeZC/Hidee-AI-agent-privacy-checkpoint.git && cd Hidee-AI-agent-privacy-checkpoint
uv venv
uv pip install -e ".[dev]"       # Hidee in editable mode, plus pytest
```

Without uv: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.

Runtime dependencies (`pyproject.toml`): FastAPI, uvicorn, Jinja2, PyYAML, httpx, python-multipart, websockets.
The hook scripts (`hidee/hook.py`, `hidee/learn_hook.py`) use only the standard library. The browser side is plain
JavaScript and CSS with no build step; xterm.js 6 and its fit addon are vendored in `hidee/webui/static/vendor/`,
and fonts in `hidee/webui/static/fonts/` (OFL licenses alongside).

## 2. Tests

```bash
.venv/bin/python -m pytest -q tests
```

The suite has about 360 tests (one file per module, `tests/test_<module>.py`, plus `tests/core/` for the ported
gateway). It needs no network, no API key, and no installed agent: sessions are tested with stand-in programs, hook
scripts with stand-in HTTP openers, and the app through FastAPI's test client. Tests use temporary folders, never
`~/.hidee`. CI (`.github/workflows/ci.yml`) runs the suite on macOS and Linux with Python 3.11 and 3.13, then
installs the built wheel into a clean environment and starts the app.

Useful subsets:

```bash
.venv/bin/python -m pytest -q tests/test_hook_api.py tests/test_toolmap.py tests/test_detect.py   # the check
.venv/bin/python -m pytest -q tests/test_evidence.py                                              # the log
.venv/bin/python -m pytest -q tests/test_gemini.py tests/test_codex.py tests/test_sessions.py     # agents
```

## 3. Project layout

```
hidee/
  cli.py            command line (hidee app | serve | demo | web | cloud-demo | evidence | agent)
  app.py            create_app: wiring of store, rules, engine, routers, and app-mode services
  sessions.py       PTY sessions, per-agent launch, hook settings, safe mode, Gemini profile, Codex overrides
  hook.py           checking hook script (fails closed)
  learn_hook.py     watching hook script (fails open)
  hook_api.py       hook endpoints, check_call, replies, evidence for turns and decisions, APP_RULES
  dialects.py       Gemini CLI translation
  toolmap.py        tool call -> action; scripts; local overreach
  detect.py         pattern detectors, redaction, labels, risk groups
  rules.py          rules file, matching, evaluation
  engine.py         submit, review, expire, one-use grants, execution
  store.py          SQLite
  executors.py      what runs after approval
  gate.py           network gate (HTTP proxy)
  withheld.py       held-back details and disguised retries
  learn.py          learn-mode timeline and token usage
  evidence.py       evidence log, verify, export
  keys.py           API keys (keychain or 0600 file)
  live.py           live flow feed
  workspace.py      workspace, sessions, terminal WebSocket, Settings, Evidence routes
  web.py            review, confirm, decide, Dashboard, Who knows what, step pop-ups
  flow.py           SVG drawings
  api.py, agents.py agent HTTP API and tokens (hidee serve)
  demo.py           key-free demo scenarios (hidee demo, hidee cloud-demo)
  online.py         online demo: scripted sessions, per-visitor sandboxes
  website.py        product site (hidee web)
  icons.py          inline SVG icons
  core/             privacy gateway: policy checks, one-use grants, disclosure history
  webui/templates/  Jinja2 pages
  webui/static/     CSS, JavaScript, fonts, vendored xterm.js
tests/              pytest suite
docs/               this documentation and screenshots
scripts/            build_release.sh
Dockerfile          image for hidee web --public
```

## 4. Running locally

Use a separate home folder and port while developing, so your own `~/.hidee/app` and a running app are not
touched:

```bash
.venv/bin/hidee app --home /tmp/hidee-dev --port 8771 --no-browser    # http://127.0.0.1:8771/workspace
```

Restart the app after changing Python code; templates and static files are read on each request (static URLs carry
the file's modification time, so browsers fetch the new copy).

Other entry points:

| Command | Address | What it is |
| :- | :- | :- |
| `hidee serve [--home] [--port 8765]` | `http://127.0.0.1:8765/` | agent HTTP API and dashboard; API docs at `/api/docs` |
| `hidee demo [--scenario bakery\|clinic\|recruiting\|shop] [--port 8765]` | `http://127.0.0.1:8765/` | key-free demo with fictional data |
| `hidee web [--port 8780]` | `http://localhost:8780`, demo at `http://try.localhost:8780/workspace` | product site and online demo, chosen by the `Host` header |
| `hidee cloud-demo [--port 8080]` | all interfaces | older hosted review demo (fictional data only) |
| `hidee agent add <id> [--name]` | | creates an API token for `hidee serve` |

Browsers resolve `*.localhost` to this computer, so the `try.` host works without setup. `hidee web --public`
listens on all interfaces, trusts proxy headers, and uses `HIDEE_SITE_URL` and `HIDEE_TRY_URL` (defaults
`https://hidee.net` and `https://try.hidee.net`); the port comes from `PORT` (default 8780).

Two development pages exist in every mode: `/app` (a scripted preview of the workspace) and `/styles` (a board of
visual directions).

### Agent API

`hidee serve` starts the HTTP API at `http://127.0.0.1:8765` (data in `~/.hidee/`) for
any agent you build yourself. Write rules in `~/.hidee/rules.yaml` that name an agent, a kind of action
(`search`, `remember`, `send_message`, `recommend`), a recipient, and the categories to `allow` or `forbid`. Give
the agent a token with `hidee agent add assistant --name "Shop assistant"`, then have it ask before acting:

```bash
curl -s http://127.0.0.1:8765/api/actions \
  -H "Authorization: Bearer $HIDEE_AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"kind": "send_message", "recipient": "parcel-co", "wait": 60,
       "fields":  {"message": {"value": "Leave it at the back door", "category": "delivery_instruction"},
                   "note":    {"value": "I am away on Tuesdays",     "category": "routine"}},
       "minimal": {"message": {"value": "Leave it at the back door", "category": "delivery_instruction"}}}'
```

The response `status` is `allowed`, `minimized`, `waiting`, `blocked`, `timed_out`, or `execution_failed`;
`executed` shows exactly what was approved. Actions to `agent-memory` are appended to `~/.hidee/memory.jsonl`;
other recipients use a simulated executor, or your agent acts on the `executed` content itself. Interactive API
documentation is at `http://127.0.0.1:8765/api/docs`.

### Website and online demo

`hidee web` serves the product site with its download page and docs, and the online
demo at `try.` the same domain (locally <http://localhost:8780> and <http://try.localhost:8780/workspace>). The
online demo is the real workspace and engine with a scripted session and fictional data; each visitor gets a
private sandbox that is removed after an hour, no shell runs, and nothing leaves. The download page offers the
wheel built by `scripts/build_release.sh` and the Mac app built by `scripts/build_dmg.sh` (section 5); the Docker
image serves both with `hidee web --public`.

## 5. Building a release

```bash
python scripts/release_key.py new   # once: the release signing key, in ~/.config/hidee/ (back it up; never commit)
scripts/build_release.sh            # uv build --wheel into ./release, notes from CHANGELOG, signature, SHA256SUMS
```

`hidee web` offers the newest `release/hidee-*-py3-none-any.whl` (by version number) on `/download` with its
SHA-256, and serves `/install.sh`, which checks the wheel's SHA-256 and installs it with `uv tool install` or
`pipx`, or else into its own virtual environment in `~/.hidee/venv`. It also serves `/latest.json`: the version, the
wheel's URL and SHA-256, the signature (`<wheel>.sig`), and the notes (`<wheel>.notes.json`, the bullets of that
version's CHANGELOG section). `hidee update` and the opt-in check read it, and install only a wheel whose Ed25519
signature over `hidee-release\n<version>\n<sha256>\n` verifies against `PUBLIC_KEY` in `hidee/update.py`. Bump
the version in `pyproject.toml` and `hidee/__init__.py` for each release.

The Mac app:

```bash
scripts/build_dmg.sh    # Hidee.app with PyInstaller in its own venv, then release/Hidee-<version>-macos-<arch>.dmg
```

The spec is `packaging/macos/Hidee.spec`, and the entry point is `hidee/frozen.py`. Inside the app,
`sys.executable` is the app itself, so the hook commands Hidee writes (`<executable> -m hidee.hook`) arrive at
`frozen.main`, which runs the hook; a Hidee command runs the command line, and no arguments (a double click) opens
the workspace. The app is ad-hoc signed by PyInstaller, not notarized, and runs on the architecture it was built
on. `hidee web` offers the newest `release/Hidee-*-macos-*.dmg` on `/download` with its SHA-256.

The Dockerfile installs Hidee from the source tree and runs `hidee web --public`, so a site fix ships without a
new release. It copies `./release` as built and signed (a rebuilt wheel would differ byte for byte and fail its
signature), and the download page serves those files unchanged.

## 6. Conventions

- **Plain words in the product.** Interface text, reasons, and explanations are short English sentences a non-expert
  can read ("Hidee refused this connection: …"). Code comments are in English and explain why, not what.
- **Fail closed where Hidee decides, fail open where it only watches.** The checking hook blocks on any error; the
  watching hook, the Gemini note hook, and the learn timeline never block or change a session.
- **Rules first, then a person; only approved content runs.** Every approval is a one-use grant bound to the
  digest of the content that was shown.
- **One check for every path.** Real hooks and the online demo's scripted sessions both go through
  `hook_api.check_call`; every agent is translated to Claude Code's dialect first.
- **Nothing leaves the computer.** Detection is local and pattern-based. Nothing is uploaded.
- **Hook scripts stay standard-library only** and start fast, because they run before every tool call.
- **Tests with every change.** Each module has its test file; new behavior gets a test that fails without it.
- **Commits** describe the change in one line, with detail in the body when needed.
