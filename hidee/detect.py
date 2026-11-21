"""Find sensitive information in text, locally, with patterns only. Nothing leaves the computer."""
from __future__ import annotations

import re
from dataclasses import dataclass

LABELS = {
    "plain": "ordinary text",
    "email": "email address",
    "phone": "phone number",
    "card_number": "card number",
    "api_key": "API key or token",
    "government_id": "government ID",
    "health": "health information",
    "money": "money or pay",
    "internal": "internal information",
    "credential_file": "credential file",
    "privileged_command": "privileged command",
    "outside_folder": "outside the session folder",
    "known_data": "known data from your sensitive files",
    # free-text facts (hidee/freetext.py)
    "mental_health": "mental health",
    "sexual_orientation": "sexual orientation or sex life",
    "religion": "religion or beliefs",
    "political": "political opinions",
    "trade_union": "trade-union membership",
    "ethnic_origin": "racial or ethnic origin",
    "genetic_biometric": "genetic or biometric data",
    "finances": "finances or debt",
    "legal": "legal trouble",
    "immigration": "immigration status",
    "family": "family or relationship matters",
    "known_fact": "restates your sensitive files",
}

# Four kinds of risk the map shows. A category can count toward more than one.
RISKS = {
    "privacy": ("email", "phone", "government_id", "health", "money", "known_data", "mental_health", "sexual_orientation", "religion", "political",
                "trade_union", "ethnic_origin", "genetic_biometric", "finances", "legal", "immigration", "family",
                "known_fact"),
    "security": ("api_key", "card_number", "credential_file", "privileged_command"),
    "internal": ("internal",),
    "overreach": ("outside_folder", "credential_file", "privileged_command"),
}


def risks_of(categories) -> list[str]:
    cats = set(categories)
    return [risk for risk, members in RISKS.items() if cats & set(members)]


@dataclass(frozen=True)
class Finding:
    category: str
    start: int
    end: int
    value: str


HEALTH_WORDS = (
    "diagnosed", "diagnosis", "diabetes", "cancer", "hiv", "aids", "depression", "anxiety", "pregnant", "pregnancy",
    "prescription", "medication", "therapy", "therapist", "psychiatrist", "chemotherapy", "asthma", "adhd", "autism",
    "dementia", "bipolar", "schizophrenia", "std", "miscarriage", "rehab", "overdose",
)
MONEY_WORDS = ("salary", "salaries", "wage", "wages", "paycheck", "payslip", "compensation", "bonus")

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("api_key", re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
        r"|xox[abp]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})"
        r"|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("government_id", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card_number", re.compile(r"\b\d(?:[ -]?\d){12,18}\b")),
    ("phone", re.compile(r"(?<![\w-])\+?\d{0,3}[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?![\w-])")),
    ("money", re.compile(r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|dollars)\b")),
    ("money", re.compile(r"\b(?:" + "|".join(MONEY_WORDS) + r")\b", re.I)),
    ("health", re.compile(r"\b(?:" + "|".join(HEALTH_WORDS) + r")\b", re.I)),
    # Internal information: confidentiality markers, private network addresses and hosts, local file paths.
    ("internal", re.compile(r"\b(?:confidential|internal[ -]only|do not (?:distribute|share)|proprietary|"
                            r"under NDA|trade secret)\b|机密|内部资料|保密", re.I)),
    ("internal", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("internal", re.compile(r"\b[\w.-]+\.(?:internal|corp|intranet|lan)\b", re.I)),
    ("internal", re.compile(r"(?:/Users|/home)/[\w.-]+/[^\s'\"]+|[A-Z]:\\\\Users\\\\[^\s'\"]+")),
]


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _valid(category: str, value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if category == "card_number":
        return _luhn(digits)
    if category == "phone":
        return 10 <= len(digits) <= 15
    return True


def find(text: str) -> list[Finding]:
    """All findings in reading order. When two overlap, the longer one wins."""
    candidates = []
    for cat, pattern in PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group()
            start = m.start() + len(raw) - len(raw.lstrip())
            end = m.end() - len(raw) + len(raw.rstrip())
            if _valid(cat, raw) and end > start:
                candidates.append(Finding(cat, start, end, text[start:end]))
    candidates.sort(key=lambda f: (-(f.end - f.start), f.start))
    kept: list[Finding] = []
    for f in candidates:
        if all(f.end <= k.start or f.start >= k.end for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda f: f.start)


def redact(text: str, findings: list[Finding] | None = None, style: str = "bracket") -> str:
    """Replace each finding with [category], or with REDACTED_CATEGORY where brackets would break a URL or a
    shell command (curl reads brackets as a pattern)."""
    out, last = [], 0
    for f in findings if findings is not None else find(text):
        mark = f"REDACTED_{f.category.upper()}" if style == "token" else f"[{f.category}]"
        out += [text[last:f.start], mark]
        last = f.end
    return "".join(out) + text[last:]
