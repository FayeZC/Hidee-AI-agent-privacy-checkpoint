"""Updates: asked once, checked only with consent and at most every two weeks, installed only on a yes and only
when the release key signed exactly that file."""
import hashlib
import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from hidee import __version__
from hidee.ed25519 import public_key, sign
from hidee.update import (CHECK_EVERY_S, UpdateError, Updates, first_run, install, is_newer, offer,
                          signed_message, version_key)

SECRET = bytes(range(32))                      # a test key; the real one is never in the repository
PUBLIC = public_key(SECRET)
WHEEL = b"PK\x03\x04 pretend wheel"


def release(version="9.0.0", data=WHEEL, secret=SECRET, signed=True, name=None):
    sha = hashlib.sha256(data).hexdigest()
    info = {"version": version, "wheel": name or f"hidee-{version}-py3-none-any.whl",
            "url": f"https://hidee.net/download/hidee-{version}-py3-none-any.whl", "sha256": sha,
            "signature": sign(secret, signed_message(version, sha)).hex() if signed else None,
            "notes": ["Safer Codex sessions", "Resizable columns"]}
    return info


class Net:
    """A stand-in for the network: records every request."""

    def __init__(self, info, wheel=WHEEL):
        self.info, self.wheel, self.calls = info, wheel, []

    def __call__(self, url, limit):
        self.calls.append(url)
        return json.dumps(self.info).encode() if url.endswith("latest.json") else self.wheel


def make(tmp_path, info=None, now=10 * CHECK_EVERY_S):
    net = Net(info or release())
    clock = {"t": now}
    return Updates(tmp_path, clock=lambda: clock["t"], get=net, public_key=PUBLIC), net, clock


def test_versions_compare_as_numbers():
    assert version_key("0.10.0") > version_key("0.9.1")
    assert is_newer("9.0.0") and not is_newer(__version__)


def test_asked_once_and_an_empty_answer_means_yes(tmp_path):
    updates, net, _ = make(tmp_path)
    asked, said = [], []
    first_run(updates, lambda q: asked.append(q) or "", said.append)
    assert updates.consent is True and asked == ["Check for Hidee updates every 2 weeks? [Y/n] "]
    first_run(updates, lambda q: asked.append(q) or "n", said.append)
    assert len(asked) == 1 and updates.consent is True            # never asked again
    assert stat.S_IMODE(os.stat(tmp_path / "update.json").st_mode) == 0o600
    assert net.calls == []                                         # asking is not checking


def test_no_means_hidee_never_checks_on_its_own(tmp_path):
    updates, net, _ = make(tmp_path)
    first_run(updates, lambda q: "no", lambda s: None)
    assert updates.consent is False and not updates.due()
    assert offer(updates, lambda q: "y", lambda s: None) is False and net.calls == []


def test_without_an_answer_there_is_no_check(tmp_path):
    updates, net, _ = make(tmp_path)
    assert updates.consent is None and not updates.due()
    assert offer(updates, lambda q: "y", lambda s: None) is False and net.calls == []


def test_with_consent_it_checks_at_most_every_two_weeks(tmp_path):
    updates, net, clock = make(tmp_path)
    updates.set_consent(True)
    assert updates.due()
    offer(updates, lambda q: "n", lambda s: None)
    assert net.calls == ["https://hidee.net/latest.json"]
    clock["t"] += CHECK_EVERY_S - 60
    assert not updates.due()
    clock["t"] += 120
    assert updates.due()


def test_a_yes_installs_the_signed_wheel(tmp_path):
    updates, net, _ = make(tmp_path)
    updates.set_consent(True)
    installed, said = [], []
    assert offer(updates, lambda q: "y", said.append, install_fn=installed.append) is True
    assert installed[0].name == "hidee-9.0.0-py3-none-any.whl" and installed[0].read_bytes() == WHEEL
    assert "Hidee 9.0.0 is available" in said[0] and "Safer Codex sessions" in said[0]
    assert said[-1] == "Updated to Hidee 9.0.0. Start it again with: hidee app"


def test_a_no_installs_nothing_and_the_workspace_still_knows(tmp_path):
    updates, net, _ = make(tmp_path)
    updates.set_consent(True)
    installed = []
    assert offer(updates, lambda q: "", lambda s: None, install_fn=installed.append) is False   # [y/N]: empty is no
    assert installed == [] and updates.available()["version"] == "9.0.0"


@pytest.mark.parametrize("info, wheel, why", [
    (release(signed=False), WHEEL, "not signed"),
    (release(), b"something else", "does not match its published SHA-256"),
    (release(secret=bytes(32)), WHEEL, "not signed by Hidee's release key"),
    (release(name="../evil.whl"), WHEEL, "unexpected file name"),
])
def test_only_exactly_the_signed_file_installs(tmp_path, info, wheel, why):
    net = Net(info, wheel)
    updates = Updates(tmp_path, get=net, public_key=PUBLIC)
    with pytest.raises(UpdateError, match=why):
        updates.download(info)


def test_a_signature_for_another_version_does_not_carry_over(tmp_path):
    info = release(version="9.0.0")
    info["version"] = "9.9.9"                    # same file and signature, a different version claimed
    with pytest.raises(UpdateError, match="not signed by Hidee's release key"):
        Updates(tmp_path, get=Net(info), public_key=PUBLIC).download(info)


def test_hidee_update_checks_now_even_without_consent(tmp_path):
    updates, net, _ = make(tmp_path)
    said = []
    offer(updates, lambda q: "n", said.append, force=True)
    assert net.calls == ["https://hidee.net/latest.json"] and "9.0.0 is available" in said[0]


def test_a_source_checkout_is_not_updated_in_place(tmp_path):
    with pytest.raises(UpdateError, match="git pull"):
        install(tmp_path / "hidee-9.0.0-py3-none-any.whl")       # the test suite runs from the source folder


def test_without_a_terminal_nothing_is_asked_or_checked(tmp_path, monkeypatch):
    from hidee.cli import updates_before_start
    monkeypatch.setattr("builtins.input", lambda q: pytest.fail("asked without a terminal"))
    assert updates_before_start(tmp_path, interactive=False) is False
    assert not (tmp_path / "update.json").exists()


def test_settings_turns_the_check_on_and_off_and_the_workspace_shows_a_newer_version(tmp_path):
    from hidee.app import create_app
    from hidee.hook_api import APP_RULES
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path / "home", app_mode=True, allowed_hosts=("testserver",))
    client = TestClient(app)
    page = client.get("/settings").text
    assert "Check for updates every 2 weeks" in page and "You have not chosen yet" in page
    client.post("/settings/updates", data={"csrf": app.state.csrf, "check": "on"})
    assert app.state.updates.consent is True
    client.post("/settings/updates", data={"csrf": app.state.csrf})
    assert app.state.updates.consent is False
    app.state.updates.get = Net(release())
    client.post("/settings/updates/check", data={"csrf": app.state.csrf})
    assert "Hidee 9.0.0 is available" in client.get("/settings").text
    assert "Hidee 9.0.0 is available" in client.get("/workspace").text
