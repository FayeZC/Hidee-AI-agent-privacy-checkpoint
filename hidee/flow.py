"""Draw one case as a concrete picture: agent → Hidee checkpoint → recipient, one chip per piece of information."""
from __future__ import annotations

from markupsafe import Markup, escape

from .icons import icon_in_svg
from .store import ActionRecord

W = 900
CHIP_H, GAP, R = 36, 12, 44
COLORS = {"ok": "#008A4E", "warn": "#E85300", "bad": "#D8141A", "wait": "#8A8A8A", "accent": "#1F3BFF",
          "line": "#0A0A0A", "soft": "#9A9A9A", "ink": "#0A0A0A", "muted": "#4D4D4D", "paper": "#FFFFFF"}
MARK_ICONS = {"ok": "✓", "warn": "⚠", "bad": "✕"}
SANS = "Space Grotesk, Helvetica Neue, Arial, sans-serif"
SERIF = SANS
MONO = "JetBrains Mono, SF Mono, Menlo, monospace"
AGENT_X, HIDEE_X, RECIPIENT_X = 92, 450, 808       # circle centres
LEFT_ZONE, RIGHT_ZONE = 271, 629                     # chip column centres
MAX_CHIP_W = 250


def decision_label(rec: ActionRecord) -> str:
    status, by = rec.status, rec.decided_by
    if status == "allowed":
        return "Rules allowed it" if by == "rules" else "You approved it"
    if status == "minimized":
        return "You sent the shorter version"
    if status == "waiting":
        return "Waiting for you"
    if status == "timed_out":
        return "No decision in time"
    if status == "execution_failed":
        return "Sending failed"
    if status == "blocked":
        if by == "rules":
            return "Rules blocked it"
        if rec.reason.startswith("Rejected"):
            return "You rejected it"
        return "Safety check stopped it"
    return status


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _wrap(text: str, limit: int) -> list[str]:
    """Split into at most two lines at word boundaries; never cut a word."""
    lines = [""]
    for word in text.split():
        candidate = (lines[-1] + " " + word).strip()
        if len(candidate) <= limit or not lines[-1]:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines if len(lines) <= 2 else [lines[0], " ".join(lines[1:])]


def _chip(cx: float, y: float, text: str, color: str, strike: bool = False) -> str:
    text = _short(text, 26)
    w = min(MAX_CHIP_W, int(len(text) * 8.4) + 30)
    deco = ' text-decoration="line-through"' if strike else ""
    return (f'<rect x="{cx - w / 2:.0f}" y="{y:.0f}" width="{w}" height="{CHIP_H}" fill="#fff" '
            f'stroke="{color}" stroke-width="1"/>'
            f'<text x="{cx:.0f}" y="{y + 23:.0f}" text-anchor="middle" font-family="{MONO}" font-size="13" '
            f'letter-spacing="0.8" style="text-transform:uppercase" fill="{color}"{deco}>{escape(text)}</text>')


def _node(cx: float, cy: float, icon_name: str, kind: str, name: str, name_color: str, ring: str,
          kind_color: str = COLORS["muted"], dashed: bool = False, filled: bool = False) -> str:
    """A square with a line icon, a mono kind label and the name below, wrapped to fit."""
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    fill = ring if filled else "#fff"
    parts = [f'<rect x="{cx - R:.0f}" y="{cy - R:.0f}" width="{2 * R}" height="{2 * R}" fill="{fill}" stroke="{ring}" '
             f'stroke-width="1"{dash}/>',
             icon_in_svg(icon_name, cx, cy, 38, "#FFFFFF" if filled else COLORS["ink"]),
             f'<text x="{cx}" y="{cy + R + 24:.0f}" text-anchor="middle" font-family="{MONO}" font-size="12" '
             f'letter-spacing="1.6" style="text-transform:uppercase" fill="{kind_color}">{escape(kind)}</text>']
    for i, line in enumerate(_wrap(name, 17)):
        parts.append(f'<text x="{cx}" y="{cy + R + 50 + i * 23:.0f}" text-anchor="middle" font-family="{SANS}" '
                     f'font-size="19" font-weight="600" letter-spacing="-0.3" fill="{name_color}">{escape(line)}</text>')
    return "".join(parts)


def render_flow(rec: ActionRecord, case: dict) -> Markup:
    """`case` is the dashboard view of `rec` (labels, rows with category and mark class)."""
    rows = case["rows"]
    n = max(1, len(rows))
    stack = n * CHIP_H + (n - 1) * GAP
    cy = 44 + max(R + 6, stack / 2 + 8)
    height = cy + R + 112
    sent_states = ("allowed", "minimized")
    got = set(case["reached"])
    out = []
    for row in rows:
        if rec.status in sent_states:
            out.append(("ok", "✓", False) if row["name"] in got else ("bad", "✕", True))
        elif rec.status == "execution_failed":
            out.append(("warn", "⚠", False))
        elif rec.status == "waiting":
            out.append(("wait", "○", False))
        else:
            out.append(("bad", "✕", True))
    any_sent = rec.status in sent_states and any(state == "ok" for state, _, _ in out)
    decision_color = COLORS[case["out_cls"]]
    right_color = COLORS["accent"] if any_sent else COLORS["soft"]
    dash = "" if any_sent else 'stroke-dasharray="5 6" '

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height:.0f}" width="100%" role="img" '
        f'aria-label="Information flow for this case">',
        '<defs>'
        f'<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["line"]}" stroke-width="1.4"/></marker>'
        f'<marker id="arr-ok" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["accent"]}" stroke-width="1.4"/></marker></defs>',
        f'<text x="{LEFT_ZONE}" y="22" text-anchor="middle" font-family="{MONO}" font-size="12" letter-spacing="1.6" '
        f'style="text-transform:uppercase" fill="{COLORS["muted"]}">Asked to send</text>',
        f'<text x="{RIGHT_ZONE}" y="22" text-anchor="middle" font-family="{MONO}" font-size="12" letter-spacing="1.6" '
        f'style="text-transform:uppercase" fill="{COLORS["muted"]}">Reached {escape(_short(case["recipient"], 18))}</text>',
        f'<line x1="{AGENT_X + R}" y1="{cy:.0f}" x2="{HIDEE_X - R - 6}" y2="{cy:.0f}" stroke="{COLORS["line"]}" '
        f'stroke-width="1.25" marker-end="url(#arr)"/>',
        f'<line x1="{HIDEE_X + R}" y1="{cy:.0f}" x2="{RECIPIENT_X - R - 6}" y2="{cy:.0f}" stroke="{right_color}" '
        f'stroke-width="1.25" {dash}marker-end="url({"#arr-ok" if any_sent else "#arr"})"/>',
        _node(AGENT_X, cy, "agent", "AI agent", case["agent"], COLORS["ink"], COLORS["line"]),
        _node(HIDEE_X, cy, "shield", "Hidee checkpoint", decision_label(rec), decision_color, COLORS["accent"],
              filled=True),
        _node(RECIPIENT_X, cy, "service", place_kind(rec.recipient), case["recipient"], COLORS["ink"], COLORS["line"]),
    ]
    top = cy - stack / 2
    for i, row in enumerate(rows):
        y = top + i * (CHIP_H + GAP)
        parts.append(_chip(LEFT_ZONE, y, f'{MARK_ICONS[row["cls"]]} {row["category"]}', COLORS[row["cls"]]))
        state, mark, strike = out[i]
        parts.append(_chip(RIGHT_ZONE, y, f"{mark} {row['category']}", COLORS[state], strike))
    if rec.status in ("blocked", "timed_out"):
        parts.append(f'<text x="{RIGHT_ZONE}" y="{top + stack + 30:.0f}" text-anchor="middle" font-family="{MONO}" '
                     f'font-size="13" letter-spacing="1.5" style="text-transform:uppercase" fill="{COLORS["bad"]}">nothing sent</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


SENSITIVE = ("waiting", "blocked", "minimized", "timed_out", "execution_failed")


def place_kind(recipient: str) -> str:
    if recipient == "this-computer":
        return "This computer"
    if recipient == "model-provider":
        return "Model provider"
    return "Outside service"


EVENT_DECISION = {"local": ("wait", "Ordinary local work"), "allowed": ("ok", "Passed"),
                  "blocked": ("bad", "Refused")}


def render_event_flow(event: dict, agent: str, place: str, labels: dict[str, str]) -> Markup:
    """The same picture for a step that was only recorded, not reviewed: local work and network connections."""
    cats = [c for c in event.get("categories", []) if c != "plain"]
    chips = [labels.get(c, c.replace("_", " ")) for c in cats] or ["ordinary work"]
    cls, decision = EVENT_DECISION.get(event["status"], ("wait", event["status"]))
    passed = event["status"] != "blocked"
    n = len(chips)
    stack = n * CHIP_H + (n - 1) * GAP
    cy = 44 + max(R + 6, stack / 2 + 8)
    height = cy + R + 112
    right_color = COLORS["accent"] if passed else COLORS["soft"]
    dash = "" if passed else 'stroke-dasharray="5 6" '
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height:.0f}" width="100%" role="img" '
        f'aria-label="Information flow for this step">',
        '<defs>'
        f'<marker id="earr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["line"]}" stroke-width="1.4"/></marker>'
        f'<marker id="earr-ok" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["accent"]}" stroke-width="1.4"/></marker></defs>',
        f'<text x="{LEFT_ZONE}" y="22" text-anchor="middle" font-family="{MONO}" font-size="12" letter-spacing="1.6" '
        f'style="text-transform:uppercase" fill="{COLORS["muted"]}">What it carried</text>',
        f'<text x="{RIGHT_ZONE}" y="22" text-anchor="middle" font-family="{MONO}" font-size="12" letter-spacing="1.6" '
        f'style="text-transform:uppercase" fill="{COLORS["muted"]}">Reached {escape(_short(place, 18))}</text>',
        f'<line x1="{AGENT_X + R}" y1="{cy:.0f}" x2="{HIDEE_X - R - 6}" y2="{cy:.0f}" stroke="{COLORS["line"]}" '
        f'stroke-width="1.25" marker-end="url(#earr)"/>',
        f'<line x1="{HIDEE_X + R}" y1="{cy:.0f}" x2="{RECIPIENT_X - R - 6}" y2="{cy:.0f}" stroke="{right_color}" '
        f'stroke-width="1.25" {dash}marker-end="url({"#earr-ok" if passed else "#earr"})"/>',
        _node(AGENT_X, cy, "agent", "AI agent", agent, COLORS["ink"], COLORS["line"]),
        _node(HIDEE_X, cy, "shield", "Hidee checkpoint", decision, COLORS[cls], COLORS["accent"], filled=True),
        _node(RECIPIENT_X, cy, "service", place_kind(event["recipient"]), place, COLORS["ink"], COLORS["line"]),
    ]
    top = cy - stack / 2
    for i, chip in enumerate(chips):
        y = top + i * (CHIP_H + GAP)
        color = COLORS["ok"] if not cats else COLORS["warn"]
        parts.append(_chip(LEFT_ZONE, y, f'{"✓" if not cats else "⚠"} {chip}', color))
        parts.append(_chip(RIGHT_ZONE, y, f'{"✓" if passed else "✕"} {chip}',
                           COLORS["ok"] if passed else COLORS["bad"], not passed))
    if not passed:
        parts.append(f'<text x="{RIGHT_ZONE}" y="{top + stack + 30:.0f}" text-anchor="middle" font-family="{MONO}" '
                     f'font-size="13" letter-spacing="1.5" style="text-transform:uppercase" fill="{COLORS["bad"]}">'
                     f'connection refused</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def _halo_text(x: float, y: float, text: str, color: str, size: int = 15, anchor: str = "middle") -> str:
    """Text with a paper-coloured halo, so it stays readable where it crosses a line."""
    return (f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" font-family="{MONO}" font-size="{size}" '
            f'letter-spacing="0.8" style="text-transform:uppercase" '
            f'fill="{color}" stroke="{COLORS["paper"]}" stroke-width="5" paint-order="stroke" '
            f'stroke-linejoin="round">{escape(text)}</text>')


def _towards(a: tuple[float, float], b: tuple[float, float], dist: float) -> tuple[float, float]:
    import math
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1
    return a[0] + dx / length * dist, a[1] + dy / length * dist


def render_overview(cases: list[dict], steps: dict[str, int], approved: set[str], selected_id: str | None) -> Markup:
    """The whole application: every agent, the Hidee checkpoint, every service, with numbered sensitive steps.

    `cases` are dashboard views in time order; `steps` maps a case id to its sensitive-step number.
    """
    agents = list(dict.fromkeys(c["agent"] for c in cases))
    recipients = list(dict.fromkeys(c["recipient_id"] for c in cases))
    labels = {c["recipient_id"]: c["recipient"] for c in cases}
    rows = max(len(agents), len(recipients), 1)
    top, span = 40, rows * 170
    height = top + span + 20
    cy = top + span / 2 - 30

    def ys(n: int) -> list[float]:
        return [top + (i + 0.5) * span / n - 30 for i in range(n)]

    agent_y = dict(zip(agents, ys(len(agents))))
    recipient_y = dict(zip(recipients, ys(len(recipients))))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height:.0f}" width="100%" role="img" '
             f'aria-label="All agents, the Hidee checkpoint, and every outside service">',
             '<defs>'
             f'<marker id="m-line" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["line"]}" stroke-width="1.4"/></marker>'
             f'<marker id="m-ok" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["ok"]}" stroke-width="1.4"/></marker>'
             f'<marker id="m-bad" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             f'<path d="M1,1 L9,5 L1,9" fill="none" stroke="{COLORS["bad"]}" stroke-width="1.4"/></marker></defs>']
    markers = []
    left_bus, right_bus = HIDEE_X - R - 60, HIDEE_X + R + 42
    soft = COLORS["soft"]
    # Agents → a vertical bus → Hidee
    ys_a = [agent_y[a] for a in agents] + [cy]
    parts.append(f'<line x1="{left_bus}" y1="{min(ys_a):.0f}" x2="{left_bus}" y2="{max(ys_a):.0f}" stroke="{soft}" stroke-width="1"/>')
    parts.append(f'<line x1="{left_bus}" y1="{cy:.0f}" x2="{HIDEE_X - R - 8}" y2="{cy:.0f}" stroke="{COLORS["line"]}" '
                 f'stroke-width="1" marker-end="url(#m-line)"/>')
    for agent in agents:
        y = agent_y[agent]
        n = sum(c["agent"] == agent for c in cases)
        parts.append(f'<line x1="{AGENT_X + R}" y1="{y:.0f}" x2="{left_bus}" y2="{y:.0f}" stroke="{COLORS["line"]}" stroke-width="1"/>')
        parts.append(_halo_text((AGENT_X + R + left_bus) / 2, y - 12, f'{n} request{"s" if n != 1 else ""}',
                                COLORS["muted"], size=12))
    # Hidee → a vertical bus → every service, one coloured branch each
    ys_r = [recipient_y[r] for r in recipients] + [cy]
    parts.append(f'<line x1="{HIDEE_X + R}" y1="{cy:.0f}" x2="{right_bus}" y2="{cy:.0f}" stroke="{COLORS["line"]}" stroke-width="1"/>')
    parts.append(f'<line x1="{right_bus}" y1="{min(ys_r):.0f}" x2="{right_bus}" y2="{max(ys_r):.0f}" stroke="{soft}" stroke-width="1"/>')
    for rid in recipients:
        mine = [c for c in cases if c["recipient_id"] == rid]
        sent = sum(c["status"] in ("allowed", "minimized") for c in mine)
        waiting = sum(c["status"] == "waiting" for c in mine)
        stopped = sum(c["status"] in ("blocked", "timed_out") for c in mine)
        failed = sum(c["status"] == "execution_failed" for c in mine)
        color, marker, dash = ((COLORS["accent"], "m-ok", "") if sent else
                               (COLORS["wait"], "m-line", ' stroke-dasharray="5 5"') if waiting else
                               (COLORS["bad"], "m-bad", ' stroke-dasharray="5 5"'))
        y, end = recipient_y[rid], RECIPIENT_X - R - 8
        parts.append(f'<line x1="{right_bus}" y1="{y:.0f}" x2="{end}" y2="{y:.0f}" stroke="{color}" stroke-width="1.25"{dash} '
                     f'marker-end="url(#{marker})"/>')
        counts = [f"{k} {word}" for k, word in ((sent, "sent"), (waiting, "waiting"), (stopped, "blocked"),
                                                 (failed, "failed")) if k]
        parts.append(_halo_text(end - 4, y - 18, " · ".join(counts), color if color != COLORS["wait"] else COLORS["muted"],
                                size=12, anchor="end"))
        for j, c in enumerate([c for c in mine if c["id"] in steps]):
            markers.append((c, right_bus + 34 + j * 36, y))
        # Which kinds of information reached this service, were stopped, or are waiting.
        reached, stopped_cats, waiting_cats = [], [], []
        for c in mine:
            for row in c["rows"]:
                if c["status"] in ("allowed", "minimized") and row["name"] in c["reached"]:
                    bucket = reached
                elif c["status"] == "waiting":
                    bucket = waiting_cats
                else:
                    bucket = stopped_cats
                if row["category"] not in bucket:
                    bucket.append(row["category"])
        lines = ([("✓", cat, COLORS["accent"], False) for cat in reached] +
                 [("○", cat, COLORS["wait"], False) for cat in waiting_cats if cat not in reached] +
                 [("✕", cat, COLORS["bad"], True) for cat in stopped_cats if cat not in reached])
        # Wrap each entry to the space between the bus and the service; never cut a word; at most 3 lines.
        width = int((RECIPIENT_X - R - 14 - (right_bus + 14)) / 8.2)
        rows_out, hidden = [], 0
        for mark, cat, col, strike in lines:
            wrapped = _wrap(f"{mark} {cat}", width)
            if len(rows_out) + len(wrapped) > 3:
                hidden += 1
                continue
            rows_out += [(text if k == 0 else "  " + text, col, strike) for k, text in enumerate(wrapped)]
        if hidden and len(rows_out) == 3:
            rows_out = rows_out[:2]
        for k, (text, col, strike) in enumerate(rows_out):
            deco = ' text-decoration="line-through"' if strike else ""
            parts.append(f'<text x="{right_bus + 14}" y="{y + 24 + k * 17:.0f}" font-family="{MONO}" font-size="12" '
                         f'letter-spacing="0.6" style="text-transform:uppercase" fill="{col}"{deco} '
                         f'xml:space="preserve">{escape(text)}</text>')
        if hidden:
            parts.append(f'<text x="{right_bus + 14}" y="{y + 24 + min(len(rows_out), 2) * 17:.0f}" font-family="{MONO}" '
                         f'font-size="12" fill="{COLORS["muted"]}">+{hidden} more</text>')
    # Nodes on top of edges
    for agent in agents:
        parts.append(_node(AGENT_X, agent_y[agent], "agent", "AI agent", agent, COLORS["ink"], COLORS["line"]))
    parts.append(_node(HIDEE_X, cy, "shield", "Hidee checkpoint", "checks every request", COLORS["ink"],
                       COLORS["accent"], filled=True))
    for rid in recipients:
        ok = rid in approved
        # "Not in your rules": no rule names this destination, so each step to it waited for a person's decision.
        parts.append(_node(RECIPIENT_X, recipient_y[rid], "service", place_kind(rid) if ok else "Not in your rules",
                           labels[rid], COLORS["ink"], COLORS["line"] if ok else COLORS["bad"],
                           kind_color=COLORS["muted"] if ok else COLORS["bad"], dashed=not ok))
    # Numbered sensitive steps, clickable squares on their branch
    for c, mx, my in markers:
        color = COLORS[c["out_cls"]]
        chosen = c["id"] == selected_id
        parts.append(f'<a href="/dashboard?id={c["id"]}#zoom" data-step="/step/{c["id"]}"><title>Step {steps[c["id"]]}: {escape(c["out_word"])}</title>'
                     f'<rect x="{mx - 13:.0f}" y="{my - 13:.0f}" width="26" height="26" fill="{color if chosen else "#fff"}" '
                     f'stroke="{color}" stroke-width="1.25"/>'
                     f'<text x="{mx:.0f}" y="{my + 5:.0f}" text-anchor="middle" font-family="{MONO}" font-size="13" '
                     f'font-weight="600" fill="{"#fff" if chosen else color}">{steps[c["id"]]}</text></a>')
    parts.append("</svg>")
    return Markup("".join(parts))


EVENT_WORDS = {
    "allow": "Your rules allowed it",
    "block": "Your rules blocked it",
    "review": "Your rules did not cover everything, so it waited for you",
}
CHOICE_WORDS = {"approve": "approve as written", "minimal": "send the shorter version", "reject": "reject"}
REFUSAL_WORDS = {"changed": "the approved content changed", "expired": "the approval expired",
                 "already_used": "the approval was already used"}


def timeline(events: list[dict], case: dict, when) -> list[dict]:
    """Turn audit events for one case into plain sentences."""
    lines = []
    for e in events:
        d, kind = e["detail"], e["event"]
        if kind == "submitted":
            where = case["recipient"].rstrip(".")
            text = f'{case["agent"]} asked Hidee before sending to {where}. {EVENT_WORDS.get(d.get("verdict"), "")}'
        elif kind == "decided":
            who = "you" if d.get("by") == "owner" else d.get("by")
            text = f"Decided by {who}: {CHOICE_WORDS.get(d.get('choice'), d.get('choice'))}"
        elif kind == "executed":
            text = f'Sent to {case["recipient"]}' + (" (shorter version)" if d.get("version") == "minimal" else "")
        elif kind == "execution_failed":
            text = "Sending failed. Hidee did not retry."
        elif kind == "grant_refused":
            text = f"Safety check stopped it: {REFUSAL_WORDS.get(d.get('reason'), d.get('reason'))}"
        elif kind == "timed_out":
            text = "No one decided in time, so it was not sent"
        else:
            text = kind
        lines.append({"when": when(e["at"]), "text": text.strip()})
    return lines
