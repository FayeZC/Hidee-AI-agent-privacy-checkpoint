"""Claude Code hook for learn mode: tell the local Hidee app what just happened. Standard library only.

Run as `python -m hidee.learn_hook` for UserPromptSubmit, PostToolUse, PostToolUseFailure, and Stop. It prints
nothing and always exits 0, so it can never block or change the session; if Hidee is unreachable, the step is
simply missing from the timeline.
"""
from __future__ import annotations

import os
import sys
import urllib.request

TIMEOUT_S = 2

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def main(stdin=None, stdout=None, stderr=None, env=None, opener=DIRECT) -> int:
    stdin = stdin or sys.stdin
    env = os.environ if env is None else env
    url = env.get("HIDEE_URL")
    if not url:
        return 0
    try:
        request = urllib.request.Request(
            url.rstrip("/") + "/hook/learn", data=stdin.read().encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Hidee-Token": env.get("HIDEE_TOKEN", ""),
                     "X-Hidee-Session": env.get("HIDEE_SESSION", ""),
                     "X-Hidee-Agent": env.get("HIDEE_AGENT", "")})
        with opener(request, timeout=TIMEOUT_S) as response:
            response.read()
    except Exception:  # fail open: learn mode only watches
        pass
    if env.get("HIDEE_AGENT") == "codex":
        (stdout or sys.stdout).write("{}")        # Codex expects JSON from a Stop hook
    return 0


if __name__ == "__main__":
    sys.exit(main())
