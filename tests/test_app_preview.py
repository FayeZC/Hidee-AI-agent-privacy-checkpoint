from fastapi.testclient import TestClient

from hidee.app import create_app


def test_app_preview_shows_three_panes_with_a_guide(tmp_path):
    client = TestClient(create_app(tmp_path))
    page = client.get("/app").text
    for text in ("Sessions", "Claude Code", "Hidee", "Your agents.", "A real terminal.",
                 "The Hidee checkpoint.", "Send shorter version", "Everything runs on this computer"):
        assert text in page, text
    assert client.get("/static/app_preview.js").status_code == 200
    assert client.get("/static/app_preview.css").status_code == 200
