"""In-process research gateway with host-owned policy and one-use execution grants.

This is an enforcement primitive for trusted host code, not an authentication
server. Policy and field scopes must never be accepted from the task agent.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
import math
import threading
import time
import uuid
from typing import Any

from .models import Action, BackendDecision, DecisionBackend, Execution, Executor, HostContext, HostPolicy, Review


def _json(value: Any) -> str:
    result = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    result.encode("utf-8")  # Reject invalid Unicode before a provider call or grant.
    return result


def _validate_json_tree(value: Any) -> None:
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _validate_json_tree(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json_tree(item)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("Payload must contain JSON values")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any, low: float = 0, high: float = 1) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def _action_copy(action: Action) -> Action:
    if not isinstance(action, Action) or not all(_string(x) for x in (action.action_id, action.kind, action.recipient)):
        raise ValueError("Invalid action")
    if not isinstance(action.payload, dict) or not all(_string(k) for k in action.payload):
        raise ValueError("Invalid payload")
    # Freeze caller-owned nested data and reject non-JSON values before inference.
    _validate_json_tree(action.payload)
    payload = json.loads(_json(action.payload))
    _json([action.action_id, action.kind, action.recipient])
    return Action(action.action_id, action.kind, action.recipient, payload)


def _wire(action: Action) -> dict[str, Any]:
    # Agent-supplied action identifiers are not necessary model context.
    return {"kind": action.kind, "recipient": action.recipient, "payload": copy.deepcopy(action.payload)}


def _context_copy(context: HostContext) -> HostContext:
    if not isinstance(context, HostContext):
        raise ValueError("A host-owned context is required")
    c = copy.deepcopy(context)
    p = c.policy
    if not isinstance(p, HostPolicy):
        raise ValueError("A host-owned policy is required")
    if not all(_string(x) for x in (c.task_id, c.version, c.task, c.purpose, c.actor_id, c.subject_id,
                                    p.policy_id, p.version, p.instructions)):
        raise ValueError("Missing host context")
    if type(c.provider_authorized) is not bool or type(c.revoked) is not bool:
        raise ValueError("Authorization flags must be booleans")
    if not _number(c.expires_at, 0, float("inf")):
        raise ValueError("Invalid expiry")
    for n in (p.max_checks, p.max_releases, p.max_context_bytes):
        if type(n) is not int or n <= 0:
            raise ValueError("Budgets must be positive integers")
    if not _number(p.min_decision_probability) or p.min_decision_probability < 0.5:
        raise ValueError("Decision support floor must be between 0.5 and 1")
    if not isinstance(p.recipient_fields, dict) or not p.recipient_fields:
        raise ValueError("Explicit recipient field scopes are required")
    scopes = [p.allowed_kinds, p.provider_fields, *p.recipient_fields.values()]
    if any(not isinstance(s, (tuple, list)) or not all(_string(x) for x in s) for s in scopes):
        raise ValueError("Explicit field lists are required")
    if not p.allowed_kinds or not all(_string(r) for r in p.recipient_fields):
        raise ValueError("Invalid permission scope")
    _json(asdict(c))
    return c


class PrivacyGateway:
    """Serialize one task/subject's checks and execute only checked snapshots.

    Use one persistent instance per task identity. Audit and history access are
    for the trusted host only; expose Review.public() to an untrusted caller.
    """

    def __init__(self, backend: DecisionBackend, context: HostContext, clock=time.time):
        self.backend = backend
        self._context = _context_copy(context)
        self._clock = clock
        self._lock = threading.RLock()
        self._checks = 0
        self._revision = 0
        self._history: list[dict[str, Any]] = []
        self._audit: list[dict[str, Any]] = []
        self._pending: dict[str, dict[str, Any]] = {}

    @property
    def check_count(self) -> int:
        with self._lock:
            return self._checks

    def history(self) -> list[dict[str, Any]]:
        """Return a private host-side snapshot; not a public API response."""
        with self._lock:
            return copy.deepcopy(self._history)

    def audit(self) -> list[dict[str, Any]]:
        """Operational metadata, without raw payloads, probabilities, or keys."""
        with self._lock:
            return copy.deepcopy(self._audit)

    def update_context(self, context: HostContext) -> None:
        fresh = _context_copy(context)
        with self._lock:
            old = self._context
            if (fresh.task_id, fresh.actor_id, fresh.subject_id) != (old.task_id, old.actor_id, old.subject_id):
                raise ValueError("Use a separate gateway for another task or subject")
            self._context = fresh
            self._pending.clear()
            # Policy changes do not reset consumed budgets or disclosure history.

    def _reject(self, reason: str, decision: str = "block") -> Review:
        self._audit.append({"event": "review", "decision": decision, "reason": reason})
        return Review(decision, status="not_authorized")

    def _context_fingerprint(self) -> str:
        return _digest(asdict(self._context))

    def _permission_failure(self, action: Action) -> str | None:
        c = self._context
        if c.revoked or self._clock() >= c.expires_at:
            return "authorization_unavailable"
        if action.kind not in c.policy.allowed_kinds or action.recipient not in c.policy.recipient_fields:
            return "recipient_or_kind_denied"
        if not set(action.payload).issubset(c.policy.recipient_fields[action.recipient]):
            return "recipient_field_scope_denied"
        return None

    def _state(self, proposed: Action, alternative: Action | None) -> dict[str, Any]:
        c = self._context
        prior = [copy.deepcopy(r) for r in self._history if r["recipient"] == proposed.recipient]
        return {
            "task": {"request": c.task, "purpose": c.purpose},
            "policy": {"id": c.policy.policy_id, "version": c.policy.version, "instructions": c.policy.instructions},
            "authorization": {"actor_subject_relation": "self" if c.actor_id == c.subject_id else "other",
                              "verified_access": True, "recipient": proposed.recipient, "context_version": c.version},
            "proposed_action": _wire(proposed),
            "minimal_alternative": _wire(alternative) if alternative else None,
            "prior_releases": prior,
        }

    @staticmethod
    def _valid_decision(d: BackendDecision, criteria: dict[str, str]) -> bool:
        if not isinstance(d, BackendDecision) or d.choice not in criteria or not _string(d.model):
            return False
        p = d.probabilities
        return (isinstance(p, dict) and set(p) == set(criteria) and all(_number(v) for v in p.values())
                and math.isclose(sum(p.values()), 1.0, abs_tol=1e-6)
                and p[d.choice] == max(p.values()) and _number(d.confidence)
                and all(type(n) is int and n >= 0 for n in (d.input_tokens, d.output_tokens))
                and _number(d.latency_s, 0, float("inf")))

    def evaluate(self, action: Action, minimal_alternative: Action | None = None) -> Review:
        """Inspect candidates without executing them or exposing raw model scores."""
        with self._lock:
            if self._checks >= self._context.policy.max_checks:
                return self._reject("check_budget_exhausted")
            self._checks += 1
            try:
                proposed = _action_copy(action)
                alternative = _action_copy(minimal_alternative) if minimal_alternative is not None else None
            except (TypeError, ValueError, OverflowError, RecursionError):
                return self._reject("invalid_action")
            for candidate in (proposed, alternative):
                if candidate is not None:
                    failure = self._permission_failure(candidate)
                    if failure:
                        return self._reject(failure)
            if alternative and (alternative.kind != proposed.kind or alternative.recipient != proposed.recipient
                                or alternative.action_id == proposed.action_id):
                return self._reject("invalid_alternative")
            c = self._context
            if len(self._history) >= c.policy.max_releases:
                return self._reject("release_budget_exhausted")
            if not c.provider_authorized:
                return self._reject("provider_not_authorized")
            state = self._state(proposed, alternative)
            transfers = [state["proposed_action"], *state["prior_releases"]]
            if state["minimal_alternative"] is not None:
                transfers.append(state["minimal_alternative"])
            if any(not set(x["payload"]).issubset(c.policy.provider_fields) for x in transfers):
                return self._reject("provider_field_scope_denied")
            criteria = {
                "allow": "The original action fits the task, declared policy and recipient's permitted view, including prior releases.",
                "request_verified_context": "Needed verified context is missing; obtain it without releasing the proposed payload.",
                "block": "Do not execute the proposed action because it is inappropriate under the task and policy.",
            }
            if alternative:
                criteria["use_minimal_alternative"] = "The supplied alternative is sufficient for the task and policy; execute only that exact alternative."
            # Count the entire visible packet and rubric; no silent truncation.
            if len(_json({"state": state, "criteria": criteria}).encode()) > c.policy.max_context_bytes:
                return self._reject("context_budget_exhausted")
            fingerprint = self._context_fingerprint()
            revision = self._revision
            try:
                d = copy.deepcopy(self.backend.decide(copy.deepcopy(state), dict(criteria)))
                if not self._valid_decision(d, criteria):
                    return self._reject("invalid_backend_result")
            except Exception:
                # Neither provider errors nor exception messages reach recipients/logs.
                return self._reject("backend_unavailable")
            self._audit.append({"event": "backend", "model": d.model, "input_tokens": d.input_tokens,
                                "output_tokens": d.output_tokens, "latency_s": d.latency_s})
            if fingerprint != self._context_fingerprint() or revision != self._revision:
                return self._reject("state_changed_during_review")
            # Authorization may expire during a slow provider request.
            for candidate in (proposed, alternative):
                if candidate and self._permission_failure(candidate):
                    return self._reject("authorization_changed_during_review")
            if d.choice in {"block", "request_verified_context"}:
                self._audit.append({"event": "review", "decision": d.choice, "reason": "backend_decision"})
                return Review(d.choice)
            if d.probabilities[d.choice] < c.policy.min_decision_probability:
                return self._reject("insufficient_decision_support", "request_verified_context")
            selected = alternative if d.choice == "use_minimal_alternative" else proposed
            review_id = uuid.uuid4().hex
            self._pending[review_id] = {"action": selected, "context": fingerprint, "revision": revision}
            self._audit.append({"event": "review", "decision": d.choice, "reason": "grant_created",
                                "action_digest": _digest(asdict(selected))})
            return Review(d.choice, review_id)

    def execute(self, review_id: str, executor: Executor) -> Execution:
        """Consume a grant and invoke a trusted executor with its frozen action.

        The executor must send exactly that action to its recipient. Tool results
        remain in trusted host code and require their own check before forwarding.
        """
        with self._lock:
            if not isinstance(review_id, str) or not callable(executor):
                return Execution("not_executed")
            grant = self._pending.pop(review_id, None)
            if grant is None:
                return Execution("not_executed")
            action = grant["action"]
            if (grant["context"] != self._context_fingerprint() or grant["revision"] != self._revision
                    or self._permission_failure(action) or len(self._history) >= self._context.policy.max_releases):
                self._audit.append({"event": "execution", "status": "not_executed", "reason": "stale_grant"})
                return Execution("not_executed")
            # Reserve the information-flow event before performing any side effect.
            # If the sink fails after sending, conservatively retain possible exposure.
            entry = {"sequence": self._revision + 1, **_wire(action), "status": "possibly_released"}
            self._history.append(entry)
            self._revision += 1
            self._pending.clear()
            try:
                executor(_action_copy(action))
            except Exception:
                self._audit.append({"event": "execution", "status": "execution_uncertain"})
                return Execution("execution_uncertain", action.action_id)
            entry["status"] = "released"
            self._audit.append({"event": "execution", "status": "executed"})
            return Execution("executed", action.action_id)


# Public names used by the Hidee service.
canonical_json = _json
digest = _digest
valid_decision = PrivacyGateway._valid_decision
