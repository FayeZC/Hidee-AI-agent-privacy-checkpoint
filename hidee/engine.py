"""Decide and execute agent actions: rules first, then a person; only approved content runs."""
from __future__ import annotations

import time
import uuid
from typing import Callable

from .core import digest
from .executors import ExecutionRequest, Executors
from .rules import RuleSet, evaluate
from .store import ActionRecord, Store

CHOICES = ("approve", "minimal", "reject", "draft")
NOTE_MAX = 300


def clean_note(note) -> str:
    """A rejection reason as one short line of plain text (control characters removed, at most NOTE_MAX characters)."""
    text = " ".join(str(note or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:NOTE_MAX]


class ActionError(ValueError):
    """The request cannot be accepted. The message is safe to show the caller."""


def _validate_fields(fields, name: str = "fields") -> dict[str, dict[str, str]]:
    if not isinstance(fields, dict) or not fields:
        raise ActionError(f"{name} must be a non-empty object")
    clean = {}
    for key, item in fields.items():
        if not isinstance(key, str) or not key.strip():
            raise ActionError(f"{name} names must be non-empty text")
        if not isinstance(item, dict) or set(item) != {"value", "category"}:
            raise ActionError(f"each entry in {name} needs exactly 'value' and 'category'")
        if not isinstance(item["value"], str) or not isinstance(item["category"], str) or not item["category"].strip():
            raise ActionError(f"{name}.{key} needs a text value and a category")
        clean[key] = {"value": item["value"], "category": item["category"]}
    return clean


def _categories(fields: dict[str, dict[str, str]]) -> list[str]:
    return sorted({f["category"] for f in fields.values()})


SENT = ("allowed", "minimized")


def reached_fields(fields: dict[str, dict[str, str]], sent: dict[str, str]) -> set[str]:
    """The fields whose content is in what actually ran (`sent`: field name -> the value that ran).

    A field that ran unchanged reached the recipient; ordinary text reached it even when shortened. A detected
    detail (field "<leaf>: <kind>", as hidee.toolmap names it) reached the recipient when its value is still in the
    text of its leaf that ran: details the user's request names and destination fields are kept, the rest removed."""
    out = set()
    leaves = sorted(sent, key=len, reverse=True)
    for name, f in fields.items():
        if name in sent:
            if sent[name] == f["value"] or f["category"] == "plain":
                out.add(name)
            continue
        leaf = next((k for k in leaves if name.startswith(k + ": ")), None)
        if leaf is not None and f["category"] != "plain" and f["value"] and f["value"] in sent[leaf]:
            out.add(name)
    return out


def reached(rec: ActionRecord) -> set[str]:
    """The fields of a decided action that reached its recipient, from the executed record. Nothing reached for a
    rejection, a draft, a block, a time-out, or a step still waiting."""
    if rec.status not in SENT or not rec.executed:
        return set()
    return reached_fields(rec.fields, rec.executed)


class Engine:
    def __init__(self, store: Store, rules: Callable[[], RuleSet], executors: Executors,
                 clock: Callable[[], float] = time.time, review_timeout_s: float = 600, grant_ttl_s: float = 60):
        self.store, self.rules, self.executors, self.clock = store, rules, executors, clock
        self.review_timeout_s, self.grant_ttl_s = review_timeout_s, grant_ttl_s
        self.notes: dict[str, str] = {}      # action id -> the reason given with a rejection
        self.withhold_named: dict[str, list[str]] = {}   # action id -> categories a rejection names as not needed

    def get(self, action_id: str) -> ActionRecord | None:
        return self.store.get_action(action_id)

    def submit(self, agent_id: str, kind: str, recipient: str, fields, minimal=None, hold: bool = False,
               hold_reason: str = "") -> ActionRecord:
        """`hold`: wait for the person even when the rules would allow (messages to people); a block still blocks."""
        if not isinstance(kind, str) or not kind or not isinstance(recipient, str) or not recipient:
            raise ActionError("kind and recipient are required")
        fields = _validate_fields(fields)
        if minimal is not None:
            minimal = _validate_fields(minimal, "minimal")
            if not set(minimal) <= set(fields):
                raise ActionError("minimal may only keep or shorten entries that are in fields")
        rules, now = self.rules(), self.clock()
        outcome = evaluate(rules, agent_id, kind, recipient, _categories(fields))
        if minimal is not None and evaluate(rules, agent_id, kind, recipient, _categories(minimal)).verdict == "block":
            minimal = None  # never offer a version the rules forbid
        rec = ActionRecord(id=uuid.uuid4().hex, agent_id=agent_id, kind=kind, recipient=recipient, fields=fields,
                           minimal=minimal, status="received", reason=outcome.reason, marks=outcome.marks,
                           decided_by=None, executed=None, created_at=now, decided_at=None, expires_at=None)
        with self.store.lock:
            self.store.insert_action(rec)
            self.store.audit("submitted", rec.id, {"agent": agent_id, "kind": kind, "recipient": recipient,
                                                    "verdict": outcome.verdict}, now)
            if outcome.verdict == "block":
                self.store.update_action(rec.id, status="blocked", decided_by="rules", decided_at=now)
            elif outcome.verdict == "allow" and hold:
                self.store.update_action(rec.id, status="waiting", reason=hold_reason or outcome.reason,
                                         expires_at=now + self.review_timeout_s)
            elif outcome.verdict == "allow":
                self.store.update_action(rec.id, decided_by="rules", decided_at=now)
                self._grant_and_execute(rec.id, "original", "allowed")
            else:
                self.store.update_action(rec.id, status="waiting", expires_at=now + self.review_timeout_s)
            return self.store.get_action(rec.id)

    def decide(self, action_id: str, choice: str, decided_by: str = "owner", note: str = "",
               withhold: list[str] | None = None) -> ActionRecord:
        """`note`: for a rejection, an optional reason written by whoever decided, which the agent is told, such as
        "please resend without the phone number". Stored as written; never filled in by Hidee. `withhold`: for a
        rejection, the categories it names as not needed, as a list (withheld memory then remembers those details;
        `[]` names none, so the destination is remembered); without it, category names in `note` count."""
        if choice not in CHOICES:
            raise ActionError("choice must be approve, minimal, reject, or draft")
        with self.store.lock:
            self.expire_waiting()
            rec = self.store.get_action(action_id)
            if rec is None:
                raise ActionError("No such action")
            if rec.status != "waiting":
                raise ActionError("This action has already been decided")
            if choice == "minimal" and rec.minimal is None:
                raise ActionError("This action has no minimal version")
            now = self.clock()
            self.store.audit("decided", rec.id, {"choice": choice, "by": decided_by}, now)
            self.store.update_action(rec.id, decided_by=decided_by, decided_at=now)
            if choice == "draft":
                # The person will send it themselves: nothing runs and nothing leaves.
                self.store.update_action(rec.id, status="drafted",
                                         reason="Saved as a draft for the user; nothing was sent.")
            elif choice == "reject":
                note = clean_note(note)
                if note:
                    self.notes[rec.id] = note
                if withhold is not None:
                    # the categories the rejection names as not needed ([] when it names no detail, e.g. a destination)
                    self.withhold_named[rec.id] = [str(c) for c in withhold][:20]
                self.store.update_action(rec.id, status="blocked",
                                         reason=(f"Rejected by the user: {note}" + ("" if note[-1] in ".!?" else "."))
                                         if note else f"Rejected by {decided_by}.")
            elif choice == "approve":
                self._grant_and_execute(rec.id, "original", "allowed", f"Approved by {decided_by}.")
            else:
                self._grant_and_execute(rec.id, "minimal", "minimized",
                                        f"{decided_by.capitalize()} approved the shorter version.")
            return self.store.get_action(rec.id)

    def expire_waiting(self) -> int:
        now = self.clock()
        minutes = max(1, round(self.review_timeout_s / 60))
        with self.store.lock:
            ids = self.store.expired_waiting_ids(now)
            for action_id in ids:
                self.store.update_action(action_id, status="timed_out", decided_by="timeout", decided_at=now,
                                         reason=f"No one reviewed this within {minutes} minutes, so it was not sent.")
                self.store.audit("timed_out", action_id, {}, now)
            return len(ids)

    @staticmethod
    def _payload(rec: ActionRecord, version: str):
        return rec.fields if version == "original" else rec.minimal

    def _grant_and_execute(self, action_id: str, version: str, success_status: str,
                           success_reason: str | None = None) -> ActionRecord:
        rec = self.store.get_action(action_id)
        grant_id = uuid.uuid4().hex
        self.store.insert_grant(grant_id, action_id, version, digest(self._payload(rec, version)),
                                self.clock() + self.grant_ttl_s)
        return self.execute_grant(grant_id, success_status, success_reason)

    def execute_grant(self, grant_id: str, success_status: str, success_reason: str | None = None) -> ActionRecord:
        with self.store.lock:
            grant = self.store.get_grant(grant_id)
            if grant is None:
                raise ActionError("No such approval")
            rec = self.store.get_action(grant["action_id"])
            now = self.clock()
            if not self.store.use_grant(grant_id):
                self.store.audit("grant_refused", rec.id, {"reason": "already_used"}, now)
                return rec
            if now >= grant["expires_at"]:
                return self._refuse(rec, "The approval expired before it ran, so nothing was sent.", "expired", now)
            payload = self._payload(rec, grant["version"])
            if payload is None or digest(payload) != grant["payload_digest"]:
                return self._refuse(rec, "The approved content changed before it ran, so nothing was sent.",
                                    "changed", now)
            # Record possible exposure before the side effect, as the core gateway does: the kinds of information
            # in what runs, including details a shorter version kept (requested or destination), never removed ones.
            values = {k: v["value"] for k, v in payload.items()}
            kinds = set(_categories(payload)) | {rec.fields[n]["category"] for n in reached_fields(rec.fields, values)}
            self.store.add_disclosures(rec.id, rec.recipient, sorted(kinds), now)
            try:
                self.executors.run(ExecutionRequest(rec.id, rec.kind, rec.recipient, values))
            except Exception:
                self.store.update_action(rec.id, status="execution_failed", executed=values,
                                         reason="The executor reported an error. Hidee did not retry, "
                                                "because a retry could send it twice.")
                self.store.audit("execution_failed", rec.id, {}, now)
                return self.store.get_action(rec.id)
            changes = {"status": success_status, "executed": values}
            if success_reason:
                changes["reason"] = success_reason
            self.store.update_action(rec.id, **changes)
            self.store.audit("executed", rec.id, {"version": grant["version"]}, now)
            return self.store.get_action(rec.id)

    def _refuse(self, rec: ActionRecord, reason: str, code: str, now: float) -> ActionRecord:
        self.store.update_action(rec.id, status="blocked", reason=reason)
        self.store.audit("grant_refused", rec.id, {"reason": code}, now)
        return self.store.get_action(rec.id)
