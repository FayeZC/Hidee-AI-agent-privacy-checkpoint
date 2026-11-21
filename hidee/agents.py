"""Per-agent bearer tokens. Only a SHA-256 hash of each token is stored."""
from __future__ import annotations

import hashlib
import secrets

from .store import Store


def new_token() -> str:
    return "hd_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def add_agent(store: Store, agent_id: str, name: str, now: float) -> str:
    """Create or replace an agent's token and return the token once."""
    token = new_token()
    store.add_agent(agent_id, name, hash_token(token), now)
    return token


def authenticate(store: Store, token: str) -> dict | None:
    if not token:
        return None
    return store.agent_by_token_hash(hash_token(token))
