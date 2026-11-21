import pytest

from hidee.rules import Rule, RuleSet, RulesError, evaluate, from_dict, load_rules, save_rules, sentence

DATA = {
    "agents": {"shop": "Shopping assistant"},
    "recipients": {"parcel": "Parcel Co."},
    "categories": {"delivery": "delivery instructions", "routine": "daily routine", "address": "home address"},
    "rules": [{"agent": "shop", "kind": "send_message", "recipient": "parcel",
               "allow": ["delivery"], "forbid": ["address"]}],
}


@pytest.fixture
def rs():
    return from_dict(DATA)


def test_sentence_is_plain_language(rs):
    assert sentence(rs, rs.rules[0]) == (
        "Shopping assistant may send messages to Parcel Co. with delivery instructions without asking; "
        "never home address; anything else waits for your review.")


def test_all_allowed_categories_are_allowed(rs):
    out = evaluate(rs, "shop", "send_message", "parcel", ["delivery"])
    assert out.verdict == "allow"
    assert out.marks == {"delivery": "needed"}


def test_uncovered_category_needs_review(rs):
    out = evaluate(rs, "shop", "send_message", "parcel", ["delivery", "routine"])
    assert out.verdict == "review"
    assert out.marks == {"delivery": "needed", "routine": "check"}
    assert "daily routine" in out.reason


def test_forbidden_category_blocks(rs):
    out = evaluate(rs, "shop", "send_message", "parcel", ["delivery", "address"])
    assert out.verdict == "block"
    assert out.marks["address"] == "forbidden"
    assert out.reason == "Parcel Co. must never receive home address."


def test_unknown_recipient_blocks(rs):
    out = evaluate(rs, "shop", "send_message", "ad-partner", ["delivery"])
    assert out.verdict == "block"
    assert "not an approved recipient" in out.reason


def test_no_matching_rule_blocks(rs):
    out = evaluate(rs, "shop", "search", "parcel", ["delivery"])
    assert out.verdict == "block"
    assert out.reason == "Shopping assistant has no permission to search Parcel Co.."


def test_unknown_kind_blocks(rs):
    assert evaluate(rs, "shop", "delete_everything", "parcel", ["delivery"]).verdict == "block"


def test_roundtrip(tmp_path, rs):
    path = tmp_path / "rules.yaml"
    save_rules(rs, path)
    assert load_rules(path) == rs


@pytest.mark.parametrize("change, message", [
    (lambda d: d["rules"][0].update(agent="ghost"), "unknown agent"),
    (lambda d: d["rules"][0].update(recipient="ghost"), "unknown recipient"),
    (lambda d: d["rules"][0].update(kind="ghost"), "unknown kind"),
    (lambda d: d["rules"][0].update(allow=["ghost"]), "unknown categories"),
    (lambda d: d["rules"][0].update(forbid=["delivery"]), "both allowed and forbidden"),
    (lambda d: d["rules"][0].pop("recipient"), "missing 'recipient'"),
])
def test_invalid_rules_are_rejected(change, message):
    import copy
    data = copy.deepcopy(DATA)
    change(data)
    with pytest.raises(RulesError, match=message):
        from_dict(data)


def test_empty_file_means_no_rules(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("")
    assert load_rules(path) == RuleSet()
