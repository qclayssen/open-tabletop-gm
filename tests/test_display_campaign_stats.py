"""A display roster belongs to its campaign, not the display process."""

import json

import pytest

from tests._browser import load_display_app


@pytest.fixture
def display(tmp_path, monkeypatch):
    root = tmp_path / "gm-root"
    campaigns = root / "campaigns"
    campaigns.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (campaigns / name).mkdir()
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("GM_STATS_FILE", str(tmp_path / "fallback-stats.json"))
    app = load_display_app("gm_display_campaign_stats")
    app.CAMP_FILE = str(tmp_path / ".campaign")
    app._campaign_dir_for_name = lambda name: campaigns / name
    return app


def register(app, campaign):
    response = app.app.test_client().post("/chunk", json={"campaign": campaign})
    assert response.status_code == 204


def test_roster_is_saved_and_restored_per_campaign(display, tmp_path):
    alpha_stats = {"players": [{"name": "Kairos"}]}
    beta_stats = {"players": [{"name": "Mira"}]}

    register(display, "alpha")
    display._current_stats.update(alpha_stats)
    display._persist_stats()

    register(display, "beta")
    display._current_stats.update(beta_stats)
    display._persist_stats()

    saved_alpha = json.loads((tmp_path / "gm-root/campaigns/alpha/stats.json").read_text(
        encoding="utf-8"))
    saved_beta = json.loads((tmp_path / "gm-root/campaigns/beta/stats.json").read_text(
        encoding="utf-8"))
    assert {key: value for key, value in saved_alpha.items()
            if key != "system_version"} == alpha_stats
    assert {key: value for key, value in saved_beta.items()
            if key != "system_version"} == beta_stats

    register(display, "alpha")
    assert {key: value for key, value in display._current_stats.items()
            if key != "system_version"} == alpha_stats


def test_clearing_one_campaign_does_not_delete_another_roster(display, tmp_path):
    alpha_roster = tmp_path / "gm-root/campaigns/alpha/stats.json"
    beta_roster = tmp_path / "gm-root/campaigns/beta/stats.json"
    alpha_roster.write_text('{"players": [{"name": "Kairos"}]}', encoding="utf-8")
    beta_roster.write_text('{"players": [{"name": "Mira"}]}', encoding="utf-8")

    register(display, "alpha")
    display._do_clear()

    assert not alpha_roster.exists()
    assert json.loads(beta_roster.read_text(encoding="utf-8")) == {
        "players": [{"name": "Mira"}]
    }
