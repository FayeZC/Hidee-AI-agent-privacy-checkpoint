import hashlib
import re
import time

from fastapi.testclient import TestClient

from hidee.online import COOKIE, create_online_app
from hidee.website import create_web_app


def make(tmp_path, with_wheel=True):
    release = tmp_path / "release"
    release.mkdir()
    if with_wheel:
        (release / "hidee-9.9.9-py3-none-any.whl").write_bytes(b"wheel bytes")
    app, online = create_web_app("https://hidee.net", "https://try.hidee.net", release, pace=0.02)
    return TestClient(app), online


def test_site_pages_and_slogan(tmp_path):
    client, _ = make(tmp_path)
    home = client.get("/").text
    assert "See higher." in home and "Know deeper." in home and 'class="hero-art"' in home
    assert 'href="https://try.hidee.net/workspace"' in home
    for path in ("/download", "/docs"):
        assert client.get(path).status_code == 200


def test_download_offers_the_wheel_with_its_checksum(tmp_path):
    client, _ = make(tmp_path)
    page = client.get("/download").text
    assert hashlib.sha256(b"wheel bytes").hexdigest() in page and "hidee-9.9.9-py3-none-any.whl" in page
    assert client.get("/download/hidee-9.9.9-py3-none-any.whl").content == b"wheel bytes"
    assert client.get("/download/other.whl").status_code == 404
    script = client.get("/install.sh").text
    assert "https://hidee.net/download/hidee-9.9.9-py3-none-any.whl" in script and "uv tool install" in script
    # The script checks the download against the published SHA-256 before installing anything.
    assert f'SHA256="{hashlib.sha256(b"wheel bytes").hexdigest()}"' in script
    # Without uv or pipx it uses its own virtual environment, never `pip install --user` into a system Python.
    assert '-m venv --clear "$HOME/.hidee/venv"' in script and "--user" not in script


def test_without_a_release_the_site_says_so(tmp_path):
    client, _ = make(tmp_path, with_wheel=False)
    assert "being prepared" in client.get("/download").text
    assert client.get("/install.sh").status_code == 503


def test_try_host_serves_a_private_sandbox(tmp_path):
    client, online = make(tmp_path)
    r = client.get("/workspace", headers={"host": "try.hidee.net"})
    assert r.status_code == 200 and "Online demo" in r.text and "Start the demo" in r.text
    assert COOKIE in r.headers["set-cookie"] and len(online.boxes) == 1
    assert "Download Hidee" in r.text and "https://hidee.net/download" in r.text


def run_demo(client, host="try.hidee.net"):
    page = client.get("/workspace", headers={"host": host}).text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = client.post("/sessions", data={"csrf": csrf, "kind": "clinic", "name": "Clinic assistant"},
                    headers={"host": host}, follow_redirects=False)
    assert r.status_code == 303


def test_scripted_session_uses_the_real_engine(tmp_path):
    client = TestClient(create_online_app(pace=0.02))
    run_demo(client, "testserver")
    box = next(iter(client.app.boxes.values())).app
    for _ in range(200):
        if box.state.store.list_actions(status="waiting"):
            break
        time.sleep(0.02)
    rec = box.state.store.list_actions(status="waiting")[0]
    assert rec.recipient == "web-search" and {"email", "phone"} <= {f["category"] for f in rec.fields.values()}
    box.state.engine.decide(rec.id, "minimal")
    for _ in range(200):
        text = bytes(next(iter(box.state.sessions.sessions.values())).scrollback).decode()
        if "Ran the shorter version" in text:
            break
        time.sleep(0.02)
    assert "[email]" in text and "maria.lopez@example.org, root canal" in text   # asked, then shortened
    assert "/var/" not in client.get("/workspace").text                          # the temp folder stays private


def test_visitors_are_isolated(tmp_path):
    online = create_online_app(pace=0.02)
    a, b = TestClient(online), TestClient(online)
    run_demo(a, "testserver")
    a.get("/live")
    b.get("/live")
    assert len(online.boxes) == 2
    assert b.get("/live").json()["events"] == []
    online.ttl_s = 0
    online.get(None, create=False)                                               # idle sandboxes are removed
    assert online.boxes == {}


def test_latest_json_says_how_to_verify_the_newest_release(tmp_path):
    client, _ = make(tmp_path)
    release = tmp_path / "release"
    (release / "hidee-10.0.0-py3-none-any.whl").write_bytes(b"newer wheel")     # 10 sorts after 9 by version
    (release / "hidee-10.0.0-py3-none-any.whl.sig").write_text("ab" * 64 + "\n")
    (release / "hidee-10.0.0-py3-none-any.whl.notes.json").write_text('["Resizable columns"]')
    info = client.get("/latest.json").json()
    assert info == {"version": "10.0.0", "wheel": "hidee-10.0.0-py3-none-any.whl",
                    "url": "https://hidee.net/download/hidee-10.0.0-py3-none-any.whl",
                    "sha256": hashlib.sha256(b"newer wheel").hexdigest(), "signature": "ab" * 64,
                    "notes": ["Resizable columns"]}


def test_the_download_page_offers_the_mac_app_when_one_is_built(tmp_path):
    client, _ = make(tmp_path)
    assert "Hidee for Mac" not in client.get("/download").text                 # no disk image yet
    (tmp_path / "release" / "Hidee-9.9.9-macos-arm64.dmg").write_bytes(b"disk image")
    page = client.get("/download").text
    assert "Download Hidee 9.9.9 for Mac" in page and hashlib.sha256(b"disk image").hexdigest() in page
    assert "Open Anyway" in page
    assert client.get("/download/Hidee-9.9.9-macos-arm64.dmg").content == b"disk image"
    assert client.get("/download/Hidee-1.0.0-macos-arm64.dmg").status_code == 404   # only the current one
