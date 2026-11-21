"""Hidee's own data: kept on the user's computer, readable only by them.

Hidee has no account and no telemetry, checks for updates only if the user said yes, and sends nothing else to its
makers. What it keeps
lives under its home folder (~/.hidee/app by default): the checkpoint database, the rules, the evidence log, and
one folder per session. This module locks that folder to the user (folders 0700, files 0600) and lists what is
there, so the Settings page can show it.
"""
from __future__ import annotations

import os
from pathlib import Path

PARTS = [("Checkpoint database", "hidee.db", "Each outbound step Hidee checked, and your decisions"),
         ("Evidence log", "evidence", "The hash-chained record of every session"),
         ("Sessions", "sessions", "Per-session settings and names; never your files"),
         ("Rules", "rules.yaml", "What may go where, in plain YAML")]


def make_private(home: Path) -> None:
    """Folders 0700 and files 0600 under the home folder, and the folder above it if it is Hidee's own."""
    home = Path(home)
    roots = [home] + ([home.parent] if home.parent.name == ".hidee" else [])
    for root in roots:
        if root.exists():
            os.chmod(root, 0o700)
    for folder, dirs, files in os.walk(home):
        for d in dirs:
            os.chmod(os.path.join(folder, d), 0o700)
        for f in files:
            path = os.path.join(folder, f)
            if not os.path.islink(path):
                os.chmod(path, 0o600)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def data_inventory(home: Path) -> list[dict]:
    home = Path(home)
    return [{"what": what, "path": str(home / name), "bytes": _size(home / name), "about": about}
            for what, name, about in PARTS]
