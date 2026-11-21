"""Key-free demos: a day at one of four fictional small businesses. Rules settle most steps; one waits for you."""
from __future__ import annotations

import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable

from .agents import add_agent
from .rules import from_dict, save_rules
from .store import Store


def _f(value: str, category: str) -> dict:
    return {"value": value, "category": category}


def _rule(agent, kind, recipient, allow=(), forbid=()):
    return {"agent": agent, "kind": kind, "recipient": recipient, "allow": list(allow), "forbid": list(forbid)}


def _step(title, expect, agent, kind, recipient, fields, minimal=None):
    step = {"title": title, "expect": expect, "agent": agent, "kind": kind, "recipient": recipient, "fields": fields}
    if minimal is not None:
        step["minimal"] = minimal
    return step


# --- Bluebird Bakery: a delivery note that says when the owner is away ---------------------------------
_DELIVERY = _f("Leave it at the back door.", "delivery_instruction")
BAKERY = {
    "title": "Bakery",
    "business": "Bluebird Bakery",
    "rules": {
        "agents": {"assistant": "Bluebird assistant", "bookkeeper": "Bookkeeping agent"},
        "recipients": {"web-search": "Web search", "agent-memory": "Assistant memory",
                       "parcel-co": "Parcel Co.", "accountant": "Accountant"},
        "categories": {"business_info": "public business information", "order_details": "order details",
                       "delivery_instruction": "delivery instructions", "routine": "daily routine",
                       "customer_contact": "customer contact details"},
        "rules": [_rule("assistant", "search", "web-search", ["business_info"]),
                  _rule("assistant", "remember", "agent-memory", ["order_details"]),
                  _rule("assistant", "send_message", "parcel-co", ["delivery_instruction"]),
                  _rule("bookkeeper", "send_message", "accountant", ["order_details"], ["customer_contact"])],
    },
    "steps": [
        _step("Look up a supplier's opening hours", "allowed", "assistant", "search", "web-search",
              {"query": _f("Flour Mill Co. opening hours", "business_info")}),
        _step("Save today's order to memory", "allowed", "assistant", "remember", "agent-memory",
              {"order": _f("Order #1042: two sourdough loaves, deliver Friday.", "order_details")}),
        _step("Send the customer list to an ad partner", "blocked", "assistant", "send_message", "ad-partner",
              {"customers": _f("Customer list with 312 email addresses.", "customer_contact")}),
        _step("Send the monthly sales summary to the accountant", "allowed", "bookkeeper", "send_message",
              "accountant", {"summary": _f("October sales: 412 orders, $9,870.", "order_details")}),
        _step("Send customer phone numbers to the accountant", "blocked", "bookkeeper", "send_message",
              "accountant", {"phones": _f("Customer phone numbers for follow-up calls.", "customer_contact")}),
        _step("A delivery note that also says when the owner is away", "waiting", "assistant", "send_message",
              "parcel-co", {"message": _DELIVERY, "note": _f("The owner is away every Tuesday night.", "routine")},
              {"message": _DELIVERY}),
    ],
}

# --- Maple Dental: an insurance claim that needs the diagnosis ------------------------------------------
CLINIC = {
    "title": "Dental clinic",
    "business": "Maple Dental",
    "rules": {
        "agents": {"frontdesk": "Front-desk assistant", "billing": "Billing agent"},
        "recipients": {"calendar": "Clinic calendar", "sms": "Text reminders", "insurer": "Insurance company"},
        "categories": {"patient_name": "patient name", "appointment_time": "appointment time",
                       "diagnosis": "diagnosis", "insurance_id": "insurance ID", "treatment_cost": "treatment cost"},
        "rules": [_rule("frontdesk", "remember", "calendar", ["patient_name", "appointment_time"]),
                  _rule("frontdesk", "send_message", "sms", ["patient_name", "appointment_time"], ["diagnosis"]),
                  _rule("billing", "send_message", "insurer", ["patient_name", "insurance_id", "treatment_cost"])],
    },
    "steps": [
        _step("Book a cleaning in the clinic calendar", "allowed", "frontdesk", "remember", "calendar",
              {"patient": _f("Jordan Rivera", "patient_name"), "time": _f("Thursday 3:30 pm", "appointment_time")}),
        _step("Text reminder that names the treatment", "blocked", "frontdesk", "send_message", "sms",
              {"text": _f("Hi Jordan, see you Thursday 3:30 pm", "appointment_time"),
               "reason": _f("for your root canal follow-up.", "diagnosis")}),
        _step("Plain text reminder", "allowed", "frontdesk", "send_message", "sms",
              {"name": _f("Hi Jordan,", "patient_name"), "text": _f("see you Thursday 3:30 pm.", "appointment_time")}),
        _step("Insurance claim with the diagnosis code", "waiting", "billing", "send_message", "insurer",
              {"patient": _f("Jordan Rivera, member 88-2041", "insurance_id"),
               "cost": _f("Claim: $1,150", "treatment_cost"),
               "diagnosis": _f("Diagnosis K04.7: periapical abscess", "diagnosis")}),
    ],
}

# --- Northwind Studio: an interview summary that mentions salary ----------------------------------------
_SUMMARY = _f("Sam Lee interviewed well: strong portfolio, good with clients.", "interview_notes")
RECRUITING = {
    "title": "Recruiting",
    "business": "Northwind Studio",
    "rules": {
        "agents": {"hiring": "Hiring assistant"},
        "recipients": {"candidate": "Candidate email", "team-chat": "Team chat", "web-search": "Web search",
                       "owner-feed": "Owner's suggestions"},
        "categories": {"role_info": "role information", "interview_time": "interview time",
                       "interview_notes": "interview notes", "salary_expectation": "salary expectation",
                       "social_media": "personal social media", "person_location": "where a person lives"},
        "rules": [_rule("hiring", "send_message", "candidate", ["role_info", "interview_time"]),
                  _rule("hiring", "send_message", "team-chat", ["interview_notes", "interview_time"]),
                  _rule("hiring", "search", "web-search", ["role_info"], ["social_media", "person_location"]),
                  _rule("hiring", "recommend", "owner-feed", ["role_info"], ["person_location", "social_media"])],
    },
    "steps": [
        _step("Send the interview time to the candidate", "allowed", "hiring", "send_message", "candidate",
              {"role": _f("Junior designer role", "role_info"), "time": _f("Interview Monday 10 am", "interview_time")}),
        _step("Search the candidate's personal social media", "blocked", "hiring", "search", "web-search",
              {"query": _f("Sam Lee personal Instagram", "social_media")}),
        _step("Suggest checking where the candidate lives", "blocked", "hiring", "recommend", "owner-feed",
              {"idea": _f("Look up Sam's home address to estimate the commute.", "person_location")}),
        _step("Interview summary that mentions the salary ask", "waiting", "hiring", "send_message", "team-chat",
              {"summary": _SUMMARY, "salary": _f("Sam is asking for $68,000.", "salary_expectation")},
              {"summary": _SUMMARY}),
    ],
}

# --- Juniper Candles: a complaint forwarded to an outside AI model --------------------------------------
_COMPLAINT = _f("The candle arrived cracked and the box was wet.", "complaint_text")
SHOP = {
    "title": "Online shop",
    "business": "Juniper Candles",
    "rules": {
        "agents": {"support": "Support assistant"},
        "recipients": {"customer": "Customer reply", "crm": "Customer records", "ai-model": "Outside AI model"},
        "categories": {"order_status": "order status", "customer_name": "customer name",
                       "complaint_text": "complaint text", "payment_card": "payment card number"},
        "rules": [_rule("support", "send_message", "customer", ["order_status", "customer_name"]),
                  _rule("support", "remember", "crm", ["order_status", "customer_name", "complaint_text"]),
                  _rule("support", "send_message", "ai-model", ["complaint_text"], ["payment_card"])],
    },
    "steps": [
        _step("Tell a customer their order has shipped", "allowed", "support", "send_message", "customer",
              {"name": _f("Hi Priya,", "customer_name"), "status": _f("your order shipped today.", "order_status")}),
        _step("Log the complaint in customer records", "allowed", "support", "remember", "crm",
              {"name": _f("Priya Shah", "customer_name"), "complaint": _COMPLAINT}),
        _step("Forward a ticket with a card number to the AI model", "blocked", "support", "send_message",
              "ai-model", {"ticket": _COMPLAINT, "card": _f("Card 4111 1111 1111 1111", "payment_card")}),
        _step("Ask the AI model to draft a reply, with the customer's name", "waiting", "support", "send_message",
              "ai-model", {"complaint": _COMPLAINT, "name": _f("Customer: Priya Shah", "customer_name")},
              {"complaint": _COMPLAINT}),
    ],
}

DEMOS = {"bakery": BAKERY, "clinic": CLINIC, "recruiting": RECRUITING, "shop": SHOP}
DEFAULT = "bakery"

# Kept for code that refers to the bakery demo directly.
ALL_STEPS = BAKERY["steps"]
SCENARIOS = [s for s in ALL_STEPS if s["expect"] == "waiting"]
HISTORY = [s for s in ALL_STEPS if s["expect"] != "waiting"]


def seed_home(home: Path, name: str = DEFAULT) -> dict[str, str]:
    """Write a demo's rules into a Hidee home and return a token for each of its agents."""
    demo = DEMOS[name]
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    save_rules(from_dict(demo["rules"]), home / "rules.yaml")
    store = Store(home / "hidee.db")
    try:
        return {agent_id: add_agent(store, agent_id, label, time.time())
                for agent_id, label in demo["rules"]["agents"].items()}
    finally:
        store.close()


def submit_in_process(engine, name: str = DEFAULT) -> list:
    """Submit a demo's steps directly to an engine."""
    return [engine.submit(s["agent"], s["kind"], s["recipient"], s["fields"], s.get("minimal"))
            for s in DEMOS[name]["steps"]]


def load_demo(app, name: str) -> None:
    """Replace everything in a running demo app with a fresh copy of one demo."""
    home = app.state.home
    app.state.store.clear()
    seed = DEMOS[name]
    save_rules(from_dict(seed["rules"]), home / "rules.yaml")
    for agent_id, label in seed["rules"]["agents"].items():
        add_agent(app.state.store, agent_id, label, time.time())
    app.state.demo_name = name
    submit_in_process(app.state.engine, name)


def create_demo_app(home: Path | str | None = None, name: str = DEFAULT):
    """A public demo with fictional data only, its steps already submitted, and a day-long review window."""
    from .app import create_app
    home = Path(home) if home else Path(tempfile.mkdtemp(prefix="hidee-cloud-demo-"))
    seed_home(home, name)
    app = create_app(home, review_timeout_s=24 * 3600, demo=True)
    app.state.demo_name = name
    submit_in_process(app.state.engine, name)
    return app


def run_scenarios(submit: Callable[[str, dict], dict], interval: float = 3.0,
                  sleep: Callable[[float], None] = time.sleep, log=print, name: str = DEFAULT) -> list[dict]:
    """Submit a demo's steps through `submit(agent_id, payload)`."""
    steps = DEMOS[name]["steps"]
    results = []
    for i, s in enumerate(steps, 1):
        payload = {k: s[k] for k in ("kind", "recipient", "fields") if k in s}
        if "minimal" in s:
            payload["minimal"] = s["minimal"]
        result = submit(s["agent"], payload)
        results.append(result)
        log(f"  {i}. {s['title']}: {result['status']}")
        if interval and i < len(steps):
            sleep(interval)
    return results


def main(port: int = 8765, interval: float = 3.0, open_browser: bool = True, name: str = DEFAULT) -> None:
    import httpx
    import uvicorn

    from .app import create_app

    demo = DEMOS[name]
    home = Path(tempfile.mkdtemp(prefix="hidee-demo-"))
    tokens = seed_home(home, name)
    server = uvicorn.Server(uvicorn.Config(create_app(home), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/"
    print(f"Hidee demo is running at {url}  (data in {home})", flush=True)
    print(f"Replaying a day at {demo['business']}: rules settle most steps, one waits for you.", flush=True)
    if open_browser:
        webbrowser.open(url)
    with httpx.Client(base_url=url, timeout=10) as client:
        def submit(agent: str, payload: dict) -> dict:
            response = client.post("/api/actions", json=payload, headers={"Authorization": f"Bearer {tokens[agent]}"})
            response.raise_for_status()
            return response.json()
        run_scenarios(submit, interval, log=lambda *a: print(*a, flush=True), name=name)
    print("Done. One request is waiting on the 'To review' page; decide it in the browser.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        server.should_exit = True
