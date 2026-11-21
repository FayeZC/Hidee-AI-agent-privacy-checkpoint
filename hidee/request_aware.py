"""Which highlighted details the user's own request names, so the shorter version keeps them.

The shorter version removes every highlighted detail except destination fields and the details the user's request
names or clearly asks to include. Only the user's own request counts: the prompts the person typed (the
UserPromptSubmit hook, or the prompt Hidee launched a headless session with). Tool results, fetched pages, files,
and the model's own text never count, so text an injected page writes cannot make a detail "requested".

A detail is named by the request when
  1. its value appears in the request (case and spacing ignored; for numbers, the digits match), or
  2. the request asks to include its kind ("include the patient's name and date of birth", "with their phone
     number"): a word for the kind in the same sentence as an inclusion word (include, with, add, along with, plus,
     list, give, send, share, tell, mention, containing), and not negated ("without", "don't include", "leave out",
     "except", "not", "no"). API keys and other credentials are never kept by kind, only by value.
For details found in the user's sensitive files (category known_data), the kind is read from the value's shape:
a date, a person's name, an email address, a phone or ID number, a street address, or an amount.
"""
from __future__ import annotations

import re

INCLUDE = r"(?:include|including|includes|with|add|adding|along\s+with|plus|list|listing|give|send|share|tell|" \
          r"mention|containing|contains|attach|put|copy|and)"
NEGATE = re.compile(r"(?i)\b(?:without|don'?t|do\s+not|never|leave\s+out|leaving\s+out|omit|omitting|except|"
                    r"excluding|exclude|no|not|minus|but\s+not|remove)\b[^.;!?]{0,40}$")
POSS = r"(?:their|his|her|its|your|my|our|the|each|every|all|[a-z]+'s|[a-z]+s')"
KIND_WORDS = {
    "email": rf"\be-?mail\s+address(?:es)?\b|\b{POSS}\s+(?:[a-z]+\s+)?e-?mails?\b(?!\s+(?:to|them|him|her|it)\b)",
    "phone": r"\b(?:tele)?phone(?:\s+numbers?)?\b|\bmobile(?:\s+numbers?)?\b|\bcell(?:\s+numbers?)?\b|"
             r"\bcontact\s+numbers?\b|\bnumbers?\s+to\s+call\b",
    "name": rf"\b(?:full|first|last|family)\s+names?\b|\b{POSS}\s+names?\b|\bnames\b",
    "dob": r"\bdates?\s+of\s+birth\b|\bbirth\s*dates?\b|\bbirthdays?\b|\bd\.?o\.?b\.?\b",
    "date": rf"\b{POSS}\s+(?:[a-z]+\s+)?dates?\b",
    "address": r"\b(?:street|home|mailing|postal|delivery)\s+address(?:es)?\b|(?<!mail\s)(?<!mail)\baddress(?:es)?\b",
    "government_id": r"\bssns?\b|\bsocial\s+security(?:\s+numbers?)?\b|\btax\s+ids?\b|\btins?\b|\bmember\s+ids?\b|"
                     r"\binsurance\s+(?:ids?|numbers?)\b|\bid\s+numbers?\b|\bnational\s+ids?\b|\bpassport(?:\s+numbers?)?\b",
    "card_number": r"\bcard\s+numbers?\b|\bcredit\s+cards?\b",
    "account": r"\baccount\s+numbers?\b|\bbank\s+(?:details|accounts?)\b|\biban\b|\brouting\s+numbers?\b",
    "money": r"\bamounts?\b|\bbalances?\b|\btotals?\b|\bprices?\b|\bcosts?\b|\bfees?\b|\brates?\b|\bpay\b|"
             r"\bsalar(?:y|ies)\b|\bwages?\b",
    "mental_health": r"\bmental\s+health\b|\bdiagnos(?:is|es)\b|\bconditions?\b|\btherapy\b|\btreatment\b",
    "sexual_orientation": r"\bsexual\s+orientation\b|\bsexuality\b|\bgender\s+identity\b",
    "religion": r"\breligio(?:n|us)\b|\bfaith\b|\bbeliefs?\b",
    "political": r"\bpolitic(?:s|al)\b|\bpolitical\s+views?\b",
    "trade_union": r"\bunion\b",
    "ethnic_origin": r"\bethnicity\b|\brace\b|\bethnic\b|\bbackground\b",
    "genetic_biometric": r"\bgenetic\b|\bdna\b|\bbiometric\b",
    "finances": r"\bfinances?\b|\bfinancial\s+(?:situation|details)\b|\bdebts?\b",
    "legal": r"\blegal\b|\bcase\b|\brecord\b|\bcourt\b|\bcharges?\b",
    "immigration": r"\bimmigration\b|\bvisa\b",
    "family": r"\bfamily\s+(?:situation|matters?)\b|\bdivorce\b|\bpregnan(?:cy|t)\b|\bcustody\b",
    "health": r"\bdiagnos(?:is|es)\b|\bconditions?\b|\bmedical\b|\bhealth\b|\bsymptoms?\b|\bmedications?\b|"
              r"\ballerg(?:y|ies)\b|\breason\s+for\s+(?:the\s+)?(?:referral|visit)\b|\btreatments?\b",
}
# Hidee's categories and the kinds a request can name for them.
CATEGORY_KINDS = {"email": ("email",), "phone": ("phone",), "government_id": ("government_id",),
                  "card_number": ("card_number",), "money": ("money", "account"), "health": ("health",),
                  "internal": (), "api_key": (),
                  "mental_health": ("mental_health", "health"), "sexual_orientation": ("sexual_orientation",),
                  "religion": ("religion",), "political": ("political",), "trade_union": ("trade_union",),
                  "ethnic_origin": ("ethnic_origin",), "genetic_biometric": ("genetic_biometric",),
                  "finances": ("finances", "money"), "legal": ("legal",), "immigration": ("immigration",),
                  "family": ("family",), "known_fact": ()}
DATE = re.compile(r"^\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
                  r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\s*$", re.I)
NAME = re.compile(r"^[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){1,3}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
PHONEISH = re.compile(r"^[\d\s().+/-]{6,}$")
STREET = re.compile(r"^\d+\s+\w+.*\b(?:st|street|ave|avenue|rd|road|lane|ln|blvd|drive|dr|way|court|ct)\b", re.I)
MONEY = re.compile(r"^\s*[$€£]\s?\d")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def kinds_of(category: str, value: str) -> tuple[str, ...]:
    """The kinds a request could name for this detail."""
    if category != "known_data":
        return CATEGORY_KINDS.get(category, ())
    v = value.strip()
    if DATE.match(v):
        return ("dob", "date")
    if EMAIL.match(v):
        return ("email",)
    if MONEY.match(v):
        return ("money",)
    if STREET.match(v):
        return ("address",)
    if PHONEISH.match(v) and sum(c.isdigit() for c in v) >= 6:
        return ("phone", "government_id", "account")
    if NAME.match(v):
        return ("name",)
    return ()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _date_forms(v: str) -> list[str]:
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$", v)
    if not m:
        return []
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not 1 <= mo <= 12:
        return []
    month = MONTHS[mo - 1]
    return [f"{month} {d}, {y}", f"{month} {d} {y}", f"{d} {month} {y}", f"{month[:3]} {d}, {y}", f"{mo}/{d}/{y}",
            f"{mo:02d}/{d:02d}/{y}"]


def value_named(value: str, request: str) -> bool:
    v = " ".join(value.lower().split())
    r = " ".join(request.lower().split())
    if not v or len(v) < 3:
        return False
    if re.search(r"(?<![\w@.])" + re.escape(v) + r"(?![\w@])", r):
        return True
    dv = _digits(value)
    if len(dv) >= 6 and len(dv) >= 0.6 * len(value.replace(" ", "")):
        for token in re.findall(r"[\d][\d\s().+/-]{4,}[\d]", request):
            if _digits(token) == dv:
                return True
    return any(f in r for f in _date_forms(value))


def kind_named(kind: str, request: str) -> bool:
    pattern = KIND_WORDS.get(kind)
    if not pattern:
        return False
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", request):
        low = sentence.lower()
        for m in re.finditer(pattern, low):
            before = low[:m.start()]
            if NEGATE.search(before):
                continue
            if re.search(rf"\b{INCLUDE}\b", before) or re.search(rf"\b{INCLUDE}\b", low[m.end():m.end() + 30]):
                return True
    return False


def named_by_request(category: str, value: str, request: str) -> bool:
    """True when the user's own request names this detail (by value, or by asking to include its kind)."""
    if not request:
        return False
    if value_named(value, request):
        return True
    if category in ("api_key", "credential_file", "privileged_command", "outside_folder"):
        return False
    return any(kind_named(k, request) for k in kinds_of(category, value))
