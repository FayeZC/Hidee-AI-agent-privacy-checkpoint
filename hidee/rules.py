"""Owner-written rules: which agent may send which kinds of information to whom."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

KIND_PHRASES = {
    "search": "search",
    "remember": "save information to",
    "send_message": "send messages to",
    "recommend": "make suggestions through",
    "fetch": "fetch pages from",
    "use_tool": "use tools on",
    "network": "connect to",
    "access": "reach into",
}


class RulesError(ValueError):
    """The rules file is invalid; the message names the problem in plain words."""


@dataclass(frozen=True)
class Rule:
    agent: str
    kind: str
    recipient: str
    allow: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleSet:
    agents: dict[str, str] = field(default_factory=dict)
    recipients: dict[str, str] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)
    rules: tuple[Rule, ...] = ()

    def agent_label(self, key: str) -> str:
        return self.agents.get(key, key)

    def recipient_label(self, key: str) -> str:
        return self.recipients.get(key, key)

    def category_label(self, key: str) -> str:
        return self.categories.get(key, key.replace("_", " "))


@dataclass(frozen=True)
class RuleOutcome:
    verdict: str              # "allow" | "review" | "block"
    reason: str               # one plain-language sentence
    marks: dict[str, str]     # category -> "needed" | "check" | "forbidden"


def _join(items: Iterable[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def sentence(rs: RuleSet, rule: Rule) -> str:
    text = f"{rs.agent_label(rule.agent)} may {KIND_PHRASES[rule.kind]} {rs.recipient_label(rule.recipient)}"
    if rule.allow:
        text += f" with {_join(rs.category_label(c) for c in rule.allow)} without asking"
    if rule.forbid:
        text += f"; never {_join(rs.category_label(c) for c in rule.forbid)}"
    return text + "; anything else waits for your review."


def from_dict(data) -> RuleSet:
    if data is None:
        return RuleSet()
    if not isinstance(data, dict):
        raise RulesError("The rules file must contain a mapping")

    def table(name: str) -> dict[str, str]:
        value = data.get(name) or {}
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise RulesError(f"'{name}' must map ids to labels")
        return dict(value)

    agents, recipients, categories = table("agents"), table("recipients"), table("categories")
    rules = []
    for i, item in enumerate(data.get("rules") or [], 1):
        if not isinstance(item, dict):
            raise RulesError(f"Rule {i} must be a mapping")
        try:
            rule = Rule(item["agent"], item["kind"], item["recipient"],
                        tuple(item.get("allow") or ()), tuple(item.get("forbid") or ()))
        except KeyError as e:
            raise RulesError(f"Rule {i} is missing {e.args[0]!r}") from None
        if rule.agent not in agents:
            raise RulesError(f"Rule {i}: unknown agent {rule.agent!r}")
        if rule.recipient not in recipients:
            raise RulesError(f"Rule {i}: unknown recipient {rule.recipient!r}")
        if rule.kind not in KIND_PHRASES:
            raise RulesError(f"Rule {i}: unknown kind {rule.kind!r}")
        unknown = [c for c in rule.allow + rule.forbid if c not in categories]
        if unknown:
            raise RulesError(f"Rule {i}: unknown categories {unknown}")
        if set(rule.allow) & set(rule.forbid):
            raise RulesError(f"Rule {i}: a category cannot be both allowed and forbidden")
        rules.append(rule)
    return RuleSet(agents, recipients, categories, tuple(rules))


def to_dict(rs: RuleSet) -> dict:
    return {
        "agents": dict(rs.agents),
        "recipients": dict(rs.recipients),
        "categories": dict(rs.categories),
        "rules": [{"agent": r.agent, "kind": r.kind, "recipient": r.recipient,
                   "allow": list(r.allow), "forbid": list(r.forbid)} for r in rs.rules],
    }


def load_rules(path: Path | str) -> RuleSet:
    return from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def save_rules(rs: RuleSet, path: Path | str) -> None:
    Path(path).write_text(yaml.safe_dump(to_dict(rs), sort_keys=False, allow_unicode=True), encoding="utf-8")


def _match(rs: RuleSet, agent: str, kind: str, recipient: str) -> Rule | None:
    """The matching rule with the most exact fields; "*" stands for any agent or any recipient."""
    found = [r for r in rs.rules if r.kind == kind and r.agent in (agent, "*") and r.recipient in (recipient, "*")]
    return max(found, key=lambda r: (r.agent == agent) + (r.recipient == recipient), default=None)


def evaluate(rs: RuleSet, agent: str, kind: str, recipient: str, categories: list[str]) -> RuleOutcome:
    if kind not in KIND_PHRASES:
        return RuleOutcome("block", f"Unknown action type {kind!r}.", {c: "forbidden" for c in categories})
    rule = _match(rs, agent, kind, recipient)
    if rule is None and recipient not in rs.recipients:
        return RuleOutcome("block", f"{recipient!r} is not an approved recipient.", {c: "forbidden" for c in categories})
    if rule is None:
        return RuleOutcome("block", f"{rs.agent_label(agent)} has no permission to {KIND_PHRASES[kind]} "
                                    f"{rs.recipient_label(recipient)}.", {c: "forbidden" for c in categories})
    marks = {c: "forbidden" if c in rule.forbid else "needed" if c in rule.allow else "check" for c in categories}
    forbidden = [c for c in categories if marks[c] == "forbidden"]
    if forbidden:
        return RuleOutcome("block", f"{rs.recipient_label(recipient)} must never receive "
                                    f"{_join(rs.category_label(c) for c in forbidden)}.", marks)
    unclear = [c for c in categories if marks[c] == "check"]
    if not unclear:
        return RuleOutcome("allow", "Everything in this action is allowed by your rules.", marks)
    return RuleOutcome("review", f"Your rules do not cover {_join(rs.category_label(c) for c in unclear)}. "
                                 "Check whether it is needed.", marks)
