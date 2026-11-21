"""Known data: values from files the user marks as sensitive (a customer list, patient records), found again in an
outbound call even when no pattern detector would recognize them (names, account numbers, dates, record ids).

Off unless the user designates files. A project designates them in `.hidee/sensitive` (one path or glob per line,
relative to the project folder; `#` starts a comment). Everything stays in memory in the Hidee process: the values are never written to the evidence log, which
records only the category `known_data`.

Which values count: every field of a CSV/TSV file (header row skipped), every string in a JSON file, and in text or
Markdown files the value after "Key:" on a line, table cells, and list items. A value counts when, after
normalization, it has at least 5 characters and looks like personal data rather than ordinary words: it has at
least 4 digits (an id, an account, a date, a phone number), or it is two or more capitalized words (a name), or it
is an id-like token (letters and digits joined by '-' or '_', such as HN-7KQ2X or ACCT-1234). Matching ignores case
and tolerates different separators (spaces, dots, dashes, underscores) between the value's parts.
"""
from __future__ import annotations

import csv
import fnmatch
import io
import json
import re
from pathlib import Path

from .detect import Finding

CATEGORY = "known_data"
MAX_FILE = 2 * 1024 * 1024
MAX_VALUES = 20000
PARTS = re.compile(r"[A-Za-z0-9]+")
NAME = re.compile(r"^(?:[A-Z][a-zA-Z'’-]+\.?\s+){1,3}[A-Z][a-zA-Z'’-]+$")
ID_LIKE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+$")
KEY_VALUE = re.compile(r"^\s*[-*]?\s*[\w ./()'’-]{1,40}:\s*(.+?)\s*$")
CHART_REF = re.compile(r"\b[A-Z]{2,}-[A-Z0-9]{3,}\b")


def personal(value: str) -> bool:
    v = value.strip()
    parts = PARTS.findall(v)
    if sum(len(p) for p in parts) < 5:
        return False
    return (sum(ch.isdigit() for ch in v) >= 4 or bool(NAME.match(v)) or bool(ID_LIKE.match(v)))


def _values_from_text(text: str, suffix: str) -> list[str]:
    out: list[str] = []
    if suffix in (".csv", ".tsv"):
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ","))
        for row in rows[1:]:
            out += [c for c in row]
    elif suffix == ".json":
        def walk(x):
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        try:
            walk(json.loads(text))
        except ValueError:
            pass
    else:
        for line in text.splitlines():
            if "|" in line:
                out += [c.strip() for c in line.split("|")]
            m = KEY_VALUE.match(line)
            if m:
                out.append(m.group(1))
                out += [p.strip() for p in re.split(r"[,;()]", m.group(1))]
            elif line.strip().startswith(("-", "*")):
                out.append(line.strip().lstrip("-* "))
    # Ids inside longer cells (a chart note with its reference) count on their own too.
    for v in list(out):
        out += CHART_REF.findall(v)
    return out


def _passages(text: str, suffix: str) -> list[str]:
    """Passages a restatement is compared with: each CSV row, each JSON string, each sentence or line of text."""
    if suffix in (".csv", ".tsv"):
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ","))
        return [" ".join(r) for r in rows[1:]]
    if suffix == ".json":
        out: list[str] = []

        def walk(x):
            if isinstance(x, str):
                out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", x) if s.strip())
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        try:
            walk(json.loads(text))
        except ValueError:
            pass
        return out
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def designated(project: Path) -> list[Path]:
    """Files a project marks as sensitive in .hidee/sensitive."""
    spec = Path(project) / ".hidee" / "sensitive"
    if not spec.is_file():
        return []
    paths: list[Path] = []
    for line in spec.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        paths += sorted(Path(project).glob(line)) if any(c in line for c in "*?[") else [Path(project) / line]
    return paths


class KnownData:
    def __init__(self, values: list[str], passages: list[str] | None = None) -> None:
        seen, self.patterns = set(), []
        # every value of the files (not only personal-looking ones), so a request that names a value inside a
        # restated sentence can be recognized (see named_inside)
        self.cells = sorted({c for c in (" ".join(str(v).split()) for v in values)
                             if sum(len(p) for p in PARTS.findall(c)) >= 5})[:MAX_VALUES]
        # people named in the files (for attributing a free-text fact to a known contact) and the files' passages
        # (for finding a sentence that restates them); both stay in memory, like the values
        self.names = sorted({" ".join(str(v).split()) for v in values if NAME.match(" ".join(str(v).split()))})[:MAX_VALUES]
        from .freetext import RestateIndex
        self.restate = RestateIndex(passages or []) if passages else None
        for v in values:
            v = " ".join(str(v).split())
            parts = PARTS.findall(v)
            key = "".join(parts).lower()
            if not personal(v) or key in seen:
                continue
            seen.add(key)
            # The value's parts in order, any of these separators between them: spaces, dots, dashes, underscores,
            # slashes, commas; word boundaries at both ends.
            body = r"[\s._\-/,]*".join(re.escape(p) for p in parts)
            self.patterns.append(re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I))
            if len(self.patterns) >= MAX_VALUES:
                break

    def __len__(self) -> int:
        return len(self.patterns)

    @classmethod
    def from_paths(cls, paths, root: Path | None = None) -> "KnownData":
        values: list[str] = []
        passages: list[str] = []
        files: list[Path] = []
        for p in paths:
            p = Path(p) if Path(p).is_absolute() or root is None else Path(root) / p
            if p.is_dir():
                files += [f for f in sorted(p.rglob("*")) if f.is_file()]
            elif p.is_file():
                files.append(p)
        for f in files:
            if f.stat().st_size > MAX_FILE or fnmatch.fnmatch(f.name, ".*"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            values += _values_from_text(text, f.suffix.lower())
            passages += _passages(text, f.suffix.lower())
        return cls(values, passages)

    def named_inside(self, sentence: str, request: str) -> bool:
        """True when the user's request names a value of the files that is inside `sentence` (a restated sentence),
        by value or by asking to include its kind (hidee.request_aware)."""
        if not request:
            return False
        from .request_aware import kind_named, kinds_of, value_named
        low = " ".join(sentence.lower().split())
        for cell in self.cells:
            if cell.lower() in low and (value_named(cell, request) or
                                        any(kind_named(k, request) for k in kinds_of(CATEGORY, cell))):
                return True
        return False

    def find(self, text: str) -> list[Finding]:
        out = []
        for pattern in self.patterns:
            for m in pattern.finditer(text):
                out.append(Finding(CATEGORY, m.start(), m.end(), m.group()))
        return out


def merge(findings: list[Finding], extra: list[Finding]) -> list[Finding]:
    """Pattern findings plus known-data findings; where two overlap, the longer wins (as in detect.find)."""
    candidates = sorted(findings + extra, key=lambda f: (-(f.end - f.start), f.start))
    kept: list[Finding] = []
    for f in candidates:
        if all(f.end <= k.start or f.start >= k.end for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda f: f.start)
