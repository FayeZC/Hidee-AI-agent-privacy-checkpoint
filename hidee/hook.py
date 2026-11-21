"""Claude Code PreToolUse hook: ask the local Hidee app about each tool call. Standard library only.

Run as `python -m hidee.hook`. Claude Code passes the call as JSON on stdin. Exit 0 with Hidee's JSON reply on
stdout, or exit 2 with a message on stderr, which blocks the call. Without HIDEE_URL, Hidee is not managing this
session and the hook stays out of the way.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

TIMEOUT_S = 650   # longer than Hidee's review timeout (600 s), shorter than the hook timeout (660 s)


# Talk to Hidee directly, never through the session's proxy settings.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def main(stdin=None, stdout=None, stderr=None, env=None, opener=DIRECT, after: bool = False) -> int:
    """`after` is the AfterTool hook (Gemini CLI): it may add a note to a tool result, and never blocks."""
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    env = os.environ if env is None else env
    url = env.get("HIDEE_URL")
    if not url:
        return 0
    try:
        payload = stdin.read().encode("utf-8")
        request = urllib.request.Request(
            url.rstrip("/") + ("/hook/aftertool" if after else "/hook/pretooluse"), data=payload, method="POST",
            headers={"Content-Type": "application/json", "X-Hidee-Token": env.get("HIDEE_TOKEN", ""),
                     "X-Hidee-Session": env.get("HIDEE_SESSION", ""),
                     "X-Hidee-Agent": env.get("HIDEE_AGENT", "")})
        with opener(request, timeout=5 if after else TIMEOUT_S) as response:
            answer = json.loads(response.read().decode("utf-8") or "{}")
        if not isinstance(answer, dict):
            raise ValueError("unexpected reply")
    except Exception as e:  # fail closed: if Hidee cannot check the call, the call does not run
        if after:
            return 0           # a missing note blocks nothing
        stderr.write(f"Hidee could not check this tool call ({type(e).__name__}: {e}), so it was blocked. "
                     "Is the Hidee app still running?\n")
        return 2
    if answer:
        stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main(after="--after" in sys.argv[1:]))
