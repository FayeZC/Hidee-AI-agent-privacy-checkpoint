"""Command line: `hidee app`, `hidee update`, `hidee web`, `hidee serve`, `hidee demo`, `hidee agent add`."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

DEFAULT_HOME = Path.home() / ".hidee"
STARTER_RULES = """\
# Hidee rules. Every agent action is checked against this file.
# An action is allowed only if a rule matches its agent, kind, and recipient and
# every category it contains is in `allow`. Categories in `forbid` are always blocked.
# Anything else waits for you on the dashboard. With no rules, everything is blocked.
#
# Example:
# agents:
#   assistant: Shop assistant
# recipients:
#   parcel-co: Parcel Co.
# categories:
#   delivery_instruction: delivery instructions
#   routine: someone's daily routine
# rules:
#   - agent: assistant
#     kind: send_message        # search | remember | send_message | recommend
#     recipient: parcel-co
#     allow: [delivery_instruction]
#     forbid: [routine]
agents: {}
recipients: {}
categories: {}
rules: []
"""


def init_home(home: Path, rules_text: str = STARTER_RULES) -> Path:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    rules = home / "rules.yaml"
    if not rules.exists():
        rules.write_text(rules_text, encoding="utf-8")
    return home


def run_app(home: Path, port: int, open_browser: bool, browser: bool = False, messages: str = "ask",
            freetext: bool = True) -> None:
    """The local workspace: your AI agents in real terminals, checked by Hidee. Listens on 127.0.0.1 only.
    It opens in Hidee's own window when it can (see hidee/desktop.py), or in the browser with `browser`.
    `messages` sets how messages to people go out (ask, draft, off); `freetext` turns free-text detection on or off."""
    import shutil
    import threading
    import webbrowser

    import uvicorn

    from .app import create_app
    from .hook_api import APP_RULES
    from .welcome import banner, ready, use_color
    color = use_color()
    print(f"\n{banner(color)}\n", flush=True)
    home = init_home(home, APP_RULES)
    if updates_before_start(home):
        return                                   # updated: the new version starts on the next `hidee app`
    app = create_app(home, app_mode=True, app_url=f"http://127.0.0.1:{port}", claude=shutil.which("claude") or "claude",
                     gemini=shutil.which("gemini") or "gemini", codex=shutil.which("codex") or "codex",
                     message_policy=messages, freetext=freetext)
    url = f"http://127.0.0.1:{port}/workspace"
    if not port_free(port):
        raise SystemExit(f"  Port {port} is in use: Hidee may already be running (open {url}),\n"
                         f"  or another program has it. Start on another port with: hidee app --port {port + 1}")
    from . import desktop
    window = open_browser and not browser and desktop.available()
    print(f"{ready(url, home, 'window' if window else ('browser' if open_browser else None), color)}\n", flush=True)
    if window:
        # The window needs the main thread (macOS requires it); the server runs beside it, and closing the
        # window quits Hidee.
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started and thread.is_alive():
            time.sleep(0.05)
        if not thread.is_alive():
            app.state.sessions.close_all()
            raise SystemExit(f"  Hidee could not start on port {port}. Try another: hidee app --port {port + 1}")
        try:
            desktop.open_window(url)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            app.state.sessions.close_all()
        return
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        app.state.sessions.close_all()


def port_free(port: int) -> bool:
    """True if Hidee can listen on 127.0.0.1:port (checked before starting, so a busy port is explained)."""
    import socket
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)    # as uvicorn does: a closed port is free
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def updates_before_start(home: Path, interactive: bool | None = None) -> bool:
    """In a terminal: ask once whether Hidee may check for updates, and, if the user said yes and two weeks have
    passed, check and offer the update. Without a terminal nothing is asked and nothing is checked here."""
    import sys

    from .update import Updates, first_run, offer
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        return False
    import textwrap
    updates = Updates(home)
    ask = lambda question: input("  " + question)                          # noqa: E731  (lined up with the banner)
    say = lambda message: print(textwrap.indent(message, "  "))             # noqa: E731
    asked = updates.consent is None
    first_run(updates, ask, say)
    installed = offer(updates, ask, say)
    if asked or installed:
        print()
    return installed


def welcome(ask, interactive: bool | None = None) -> None:
    """`hidee` on its own: the mark, the commands, and (in a terminal) an offer to open the workspace now."""
    import sys

    from .welcome import banner, menu, use_color
    color = use_color()
    print(f"\n{banner(color)}\n\n{menu(color)}\n", flush=True)
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive and ask("  Open the workspace now? [Y/n] ").strip().lower() not in ("n", "no"):
        run_app(DEFAULT_HOME / "app", 8770, True)


def _is_day(text: str) -> bool:
    import datetime
    try:
        return datetime.date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hidee", description="A local privacy checkpoint for AI agents.")
    # Users see only the product: the workspace, updates, the evidence log, and the key-free demo. The commands that
    # host hidee.net (web, cloud-demo) and the older agent HTTP API (serve, agent) still work but are not listed.
    sub = parser.add_subparsers(dest="command", metavar="{app,update,evidence,demo}")

    app = sub.add_parser("app", help="Open the workspace: your AI agents in real terminals, checked by Hidee")
    app.add_argument("--home", default=str(DEFAULT_HOME / "app"))
    app.add_argument("--port", type=int, default=8770)
    app.add_argument("--no-browser", action="store_true", help="Open nothing; just serve the workspace")
    app.add_argument("--browser", action="store_true", help="Open the workspace in your browser, not its own window")
    app.add_argument("--messages", choices=["ask", "draft", "off"], default="ask",
                     help="Messages to people (chat, email, posts): ask how each goes out (default), always save "
                          "them as drafts for you, or let the rules decide (off)")
    app.add_argument("--no-freetext", action="store_true",
                     help="Turn off free-text detection (sensitive facts in plain sentences and restatements of your "
                          "sensitive files); patterns and known data stay on")

    upd = sub.add_parser("update", help="Check for a newer Hidee now and install it if you say yes")
    upd.add_argument("--home", default=str(DEFAULT_HOME / "app"))
    upd.add_argument("--yes", action="store_true", help="Install without asking")
    serve = sub.add_parser("serve", description="Run the Hidee service and dashboard (agent HTTP API)")
    serve.add_argument("--home", default=str(DEFAULT_HOME))
    serve.add_argument("--port", type=int, default=8765)

    demo = sub.add_parser("demo", help="Run a key-free demonstration in your browser")
    demo.add_argument("--port", type=int, default=8765)
    demo.add_argument("--interval", type=float, default=3.0, help="Seconds between demo requests")
    demo.add_argument("--no-browser", action="store_true")
    demo.add_argument("--scenario", default="bakery", choices=["bakery", "clinic", "recruiting", "shop"],
                      help="Which fictional business to replay")

    web = sub.add_parser("web", description="Serve hidee.net: the product site, downloads, and the online demo")
    web.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8780")))
    web.add_argument("--public", action="store_true", help="Listen on all interfaces (for hosting)")
    web.add_argument("--release", default=os.environ.get("HIDEE_RELEASE_DIR", "release"),
                     help="Folder with the wheel to offer for download")

    cloud = sub.add_parser("cloud-demo", description="Run the public demo (fictional data only) for hosting")
    cloud.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))

    ev = sub.add_parser("evidence", help="Check or export the evidence log")
    ev_sub = ev.add_subparsers(dest="evidence_command", required=True)
    ev_verify = ev_sub.add_parser("verify", help="Check the log's hash chain, or an exported bundle")
    ev_verify.add_argument("bundle", nargs="?", help="An exported .zip; without it, the log in --home")
    ev_verify.add_argument("--home", default=str(DEFAULT_HOME / "app"))
    ev_export = ev_sub.add_parser("export", help="Write the log (or some days of it) to a zip with a manifest")
    ev_export.add_argument("--home", default=str(DEFAULT_HOME / "app"))
    ev_export.add_argument("--from", dest="day_from", help="First day, YYYY-MM-DD")
    ev_export.add_argument("--to", dest="day_to", help="Last day, YYYY-MM-DD")
    ev_export.add_argument("--out", default="hidee-evidence.zip")
    ev_rec = ev_sub.add_parser("reconcile", help="Totals per day to compare with the provider's and destinations' logs")
    ev_rec.add_argument("--home", default=str(DEFAULT_HOME / "app"))
    ev_rec.add_argument("--from", dest="day_from", help="First day, YYYY-MM-DD (UTC)")
    ev_rec.add_argument("--to", dest="day_to", help="Last day, YYYY-MM-DD (UTC)")
    ev_rec.add_argument("--format", choices=["md", "csv"], default="md")
    ev_time = ev_sub.add_parser("timeline", help="Write a forensic timeline: CSV, one row per record, plus a manifest")
    ev_time.add_argument("--home", default=str(DEFAULT_HOME / "app"))
    ev_time.add_argument("--from", dest="day_from", help="First day, YYYY-MM-DD (UTC)")
    ev_time.add_argument("--to", dest="day_to", help="Last day, YYYY-MM-DD (UTC)")
    ev_time.add_argument("--out", default="hidee-timeline.csv",
                         help="CSV file; the manifest is written next to it as <out>.manifest.json")

    agent = sub.add_parser("agent", description="Manage agent tokens for hidee serve")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    add = agent_sub.add_parser("add", help="Create (or replace) a token for an agent and print it")
    add.add_argument("agent_id", help="The id used for this agent in rules.yaml")
    add.add_argument("--name", help="A readable name shown on the dashboard")
    add.add_argument("--home", default=str(DEFAULT_HOME))

    args = parser.parse_args(argv)
    if args.command == "evidence":
        from .evidence import export, verify, verify_bundle
        folder = Path(args.home).expanduser() / "evidence"
        for day in (getattr(args, "day_from", None), getattr(args, "day_to", None)):
            if day and not _is_day(day):
                parser.error(f"{day} is not a day in the form YYYY-MM-DD")
        if args.evidence_command == "reconcile":
            import sys
            from .reconcile import chain_line, reconcile, report_csv, report_markdown
            rep = reconcile(folder, args.day_from, args.day_to)
            if args.format == "csv":
                sys.stdout.write(report_csv(rep))
                print(chain_line(rep["chain"]), file=sys.stderr)
            else:
                sys.stdout.write(report_markdown(rep))
            return
        if args.evidence_command == "timeline":
            from .reconcile import chain_line, chain_status, timeline_files
            out = Path(args.out)
            files = timeline_files(folder, args.day_from, args.day_to, name=out.name)
            out.write_bytes(files[out.name])
            side = out.with_name(out.name + ".manifest.json")
            side.write_bytes(files["manifest.json"])
            print(f"Wrote {out} and {side}")
            print(chain_line(chain_status(folder)))
            return
        if args.evidence_command == "export":
            out = export(folder, Path(args.out), args.day_from, args.day_to)
            print(f"Wrote {out}")
        elif args.bundle:
            ok, _, _, why = verify_bundle(Path(args.bundle))
            print(why)
            if not ok:
                raise SystemExit(1)
        else:
            ok, count, bad = verify(folder)
            print(f"Chain intact: {count} records." if ok else f"The chain breaks at record {bad}.")
            if not ok:
                raise SystemExit(1)
        return
    if args.command is None:
        welcome(input)
        return
    if args.command == "app":
        run_app(Path(args.home).expanduser(), args.port, not args.no_browser, browser=args.browser,
                messages=args.messages, freetext=not args.no_freetext)
    elif args.command == "update":
        from .update import Updates, offer
        home = Path(args.home).expanduser()
        if not offer(Updates(home), input, print, force=True, assume_yes=args.yes):
            raise SystemExit(0)
    elif args.command == "serve":
        import uvicorn

        from .app import create_app
        home = init_home(Path(args.home).expanduser())
        print(f"Hidee is running at http://127.0.0.1:{args.port}/  (rules: {home / 'rules.yaml'})", flush=True)
        uvicorn.run(create_app(home), host="127.0.0.1", port=args.port, log_level="warning")
    elif args.command == "demo":
        from . import demo as demo_module
        demo_module.main(port=args.port, interval=args.interval, open_browser=not args.no_browser,
                         name=args.scenario)
    elif args.command == "web":
        import uvicorn

        from .website import create_web_app
        if args.public:
            site = os.environ.get("HIDEE_SITE_URL", "https://hidee.net")
            try_url = os.environ.get("HIDEE_TRY_URL", "https://try.hidee.net")
        else:   # browsers resolve *.localhost to this computer, so both hosts work without setup
            site, try_url = f"http://localhost:{args.port}", f"http://try.localhost:{args.port}"
        app, _ = create_web_app(site, try_url, Path(args.release))
        print(f"Hidee site: {site}   online demo: {try_url}/workspace", flush=True)
        uvicorn.run(app, host="0.0.0.0" if args.public else "127.0.0.1", port=args.port, log_level="warning",
                    proxy_headers=args.public, timeout_keep_alive=620)
    elif args.command == "cloud-demo":
        import uvicorn

        from .demo import create_demo_app
        # The only mode that listens on all interfaces: it holds fictional data and no agent tokens.
        print(f"Hidee public demo (fictional data) listening on port {args.port}", flush=True)
        # Keep idle connections open longer than Cloud Run's front end (600 s); otherwise uvicorn's 5 s default
        # closes connections the front end is about to reuse, and visitors see sporadic 500 errors.
        uvicorn.run(create_demo_app(), host="0.0.0.0", port=args.port, log_level="warning", proxy_headers=True,
                    timeout_keep_alive=620)
    else:
        from .agents import add_agent
        from .store import Store
        home = init_home(Path(args.home).expanduser())
        token = add_agent(Store(home / "hidee.db"), args.agent_id, args.name or args.agent_id, time.time())
        print(f"Token for {args.name or args.agent_id} (shown once; store it in the agent's environment):")
        print(token)
