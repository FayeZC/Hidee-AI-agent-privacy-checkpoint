import io
import json
import subprocess
import sys
import urllib.error

from hidee.hook import main

ENV = {"HIDEE_URL": "http://127.0.0.1:9", "HIDEE_TOKEN": "t", "HIDEE_SESSION": "s1"}
CALL = json.dumps({"tool_name": "WebSearch", "tool_input": {"query": "x"}})


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run(opener, env=ENV):
    out, err = io.StringIO(), io.StringIO()
    code = main(io.StringIO(CALL), out, err, env, opener)
    return code, out.getvalue(), err.getvalue()


def test_passes_the_reply_through_with_headers():
    seen = {}

    def opener(request, timeout):
        seen.update(url=request.full_url, token=request.get_header("X-hidee-token"),
                    session=request.get_header("X-hidee-session"), body=request.data)
        return Reply(b'{"hookSpecificOutput": {"permissionDecision": "deny"}}')
    code, out, _ = run(opener)
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert seen == {"url": "http://127.0.0.1:9/hook/pretooluse", "token": "t", "session": "s1",
                    "body": CALL.encode()}


def test_empty_reply_prints_nothing():
    assert run(lambda r, timeout: Reply(b"{}")) == (0, "", "")


def test_errors_block_the_call():
    def refused(request, timeout):
        raise urllib.error.URLError("connection refused")

    def unauthorized(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
    for opener in (refused, unauthorized, lambda r, timeout: Reply(b"not json"), lambda r, timeout: Reply(b"[1]")):
        code, out, err = run(opener)
        assert code == 2 and out == "" and "blocked" in err


def test_unmanaged_sessions_are_left_alone():
    assert run(lambda r, timeout: 1 / 0, env={}) == (0, "", "")


def test_real_process_exits_2_when_hidee_is_down():
    p = subprocess.run([sys.executable, "-m", "hidee.hook"], input=CALL, capture_output=True, text=True,
                       env={**ENV, "PATH": "/usr/bin:/bin"}, timeout=30)
    assert p.returncode == 2 and "blocked" in p.stderr
