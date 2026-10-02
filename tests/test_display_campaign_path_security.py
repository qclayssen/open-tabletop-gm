"""Campaign names must never escape the campaign root through display paths."""

import pathlib

import pytest

from tests._browser import load_display_app


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    root = tmp_path / "gm-root"
    (root / "campaigns").mkdir(parents=True)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    mod = load_display_app("gm_display_campaign_path_security")
    mod.CAMP_FILE = str(tmp_path / ".campaign")
    mod._LOG_FALLBACK = str(tmp_path / "fallback-log.json")
    return mod


@pytest.mark.parametrize("name", ["../outside", str(pathlib.Path('/tmp/outside')), "bad\x00name"])
def test_log_file_rejects_unsafe_campaign_name(app_module, tmp_path, name):
    app_module.pathlib.Path(app_module.CAMP_FILE).write_text(name, encoding="utf-8")

    assert app_module._get_log_file() == app_module._LOG_FALLBACK
    assert not (tmp_path / "outside" / "text_log.json").exists()


@pytest.mark.parametrize("name", ["../outside", str(pathlib.Path('/tmp/outside')), "bad\x00name"])
def test_tail_file_rejects_unsafe_campaign_name(app_module, name):
    app_module.pathlib.Path(app_module.CAMP_FILE).write_text(name, encoding="utf-8")

    assert app_module._get_tail_file() is None


@pytest.mark.parametrize("name", ["../outside", str(pathlib.Path('/tmp/outside')), "bad\x00name"])
def test_chunk_rejects_unsafe_campaign_name(app_module, name):
    response = app_module.app.test_client().post("/chunk", json={"campaign": name})

    assert response.status_code == 400
    assert not app_module.pathlib.Path(app_module.CAMP_FILE).exists()


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "bad" + chr(0) + "name"])
def test_narrator_voice_rejects_unsafe_campaign_name(app_module, monkeypatch, name):
    class TTS:
        VALID_VOICES = {"alloy"}

    app_module._tts = TTS()
    app_module._active_campaign_name = lambda: name
    monkeypatch.setattr(app_module, "_find_campaign", lambda name: pytest.fail("resolved unsafe name"))

    assert app_module._write_narrator_voice("alloy") is False


def test_valid_campaign_name_resolves_under_campaigns(app_module, tmp_path):
    resolved = app_module._campaign_dir_for_name("test-campaign_1")

    assert resolved == (tmp_path / "gm-root" / "campaigns" / "test-campaign_1").resolve()


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "bad" + chr(0) + "name"])
def test_tactics_rejects_unsafe_campaign_name_before_subprocess(app_module, monkeypatch, name):
    app_module._active_campaign_name = lambda: name
    monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: pytest.fail("spawned tactics"))

    code, output = app_module._run_tactics(["status"])

    assert code != 0
    assert "campaign" in output.lower()
