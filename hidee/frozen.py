"""The entry point of the Hidee app bundle (Hidee.app, built by scripts/build_dmg.sh).

In the bundle, `sys.executable` is the app itself, not a Python interpreter, so the hook commands Hidee writes for
each session (`<executable> -m hidee.hook`, see hidee/sessions.py) arrive here. This entry point answers three ways:

- `-m hidee.hook` or `-m hidee.learn_hook`: run that hook, exactly as `python -m` would (the agent is waiting);
- a Hidee command (`app`, `update`, `evidence`, `demo`, ...): the command line, as the `hidee` command;
- nothing (a double click in Finder): the workspace in Hidee's own window. If Hidee is already running, another
  window onto it opens instead of a second Hidee.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HOOKS = ("hidee.hook", "hidee.learn_hook")


def run_module(module: str, rest: list[str]) -> int:
    if module == "hidee.hook":
        from .hook import main
        return main(after="--after" in rest)
    if module == "hidee.learn_hook":
        from .learn_hook import main
        return main()
    print(f"Hidee cannot run module {module}", file=sys.stderr)
    return 2


def hidee_running(port: int) -> bool:
    """True if a Hidee workspace already answers on 127.0.0.1:port."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/workspace", timeout=2) as response:
            return response.status == 200 and b"HIDEE" in response.read(4096).upper()
    except OSError:
        return False


def alert(message: str) -> None:
    """A message box, since a double-clicked app has no terminal to print to."""
    import subprocess
    script = f'display alert "Hidee" message {json_quote(message)} as warning'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def json_quote(text: str) -> str:
    import json
    return json.dumps(text)                     # a double-quoted string AppleScript accepts


def open_app() -> int:
    """A double click: the workspace in its own window. HIDEE_APP_HOME and HIDEE_APP_PORT are for testing."""
    from .cli import DEFAULT_HOME, port_free, run_app
    home = Path(os.environ.get("HIDEE_APP_HOME") or DEFAULT_HOME / "app")
    port = int(os.environ.get("HIDEE_APP_PORT") or 8770)
    if not port_free(port):
        if hidee_running(port):
            from . import desktop
            desktop.open_window(f"http://127.0.0.1:{port}/workspace")
            return 0
        alert(f"Port {port} is in use by another program, so Hidee cannot start.")
        return 1
    try:
        run_app(home, port, True)
    except SystemExit as e:
        if e.code not in (None, 0):
            alert(str(e.code).strip())
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [a for a in argv if not a.startswith("-psn_")]     # older macOS adds a process serial number
    if len(argv) >= 2 and argv[0] == "-m":
        return run_module(argv[1], argv[2:])
    if argv:
        from .cli import main as cli_main
        cli_main(argv)
        return 0
    return open_app()


if __name__ == "__main__":
    sys.exit(main())
