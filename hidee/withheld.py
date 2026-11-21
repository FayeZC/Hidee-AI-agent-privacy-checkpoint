"""Details the student has held back in a session: removed from a shorter version, or in a request they rejected.

An agent told "no" sometimes tries again in disguise: the address split in two, "at" for "@", letters spaced out.
Hidee compares later calls of the same session against what was held back, after undoing those disguises, and
stops a match without asking again.
"""
from __future__ import annotations

import re
import threading

DISGUISES = [(re.compile(r"\s*(?:\[at\]|\(at\)|\{at\}|\bat\b)\s*", re.I), "@"),
             (re.compile(r"\s*(?:\[dot\]|\(dot\)|\{dot\}|\bdot\b)\s*", re.I), ".")]
NOISE = re.compile(r"[\s\"'`\\]+")


def normalize(text: str) -> str:
    for pattern, sub in DISGUISES:
        text = pattern.sub(sub, text)
    return NOISE.sub("", text).lower()


def _needles(value: str, category: str) -> list[tuple[str, str]]:
    """(kind, needle) pairs that count as the held-back value showing up again."""
    if category == "phone":
        digits = re.sub(r"\D", "", value)
        return [("digits", digits)] if len(digits) >= 7 else []
    clean = normalize(value)
    needles = [("text", clean)]
    if category == "email" and "@" in clean:
        local = clean.split("@", 1)[0]
        if len(local) >= 6:                       # the person's part of the address, not the domain
            needles.append(("text", local))
    return needles


class Withheld:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[str, list[tuple[str, str]]] = {}

    def add(self, session: str, values) -> None:
        with self.lock:
            known = self.values.setdefault(session, [])
            for value, category in values:
                if value and (value, category) not in known:
                    known.append((value, category))

    def match(self, session: str, text: str) -> tuple[str, str] | None:
        with self.lock:
            known = list(self.values.get(session, ()))
        if not known:
            return None
        flat, digits = normalize(text), re.sub(r"\D", "", text)
        for value, category in known:
            for kind, needle in _needles(value, category):
                if needle and needle in (digits if kind == "digits" else flat):
                    return value, category
        return None
