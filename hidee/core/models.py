"""Contracts for the experimental privacy gateway. Host policy is trusted input."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

DECISIONS = ("allow", "use_minimal_alternative", "request_verified_context", "block")


class BackendError(RuntimeError):
    """A sanitized backend failure; never include a provider response or API key."""


@dataclass(frozen=True)
class Action:
    action_id: str
    kind: str
    recipient: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class HostPolicy:
    policy_id: str
    version: str
    instructions: str
    allowed_kinds: tuple[str, ...]
    recipient_fields: dict[str, tuple[str, ...]]
    provider_fields: tuple[str, ...]
    max_checks: int = 16
    max_releases: int = 8
    max_context_bytes: int = 24000
    min_decision_probability: float = 0.8


@dataclass(frozen=True)
class HostContext:
    task_id: str
    version: str
    task: str
    purpose: str
    actor_id: str
    subject_id: str
    policy: HostPolicy
    provider_authorized: bool
    expires_at: float
    revoked: bool = False


@dataclass(frozen=True)
class BackendDecision:
    choice: str
    probabilities: dict[str, float]
    confidence: float
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class DecisionBackend(Protocol):
    def decide(self, state: dict[str, Any], criteria: dict[str, str]) -> BackendDecision:
        """Evaluate only the host-built state and supplied options."""
        ...


@dataclass(frozen=True)
class Review:
    decision: str
    review_id: str | None = None
    status: str = "reviewed"

    def public(self) -> dict[str, Any]:
        """Recipient-safe control result; omit scores, evidence, and raw content."""
        return {"decision": self.decision, "review_id": self.review_id, "status": self.status}


@dataclass(frozen=True)
class Execution:
    status: str
    action_id: str | None = None


Executor = Callable[[Action], None]
