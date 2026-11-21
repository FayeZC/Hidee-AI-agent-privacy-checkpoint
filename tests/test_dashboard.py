import html
import re
import time

import pytest
from fastapi.testclient import TestClient

from hidee.agents import add_agent
from hidee.app import create_app
from hidee.rules import from_dict, save_rules
from tests.test_api import MSG, RULES

ROUTINE = {"value": "Away every Tuesday", "category": "routine"}


@pytest.fixture
def setup(tmp_path):
    save_rules(from_dict(RULES), tmp_path / "rules.yaml")
    app = create_app(tmp_path)
    add_agent(app.state.store, "shop", "Shopping assistant", time.time())
    return TestClient(app), app, app.state.engine


def svg_text(page: str, container: str) -> str:
    """All text inside the first SVG of a container, joined by spaces (wrapped names still match)."""
    start = page.index("<svg", page.index(f'class="{container}"'))
    svg = page[start:page.index("</svg>", start)]
    return " ".join(html.unescape(t) for t in re.findall(r"<text[^>]*>([^<]*)</text>", svg))


def zoom(page: str) -> str:
    return svg_text(page, "flowpic")


def overview(page: str) -> str:
    return svg_text(page, "mappic")


def test_history_moves_to_dashboard(setup):
    client, _, _ = setup
    r = client.get("/history", follow_redirects=False)
    assert r.status_code in (301, 307, 308) and r.headers["location"] == "/dashboard"


def test_empty_dashboard_explains(setup):
    client, _, _ = setup
    assert "No activity yet" in client.get("/dashboard").text


def test_overview_shows_every_agent_service_and_counts(setup):
    client, _, engine = setup
    engine.submit("shop", "send_message", "parcel", {"msg": MSG})                     # allowed
    engine.submit("shop", "send_message", "ad-partner", {"msg": MSG})                 # blocked, unapproved
    engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE})    # waiting
    text = overview(client.get("/dashboard").text)
    for piece in ("Shopping assistant", "Hidee", "Parcel Co.", "ad-partner", "Not in your rules", "Outside service",
                  "3 requests", "1 sent", "1 waiting", "1 blocked",
                  "✓ delivery instructions", "○ daily routine"):
        assert piece in text, piece


def test_only_sensitive_steps_are_numbered_and_listed(setup):
    client, _, engine = setup
    allowed = engine.submit("shop", "send_message", "parcel", {"msg": MSG})
    blocked = engine.submit("shop", "send_message", "ad-partner", {"msg": MSG})
    waiting = engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE})
    page = client.get("/dashboard").text
    steps = page[page.index('class="steps"'):page.index("</ol>", page.index('class="steps"'))]
    assert f"?id={blocked.id}" in steps and f"?id={waiting.id}" in steps and f"?id={allowed.id}" not in steps
    mappic = page[page.index('class="mappic"'):page.index("</svg>", page.index('class="mappic"'))]
    assert f"?id={blocked.id}#zoom" in mappic and f"?id={waiting.id}#zoom" in mappic
    assert f"?id={allowed.id}#zoom" not in mappic


def test_default_zoom_is_the_step_waiting_for_you(setup):
    client, _, engine = setup
    engine.submit("shop", "send_message", "ad-partner", {"msg": MSG})
    engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE})
    engine.submit("shop", "send_message", "parcel", {"msg": MSG})
    page = client.get("/dashboard").text
    assert "Waiting for you" in zoom(page) and "Step 2 up close" in page


def test_zoomed_shorter_version_shows_what_crossed_and_what_stopped(setup):
    client, _, engine = setup
    rec = engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE}, {"msg": MSG})
    engine.decide(rec.id, "minimal")
    page = client.get(f"/dashboard?id={rec.id}").text
    text = zoom(page)
    for piece in ("Shopping assistant", "Hidee", "Parcel Co.", "You sent the shorter version",
                  "✓ delivery instructions", "✕ daily routine"):
        assert piece in text, piece
    assert "What happened" in page and "Decided by you" in page and "Co.." not in page
    assert "Back door" in page.split("What was sent")[1].split("has received so far")[0]
    assert "Away every Tuesday" not in page.split("What was sent")[1].split("has received so far")[0]


def test_zoomed_blocked_and_waiting_and_allowed(setup):
    client, _, engine = setup
    blocked = engine.submit("shop", "send_message", "ad-partner", {"msg": MSG})
    waiting = engine.submit("shop", "send_message", "parcel", {"msg": MSG, "note": ROUTINE})
    allowed = engine.submit("shop", "send_message", "parcel", {"msg": MSG})
    b = zoom(client.get(f"/dashboard?id={blocked.id}").text)
    assert "Rules blocked it" in b and "nothing sent" in b and "✕ delivery instructions" in b
    assert "○ daily routine" in zoom(client.get(f"/dashboard?id={waiting.id}").text)
    page = client.get(f"/dashboard?id={allowed.id}").text
    assert "Rules allowed it" in zoom(page) and "Up close" in page


def test_all_activity_lists_everything(setup):
    client, _, engine = setup
    ids = [engine.submit("shop", "send_message", "parcel", {"msg": MSG}).id for _ in range(3)]
    page = client.get("/dashboard").text
    activity = page[page.index("All activity"):]
    assert all(f"/dashboard?id={i}" in activity for i in ids)
    assert "No sensitive steps" in page


def test_unknown_case_is_404(setup):
    client, _, _ = setup
    assert client.get("/dashboard?id=nope").status_code == 404
