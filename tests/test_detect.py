import pytest

from hidee.detect import LABELS, find, redact


def cats(text):
    return [(f.category, f.value) for f in find(text)]


@pytest.mark.parametrize("text, expected", [
    ("write to maria.lopez@example.org today", [("email", "maria.lopez@example.org")]),
    ("call +1 (917) 555-0142 after 5", [("phone", "+1 (917) 555-0142")]),
    ("card 4111 1111 1111 1111 exp 09/28", [("card_number", "4111 1111 1111 1111")]),
    ("key sk-ant-api03-abcdefghijklmnopqrstuv", [("api_key", "sk-ant-api03-abcdefghijklmnopqrstuv")]),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", [("api_key", "ghp_abcdefghijklmnopqrstuvwxyz0123456789")]),
    ("AWS AKIAIOSFODNN7EXAMPLE here", [("api_key", "AKIAIOSFODNN7EXAMPLE")]),
    ("-H 'Authorization: Bearer abcDEF123456789xyzLONG'", [("api_key", "Bearer abcDEF123456789xyzLONG")]),
    ("SSN 123-45-6789 on file", [("government_id", "123-45-6789")]),
    ("she was diagnosed with diabetes", [("health", "diagnosed"), ("health", "diabetes")]),
    ("salary of $120,000 a year", [("money", "salary"), ("money", "$120,000")]),
])
def test_finds(text, expected):
    assert cats(text) == expected


@pytest.mark.parametrize("text", [
    "best pizza near union square",
    "card 4111 1111 1111 1112",            # fails the Luhn check
    "meeting on 2026-11-04 at 10:30",      # a date is not a phone number
    "version 1.2.3 released",
    "the sky is blue",
])
def test_ignores(text):
    assert find(text) == []


def test_overlaps_keep_the_longer_match():
    # A card number is also a long digit run that could look like a phone number.
    assert cats("4111111111111111") == [("card_number", "4111111111111111")]


def test_redact():
    assert redact("mail bob@x.io or 917-555-0142") == "mail [email] or [phone]"
    assert redact("x, 917-555-0142.") == "x, [phone]."


def test_every_category_has_a_label():
    for f in find("bob@x.io 917-555-0142 4111111111111111 AKIAIOSFODNN7EXAMPLE 123-45-6789 HIV $5"):
        assert f.category in LABELS
