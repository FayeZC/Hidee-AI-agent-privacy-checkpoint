from hidee.rules import evaluate, from_dict, sentence

DATA = {
    "agents": {"*": "Any session", "s1": "Session one"},
    "recipients": {"*": "any site", "web-search": "Web search"},
    "categories": {"plain": "ordinary text", "email": "email address"},
    "rules": [
        {"agent": "*", "kind": "fetch", "recipient": "*", "allow": ["plain"]},
        {"agent": "*", "kind": "search", "recipient": "web-search", "allow": ["plain"]},
        {"agent": "s1", "kind": "search", "recipient": "web-search", "allow": ["plain", "email"]},
        {"agent": "*", "kind": "network", "recipient": "*", "forbid": ["email"]},
        {"agent": "*", "kind": "use_tool", "recipient": "*"},
    ],
}


def test_wildcards_match_any_agent_and_recipient():
    rs = from_dict(DATA)
    assert evaluate(rs, "session:7", "fetch", "example.com", ["plain"]).verdict == "allow"
    assert evaluate(rs, "session:7", "fetch", "example.com", ["plain", "email"]).verdict == "review"
    assert evaluate(rs, "session:7", "network", "api.example.com", ["email"]).verdict == "block"


def test_exact_rule_beats_wildcard():
    rs = from_dict(DATA)
    assert evaluate(rs, "s1", "search", "web-search", ["email"]).verdict == "allow"
    assert evaluate(rs, "s2", "search", "web-search", ["email"]).verdict == "review"


def test_unlisted_recipient_without_a_rule_keeps_the_old_message():
    rs = from_dict(DATA)
    out = evaluate(rs, "s2", "search", "elsewhere", ["plain"])
    assert out.verdict == "block" and "'elsewhere' is not an approved recipient." in out.reason


def test_unlisted_recipient_label_is_its_id():
    rs = from_dict(DATA)
    out = evaluate(rs, "s2", "network", "api.example.com", ["email"])
    assert "api.example.com must never receive email address." == out.reason


def test_sentences_for_new_kinds():
    rs = from_dict(DATA)
    texts = [sentence(rs, r) for r in rs.rules]
    assert texts[0].startswith("Any session may fetch pages from any site")
    assert texts[3].startswith("Any session may connect to any site")
    assert texts[4].startswith("Any session may use tools on any site")
