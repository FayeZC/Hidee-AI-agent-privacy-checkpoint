from hidee.agents import add_agent, authenticate, hash_token
from hidee.store import Store


def test_token_authenticates_and_is_stored_hashed(tmp_path):
    s = Store(tmp_path / "d.db")
    token = add_agent(s, "shop", "Shopping assistant", 1.0)
    assert token.startswith("hd_") and len(token) > 30
    assert authenticate(s, token) == {"id": "shop", "name": "Shopping assistant"}
    assert all(token not in str(row) for row in s.list_agents())
    assert s.agent_by_token_hash(hash_token(token))["id"] == "shop"


def test_wrong_or_revoked_token_fails(tmp_path):
    s = Store(tmp_path / "d.db")
    token = add_agent(s, "shop", "Shopping assistant", 1.0)
    assert authenticate(s, "hd_wrong") is None
    assert authenticate(s, "") is None
    s.revoke_agent("shop")
    assert authenticate(s, token) is None


def test_adding_again_replaces_the_token(tmp_path):
    s = Store(tmp_path / "d.db")
    old = add_agent(s, "shop", "Shopping assistant", 1.0)
    new = add_agent(s, "shop", "Shopping assistant", 2.0)
    assert authenticate(s, old) is None and authenticate(s, new)["id"] == "shop"
