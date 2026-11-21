"""Real-agent smoke test: drive a safe-mode session through the app's terminal, as a person would.

For each choice it asks the agent to curl https://httpbin.org/anything/<email>, decides the checkpoint request
with that choice, and prints what Hidee recorded; the session's screen is saved for checking what httpbin echoed.
It answers folder-trust prompts and each agent's own confirmation, and skips update prompts (never updates).
It uses the agents' real sign-ins and sends fictional example.com addresses to httpbin.org.

  .venv/bin/hidee app --home /tmp/hidee-smoke/home --port 8790 --no-browser &
  .venv/bin/python scripts/smoke_agents.py claude approve,minimal,reject      # or gemini, codex
  .venv/bin/hidee evidence verify --home /tmp/hidee-smoke/home
"""
import asyncio, json, re, sys, time
from pathlib import Path

import httpx, websockets
from hidee.store import Store

BASE, HOME = "http://127.0.0.1:8790", Path("/tmp/hidee-smoke/home")
EMAILS = {"approve": "ada.approve@example.com", "minimal": "bo.shorter@example.com", "reject": "cy.reject@example.com"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-B]|\x1b[=>78]")


def clean(s): return ANSI.sub("", s)


async def run(agent: str, choices: list[str]):
    c = httpx.Client(base_url=BASE, headers={"host": "127.0.0.1:8790"})
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/workspace?new=1").text).group(1)
    work = Path(f"/tmp/hidee-smoke/work-{agent}"); work.mkdir(parents=True, exist_ok=True)
    r = c.post("/sessions", data={"csrf": csrf, "kind": agent, "where": "open", "cwd": str(work), "safe": "1",
                                  "learn": "1", "signin": "terminal", "name": f"smoke-{agent}"}, follow_redirects=False)
    assert r.status_code == 303, (r.status_code, r.text[:300])
    sid = r.headers["location"].split("s=")[1]
    print(f"session {sid}", flush=True)
    store = Store(HOME / "hidee.db")
    buf = []
    report = []
    async with websockets.connect(f"ws://127.0.0.1:8790/term/{sid}?key={csrf}", origin=BASE, max_size=None) as ws:
        async def reader():
            try:
                async for m in ws:
                    buf.append(m)
            except websockets.ConnectionClosed:
                pass
        task = asyncio.create_task(reader())
        send = lambda d: ws.send(json.dumps({"type": "input", "data": d}))
        await ws.send(json.dumps({"type": "resize", "rows": 45, "cols": 150}))
        # Start-up: answer folder-trust style prompts with Enter, wait until the screen settles.
        t0, answered = time.time(), 0
        while time.time() - t0 < 40:
            await asyncio.sleep(2)
            tail = clean("".join(buf))[-1500:].lower()
            if "update now" in tail or "skip until next version" in tail:
                await send("\x1b"); await asyncio.sleep(2); buf.clear(); continue     # never update anything
            if "trust" in tail and answered < 3:
                if "no,exit" in tail.replace(" ", "") and tail.replace(" ", "").find("❯no") >= 0:
                    await send("\x1b[B"); await asyncio.sleep(0.5)      # move from "No, exit" to "Yes"
                await send("\r"); answered += 1; await asyncio.sleep(4)
                buf.clear()
            elif len(buf) and time.time() - t0 > 12:
                break
        for choice in choices:
            email = EMAILS[choice]
            seen = {a.id for a in store.list_actions()}
            prompt = (f"Use your shell tool to run exactly this command and show me its output: "
                      f"curl -s https://httpbin.org/anything/{email}")
            await send(prompt); await asyncio.sleep(1); await send("\r")
            decided, t1 = None, time.time()
            while time.time() - t1 < 240:
                await asyncio.sleep(2)
                tail = "".join(clean("".join(buf[-40:])).split())
                if "Allowexecutionof" in tail[-600:] and "1.Allowonce" in tail[-600:]:
                    print(f"  [{choice}] Gemini asked its own confirmation; answering Allow once", flush=True)
                    await send("\r"); await asyncio.sleep(3); buf.append("\n[driver answered allow once]\n")
                if "Wouldyouliketorunthefollowingcommand?" in tail and "Pressentertoconfirm" in tail[-400:]:
                    print(f"  [{choice}] Codex asked to run outside its sandbox; answering y", flush=True)
                    await send("y"); await asyncio.sleep(3); buf.append("\n[driver answered y]\n")
                for a in store.list_actions(status="waiting"):
                    if a.id in seen or sid not in a.agent_id:
                        continue
                    pick = choice if (choice != "minimal" or a.minimal) else "reject"
                    c.post(f"/decide/{a.id}", data={"choice": pick, "csrf": csrf})
                    seen.add(a.id)
                    decided = decided or (a.recipient, pick, a.reason)
                    print(f"  [{choice}] decided {pick} on {a.recipient}: {a.reason}", flush=True)
                if decided:
                    n = len(buf); await asyncio.sleep(15)
                    if len(buf) == n or time.time() - t1 > 200:     # screen quiet: the agent finished
                        break
            if not decided:
                await send("\x1b"); await asyncio.sleep(2)            # cancel a stuck turn before the next prompt
            report.append((choice, decided))
            await asyncio.sleep(20)
        screen = clean("".join(buf))
        Path(f"/tmp/hidee-smoke/{agent}-screen.txt").write_text(screen)
        task.cancel()
    c.post(f"/sessions/{sid}/close", data={"csrf": csrf})
    print("actions for this session:")
    for a in store.list_actions():
        if sid in a.agent_id:
            print(f"  {a.status:10} {a.kind:8} {a.recipient:30} by={a.decided_by} executed={bool(a.executed)} :: {a.reason[:90]}")
    print("report:", report)
    print("SID", sid)


asyncio.run(run(sys.argv[1], sys.argv[2].split(",")))
