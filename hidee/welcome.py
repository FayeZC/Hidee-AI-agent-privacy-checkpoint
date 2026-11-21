"""What Hidee says in the terminal: a welcome when you type `hidee`, and a short status block when the app starts.

The mark is the same as the app's logo: an H on a three-by-three grid with the top-right cell in blue.
Colour is used only on a terminal, and never when NO_COLOR is set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import __version__

BLUE, BOLD, DIM, RESET = "\033[38;2;31;59;255m", "\033[1m", "\033[2m", "\033[0m"


def use_color(stream=None) -> bool:
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty() and "NO_COLOR" not in os.environ


def _c(code: str, text: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


def banner(color: bool = False) -> str:
    """The mark beside the name, the version, and one line about what Hidee is."""
    block = "██"
    rows = [block + "  " + _c(BLUE, block, color), block * 3, block + "  " + block]
    words = [f"{_c(BOLD, 'HIDEE', color)}  {__version__}",
             "A privacy checkpoint for your AI agents",
             _c(DIM, "hidee.net", color)]
    return "\n".join(f"  {mark}   {word}" for mark, word in zip(rows, words))


def menu(color: bool = False) -> str:
    """The commands a user needs, in the order they need them."""
    lines = [("hidee app", "open the workspace: your AI agent in a terminal, checked by Hidee"),
             ("hidee demo", "a key-free demonstration with a fictional business"),
             ("hidee evidence verify", "check that the record of your sessions is intact"),
             ("hidee update", "install a newer Hidee, if there is one")]
    width = max(len(cmd) for cmd, _ in lines)
    body = "\n".join(f"  {_c(BOLD, cmd.ljust(width), color)}   {what}" for cmd, what in lines)
    return f"{body}\n\n  {_c(DIM, 'Help for any command: hidee <command> --help', color)}"


def short_path(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def ready(url: str, home: Path, opening: str | bool | None, color: bool = False) -> str:
    """Shown once the workspace is running: where it is, where the data is, and how to stop. `opening` is
    "window" (Hidee's own window), "browser" (or True), or None (nothing opens)."""
    rows = [("Workspace", url),
            ("Your data", f"{short_path(home)}   (only you can read it)"),
            ("Agents", "Claude Code, Gemini CLI, or Codex CLI; the practice lesson needs none")]
    body = "\n".join(f"  {_c(DIM, label.ljust(10), color)}  {value}" for label, value in rows)
    if opening == "window":
        return f"{body}\n\n  Opening Hidee in its own window. Close the window, or press Ctrl-C here, to stop Hidee."
    tail = "Opening the workspace in your browser." if opening else "Open the address above in your browser."
    return f"{body}\n\n  {tail} Keep this window open; press Ctrl-C to stop Hidee."
