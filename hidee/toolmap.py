"""Turn a Claude Code tool call into a Hidee action, and an approved shorter version back into tool input."""
from __future__ import annotations

import copy
import os
import re
import shlex
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .detect import LABELS, find, redact

NET_PROGRAMS = {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "ftp", "telnet", "http",
                "https", "xh", "gh", "aws", "gcloud", "gsutil", "az", "s3cmd", "rclone"}
MAIL_PROGRAMS = {"mail", "mailx", "sendmail", "msmtp", "mutt", "neomutt"}
# osascript driving an app that sends messages (Mail, Messages, Outlook).
SEND_SCRIPT = re.compile(r"application\s+\\?\"(?:Mail|Messages|Microsoft Outlook)\\?\"", re.I)
INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "deno", "bun"}
NET_CODE = re.compile(r"\b(?:requests|urllib|http\.client|httpx|aiohttp|smtplib|socket|fetch\s*\(|axios|Net::HTTP"
                      r"|LWP|https?\.request)\b")
SCRIPT_ADDRESS = re.compile(r"address\s*:\s*\\?\"([^\"\\]+)")
GIT_NET = {"push", "pull", "fetch", "clone", "ls-remote"}
URL = re.compile(r"\b(?:https?|ftp|ssh|git)://(?:[^@/\s]+@)?([A-Za-z0-9.-]+)")
USER_HOST = re.compile(r"\b[\w.-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
BARE_HOST = re.compile(r"^([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|app|ai|co|edu|gov|us|uk|de|cn))(?:[:/]|$)",
                       re.I)

Path = tuple  # a path into the tool input: keys and list indexes

# Recipient fields: where a detected detail addresses the destination rather than travelling as content. The
# shorter version keeps these by default (a reminder must still reach the patient); the person can still remove
# them. One place for every tool:
# - structured inputs (MCP tools and other JSON tool inputs): the leaf's own key, for example send_email.to,
#   post_message.channel, a billing API's email;
# - text inputs (a curl command's JSON or form body, a fetched URL's query): the key written just before the detail,
#   for example "to": "...", to=..., email=..., recipient: ...
# The destination itself (the address a mail program sends to, the host of a URL) is never a detail at all.
RECIPIENT_KEYS = {"to", "cc", "bcc", "recipient", "recipients", "email", "emails", "email_address", "address",
                  "channel", "to_email", "to_address", "phone_to", "to_phone"}
# Messages to people (a chat message, an email, a post): Hidee can hold these and ask how they should go out
# (send now, save as a draft, shorter version, reject). A call is a message to a person when it is a mail or chat
# command (kind send_message), an MCP tool whose name says it sends a message, or a request whose body addresses a
# person through a destination field (RECIPIENT_CONTEXT below, e.g. a mail API's "to").
MESSAGE_TOOL = re.compile(r"(?i)(?:^|_)(?:send|post|reply|forward)(?:_[a-z]+)*_(?:email|mail|message|msg|post|reply|"
                          r"dm|direct_message|channel_message|comment)s?$|^(?:send|post)_?(?:email|message)$")
# Draft tools write into the user's own mailbox or chat drafts; nothing reaches anyone, so they are local work.
DRAFT_TOOL = re.compile(r"(?i)(?:^|_)(?:save|create|write|add)_?draft|_draft(?:_email|_message)?$|^draft_")


def is_draft_tool(tool: str) -> bool:
    return tool.startswith("mcp__") and bool(DRAFT_TOOL.search(tool.rsplit("__", 1)[-1]))


RECIPIENT_CONTEXT = re.compile(
    # a quoted key in JSON or similar ("to": "..., 'cc': '..., \"to\": \"...) or a form/query key (to=...); a key in
    # prose ("Email: ...") is content, not a destination
    r"""(?i)(?:\\?["'](?:to|cc|bcc|recipients?|e-?mail|email_address|to_email|to_address|address|channel)\\?["']\s*:"""
    r"""\s*\[?\s*\\?["']?|(?:^|[\s&?;])(?:to|cc|bcc|recipients?|e-?mail|email_address|to_email|to_address|channel)"""
    r"""=\\?["']?)$""")


@dataclass
class Mapped:
    kind: str
    recipient: str
    fields: dict[str, dict[str, str]]
    minimal: dict[str, dict[str, str]] | None
    tool_input: dict
    paths: dict[str, Path]
    # detected details: field key -> {"leaf", "start", "end", "category", "recipient"}; empty for scripts
    details: dict = None
    # detected details the user's own request names (field keys); the shorter version keeps them
    kept_requested: list = None

    def default_remove(self) -> list[str]:
        """Details the shorter version removes unless the person says otherwise: everything except recipients."""
        return [k for k, d in (self.details or {}).items() if not d["recipient"]]

    def selective_minimal(self, remove) -> dict[str, dict[str, str]]:
        """The shorter version that removes exactly the given details (field keys) and keeps every other text."""
        remove = set(remove)
        style = "token" if self.kind in ("fetch", "network", "send_message") else "bracket"
        out = {}
        for name, path in self.paths.items():
            text = self.fields[name]["value"]
            cuts = outermost([d for k, d in (self.details or {}).items() if d["leaf"] == name and k in remove])
            cuts = sorted(cuts, key=lambda d: d["start"], reverse=True)
            for d in cuts:
                mark = f"REDACTED_{d['category'].upper()}" if style == "token" else f"[{d['category']}]"
                text = text[:d["start"]] + mark + text[d["end"]:]
            out[name] = {"value": text, "category": "plain"}
        return out

    def rebuild(self, minimal: dict[str, dict[str, str]]) -> dict:
        """The original tool input with each kept text replaced by its shorter value."""
        out = copy.deepcopy(self.tool_input)
        for name, item in minimal.items():
            *parents, last = self.paths[name]
            node = out
            for key in parents:
                node = node[key]
            node[last] = item["value"]
        return out


def outermost(items: list) -> list:
    """Spans not inside another span (a detail inside a removed sentence goes with the sentence)."""
    def span(x):
        return (x["start"], x["end"]) if isinstance(x, dict) else (x.start, x.end)
    out = []
    for x in items:
        s, e = span(x)
        if not any(span(y) != (s, e) and span(y)[0] <= s and e <= span(y)[1] for y in items):
            out.append(x)
    return out


def _leaves(value: Any, path: Path = ()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, path + (i,))


def _name(path: Path) -> str:
    out = ""
    for key in path:
        out += f"[{key}]" if isinstance(key, int) else (f".{key}" if out else str(key))
    return out


IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host(command: str) -> str:
    for pattern in (URL, USER_HOST, IPV4):
        m = pattern.search(command)
        if m:
            return m.group(1).lower()
    for token in _tokens(command):
        m = BARE_HOST.match(token)
        if m:
            return m.group(1).lower()
    return "unknown-host"


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=True)
    except ValueError:
        return command.split()


def _programs(command: str) -> list[str]:
    tokens = [t for t in re.split(r"\s*(?:&&|\|\||[|;&()])\s*|\s+", command) if t]
    return [t.rsplit("/", 1)[-1].strip("'\"") for t in tokens]


def _sends_message(command: str) -> bool:
    names = _programs(command)
    return any(n in MAIL_PROGRAMS for n in names) or ("osascript" in names and bool(SEND_SCRIPT.search(command)))


def _uses_network(command: str) -> bool:
    names = _programs(command)
    for i, name in enumerate(names):
        if name in NET_PROGRAMS:
            return True
        if name == "git" and any(t in GIT_NET for t in names[i + 1:i + 4]):
            return True
        if name == "open" and URL.search(command):
            return True
        if name in INTERPRETERS and NET_CODE.search(command):
            return True
    return False


def _message_recipient(command: str) -> str:
    m = SCRIPT_ADDRESS.search(command)
    if m:
        return m.group(1).strip().lower()
    emails = [f.value.lower() for f in find(command) if f.category == "email"]
    return emails[0] if emails else "mail-app"


def _target(tool: str, tool_input: dict) -> tuple[str, str, list[str] | None] | None:
    """(kind, recipient, outbound keys or None for every string) for the tools Hidee checks."""
    if tool == "WebSearch":
        return "search", "web-search", ["query"]
    if tool == "WebFetch":
        host = urlsplit(str(tool_input.get("url", ""))).hostname or "unknown-host"
        return "fetch", host.lower(), ["url"]
    if tool.startswith("mcp__"):
        server = tool.split("__")[1] if tool.count("__") >= 2 else tool[5:]
        return "use_tool", f"mcp:{server}", None
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if _sends_message(command):
            return "send_message", _message_recipient(command), ["command"]
        if _uses_network(command):
            return "network", _host(command), ["command"]
    return None


def _is_destination(value: str, recipient: str) -> bool:
    value = value.lower()
    return value == recipient or value.endswith("@" + recipient)


SHELLS = {"bash", "sh", "zsh"}
SCRIPT_LIMIT = 256 * 1024
SCRIPT_SHOWN = 2000


def _scripts(command: str, cwd: str) -> list[tuple[str, str]]:
    """Script files this command runs (python3 x.py, node x.js, bash x.sh, ./x.sh), with their text."""
    found, tokens = [], _tokens(command)
    for i, token in enumerate(tokens):
        name = token.rsplit("/", 1)[-1]
        path = None
        if name in INTERPRETERS | SHELLS:
            rest = tokens[i + 1:]
            if rest and rest[0] in ("-m", "-c", "-e"):
                continue
            path = next((t for t in rest if not t.startswith("-")), None)
        elif token.startswith("./") or (i == 0 and re.search(r"\.(?:sh|py|js|mjs|rb|pl)$", token)):
            path = token
        if not path:
            continue
        full = _expand(path, cwd or os.getcwd())
        try:
            if os.path.isfile(full) and os.path.getsize(full) <= SCRIPT_LIMIT:
                with open(full, encoding="utf-8", errors="replace") as f:
                    found.append((path, f.read()))
        except OSError:
            continue
    return found


def _script_goes_out(text: str) -> bool:
    return bool(NET_CODE.search(text)) or _uses_network(text) or _sends_message(text)


def map_call(tool: str, tool_input: dict, cwd: str = "", known=None, request: str = "",
             freetext: bool = True) -> Mapped | None:
    """`known`: a known.KnownData for the session (values from files the user marked sensitive), or None.
    `request`: the user's own request text for the session (never tool results); details it names are kept in the
    shorter version (request_aware)."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    target = _target(tool, tool_input)
    scripts = []
    if tool == "Bash":
        # The command may only name a script; what the script sends is in the file, so read it too.
        scripts = [(n, t) for n, t in _scripts(str(tool_input.get("command", "")), cwd) if _script_goes_out(t)]
        if target is None and scripts:
            target = ("network", _host(scripts[0][1]), ["command"])
    if target is None or target[1] in LOCAL_HOSTS:   # calls to this computer's own services stay local
        return None
    if is_draft_tool(tool):                         # a draft in the user's own account reaches no one
        return None
    mapped = _map(tool_input, *target, known=known, request=request, freetext=freetext)
    if mapped is None or not scripts:
        return mapped
    for name, text in scripts:
        shown = text if len(text) <= SCRIPT_SHOWN else text[:SCRIPT_SHOWN] + "\n…"
        mapped.fields[f"script {name}"] = {"value": shown, "category": "plain"}
        counts: dict[str, int] = {}
        from .known import merge
        for f in merge(find(text), known.find(text) if known else []):
            if _is_destination(f.value, mapped.recipient):
                continue
            counts[f.category] = counts.get(f.category, 0) + 1
            n = counts[f.category]
            mapped.fields[f"script {name}: {LABELS[f.category]}{'' if n == 1 else f' {n}'}"] = {
                "value": f.value, "category": f.category}
    mapped.minimal = None        # a script cannot be shortened safely from here; approve or reject it
    return mapped


def is_recipient_detail(path: Path, text: str, start: int) -> bool:
    """True if a detail at text[start:] addresses the destination (see RECIPIENT_KEYS and RECIPIENT_CONTEXT)."""
    last = next((k for k in reversed(path) if isinstance(k, str)), "")
    if last.lower() in RECIPIENT_KEYS:
        return True
    return bool(RECIPIENT_CONTEXT.search(text[max(0, start - 48):start]))


def _map(tool_input: dict, kind: str, recipient: str, keys, known=None, request: str = "",
         freetext: bool = True) -> Mapped | None:
    source = tool_input if keys is None else {k: tool_input[k] for k in keys if k in tool_input}
    fields, minimal, paths, found_any, details = {}, {}, {}, False, {}
    for path, text in _leaves(source):
        if not text:
            continue
        name = _name(path)
        # The destination itself (the address a message goes to, or user@host for ssh) is where the call goes,
        # not something it leaks, so it does not count as a finding.
        found = find(text)
        if known is not None:
            from .known import merge
            found = merge(found, known.find(text))
        # sensitive facts written as free text: the sentence carrying the fact (it may contain other findings)
        if freetext:
            from .freetext import find as find_facts
            found = sorted(found + find_facts(text, known), key=lambda f: (f.start, -(f.end - f.start)))
        findings = [f for f in found if not _is_destination(f.value, recipient)]
        fields[name], paths[name] = {"value": text, "category": "plain"}, path
        style = "token" if kind in ("fetch", "network", "send_message") else "bracket"
        minimal[name] = {"value": redact(text, outermost(findings), style), "category": "plain"}
        for f in findings:
            found_any = True
            label, n = f"{name}: {LABELS[f.category]}", 2
            key = label
            while key in fields:
                key, n = f"{label} {n}", n + 1
            fields[key] = {"value": f.value, "category": f.category}
            details[key] = {"leaf": name, "start": f.start, "end": f.end, "category": f.category,
                            "recipient": is_recipient_detail(path, text, f.start)}
    if not fields:
        return None
    mapped = Mapped(kind, recipient, fields, minimal if found_any else None, tool_input, paths, details)
    if found_any:
        # The default shorter version keeps recipient details; if every detail is a recipient, it removes them all
        # (otherwise the shorter version would equal the original). Details the user's own request names are kept
        # too; if that leaves nothing to remove, there is no shorter version.
        from .request_aware import named_by_request
        kept = {k for k in details if named_by_request(fields[k]["category"], fields[k]["value"], request)}
        # A sentence that restates the user's sensitive files is kept when the request names a value inside it (a
        # detail found in the sentence, or a value of the files), or asks to include that value's kind.
        for k, d in details.items():
            if d["category"] != "known_fact" or k in kept:
                continue
            inner = [j for j, e in details.items() if j != k and e["leaf"] == d["leaf"]
                     and d["start"] <= e["start"] and e["end"] <= d["end"]]
            if any(j in kept for j in inner) or (known is not None and hasattr(known, "named_inside")
                                                 and known.named_inside(fields[k]["value"], request)):
                kept.add(k)
        mapped.kept_requested = sorted(kept)
        remove = [k for k in (mapped.default_remove() or list(details)) if k not in mapped.kept_requested]
        mapped.minimal = mapped.selective_minimal(remove) if remove else None
    return mapped


# ---- Local steps that overreach: credentials, privileged commands, files outside the session folder ----

FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS"}
CREDENTIAL_PATH = re.compile(
    r"(?:^|/)(?:\.ssh/|\.aws/|\.gnupg/|\.config/gcloud/|\.kube/config|\.docker/config\.json|\.netrc|\.npmrc|"
    r"\.pypirc|\.git-credentials|Library/Keychains/|id_(?:rsa|ed25519|ecdsa)\b|credentials(?:\.json)?$|"
    r"token\.json$|\.env(?:\.[\w-]+)?$|[\w-]+\.(?:pem|key|p12|pfx)$|/etc/(?:shadow|sudoers))")
CREDENTIAL_IN_COMMAND = re.compile(CREDENTIAL_PATH.pattern.replace("$", "\\b").replace("(?:^|/)", "(?:^|[/\\s~])"))
PRIVILEGED = {"sudo", "su", "doas", "chown", "chmod", "launchctl", "crontab", "security", "csrutil", "spctl",
              "dscl", "visudo", "passwd"}


def _expand(path: str, cwd: str) -> str:
    path = os.path.expanduser(path)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def _outside(path: str, cwd: str) -> bool:
    # Compare real paths: on macOS /tmp is /private/tmp, and Claude Code reports the resolved form.
    real, root = os.path.realpath(_expand(path, cwd)), os.path.realpath(cwd)
    return not (real == root or real.startswith(root.rstrip("/") + "/"))


def map_access(tool: str, tool_input: dict, cwd: str) -> Mapped | None:
    """A local step that reaches beyond the session's job, or None for ordinary local work."""
    if not isinstance(tool_input, dict):
        return None
    findings: list[tuple[str, str]] = []
    if tool in FILE_TOOLS:
        key = next((k for k in ("file_path", "notebook_path", "path") if isinstance(tool_input.get(k), str)), None)
        if key is None:
            return None
        path = tool_input[key]
        if CREDENTIAL_PATH.search(_expand(path, cwd)):
            findings.append(("credential_file", path))
        elif cwd and _outside(path, cwd):
            findings.append(("outside_folder", path))
        text = path
    elif tool == "Bash":
        key, text = "command", str(tool_input.get("command", ""))
        program = next((n for n in _programs(text) if n in PRIVILEGED), None)
        if program:
            findings.append(("privileged_command", program))
        m = CREDENTIAL_IN_COMMAND.search(text)
        if m:
            findings.append(("credential_file", m.group().strip()))
    else:
        return None
    if not findings:
        return None
    fields = {key: {"value": text, "category": "plain"}}
    for category, value in findings:
        fields[f"{key}: {LABELS[category]}"] = {"value": value, "category": category}
    return Mapped("access", "this-computer", fields, None, tool_input, {key: (key,)})


def is_person_message(tool: str, mapped: "Mapped | None") -> bool:
    """True when the call sends a message to a person (see MESSAGE_TOOL and RECIPIENT_CONTEXT)."""
    if mapped is None:
        return False
    if mapped.kind == "send_message":
        return True
    if mapped.kind == "use_tool":
        return bool(MESSAGE_TOOL.search(tool.rsplit("__", 1)[-1]))
    if mapped.kind in ("network", "fetch"):
        for f in mapped.fields.values():
            if f["category"] == "plain":
                for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", f["value"]):
                    if RECIPIENT_CONTEXT.search(f["value"][max(0, m.start() - 48):m.start()]):
                        return True
    return False
