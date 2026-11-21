"""API keys saved in Hidee: kept in the system keychain, shown only as the last four characters, and handed to one
agent session as an environment variable."""
from fastapi.testclient import TestClient

from hidee.app import create_app
from hidee.hook_api import APP_RULES
from hidee.keys import PROVIDERS, KeyStore, MemoryBackend, check_key, launch_prefix


def test_save_read_mask_and_remove():
    keys = KeyStore(MemoryBackend())
    assert keys.get("anthropic") is None and keys.masked("anthropic") is None
    keys.save("anthropic", "  sk-ant-api03-abcdefgh1234 \n")
    assert keys.get("anthropic") == "sk-ant-api03-abcdefgh1234"
    assert keys.masked("anthropic") == "••••1234"
    keys.remove("anthropic")
    assert keys.get("anthropic") is None


def test_unknown_provider_is_refused():
    keys = KeyStore(MemoryBackend())
    try:
        keys.save("nope", "x")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_each_provider_has_its_variable():
    assert PROVIDERS["anthropic"]["env"] == "ANTHROPIC_API_KEY"
    assert PROVIDERS["gemini"]["env"] == "GEMINI_API_KEY"
    assert PROVIDERS["openai"]["env"] == "OPENAI_API_KEY"


def test_launch_prefix_keeps_the_key_out_of_the_command_line():
    env, prefix = launch_prefix("anthropic", "sk-ant-secret-9999")
    assert "sk-ant-secret-9999" not in prefix and env == {"HIDEE_KEY": "sk-ant-secret-9999"}
    # The saved key wins over Vertex AI or Bedrock settings from the shell profile.
    for name in ("CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_AUTH_TOKEN"):
        assert name in prefix
    assert 'export ANTHROPIC_API_KEY="$HIDEE_KEY"' in prefix and "unset HIDEE_KEY" in prefix


def test_check_key_reports_ok_and_rejected():
    def ok(request, timeout):
        class R:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"
        assert request.get_header("X-api-key") == "k"
        return R()

    def rejected(request, timeout):
        import urllib.error
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    assert check_key("anthropic", "k", opener=ok) == (True, "The key works.")
    good, why = check_key("anthropic", "k", opener=rejected)
    assert not good and "rejected" in why


# ---- the settings page ----

def make(tmp_path):
    (tmp_path / "rules.yaml").write_text(APP_RULES)
    app = create_app(tmp_path, app_mode=True, allowed_hosts=("testserver",))
    app.state.keys = KeyStore(MemoryBackend())
    return TestClient(app), app


def test_settings_page_lists_providers(tmp_path):
    client, _ = make(tmp_path)
    page = client.get("/settings").text
    for text in ("Anthropic", "Gemini", "OpenAI", "Not saved"):
        assert text in page


def test_saving_a_key_shows_only_its_end(tmp_path):
    client, app = make(tmp_path)
    r = client.post("/settings/keys/anthropic", data={"csrf": app.state.csrf, "key": "sk-ant-api03-zzzz7777"},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/settings").text
    assert "••••7777" in page and "sk-ant-api03-zzzz7777" not in page


def test_removing_a_key(tmp_path):
    client, app = make(tmp_path)
    app.state.keys.save("openai", "sk-proj-1234")
    client.post("/settings/keys/openai/remove", data={"csrf": app.state.csrf})
    assert app.state.keys.get("openai") is None


def test_settings_forms_need_the_form_token(tmp_path):
    client, _ = make(tmp_path)
    r = client.post("/settings/keys/anthropic", data={"csrf": "wrong", "key": "x"})
    assert r.status_code == 403
