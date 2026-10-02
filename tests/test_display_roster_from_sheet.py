import importlib.util
import json
import pathlib
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_app():
    spec = importlib.util.spec_from_file_location(
        "gm_display_app_roster_sheet", REPO / "display" / "gm-display-app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sheet(name="Mira", hp="5 / 17", level="4", klass="Ranger"):
    return (
        f"# {name}\n\n## Identity\n- **Race:** Elf | **Class:** {klass} | **Level:** {level}\n\n"
        f"## Combat Stats\n- **HP:** {hp} | **Temp HP:** 2\n- **AC:** 15 | **Initiative:** +3 | **Speed:** 35 ft\n"
    )


def test_startup_roster_comes_from_campaign_sheets_not_stats_cache(monkeypatch, tmp_path):
    app = _import_app()
    campaign = tmp_path / "campaign"
    (campaign / "characters").mkdir(parents=True)
    (campaign / "npc-files").mkdir()
    (campaign / "characters" / "mira.md").write_text(_sheet(), encoding="utf-8")
    (campaign / "npc-files" / "guide.md").write_text(
        _sheet("Guide", "9 / 9", "2", "Wizard"), encoding="utf-8")
    monkeypatch.setattr(app, "CAMP_FILE", str(tmp_path / ".campaign"))
    pathlib.Path(app.CAMP_FILE).write_text("test-campaign", encoding="utf-8")
    monkeypatch.setattr(app, "_find_display_campaign", lambda _name: campaign)
    monkeypatch.setattr(app, "STATS_FILE", str(tmp_path / "stats.json"))
    pathlib.Path(app.STATS_FILE).write_text(json.dumps({
        "players": [{"name": "Mira", "level": 1, "hp": {"current": 8, "max": 8}},
                    {"name": "Stale", "level": 1}]
    }), encoding="utf-8")
    app._current_stats = {"players": [{"name": "old in-memory"}]}

    app._load_stats()

    players = {p["name"]: p for p in app._current_stats["players"]}
    assert set(players) == {"Mira", "Guide"}
    assert players["Mira"]["level"] == 4
    assert players["Mira"]["hp"] == {"current": 5, "max": 17, "temp": 2}
    assert players["Mira"]["ac"] == 15
    assert players["Guide"]["class"] == "Wizard"
    assert json.loads(pathlib.Path(app.STATS_FILE).read_text(encoding="utf-8"))["players"][0]["level"] == 1


def test_missing_and_unparseable_sheets_are_reported_without_defaults(monkeypatch, tmp_path):
    app = _import_app()
    campaign = tmp_path / "campaign"
    (campaign / "characters").mkdir(parents=True)
    (campaign / "characters" / "broken.md").write_text("# Broken\n", encoding="utf-8")
    monkeypatch.setattr(app, "CAMP_FILE", str(tmp_path / ".campaign"))
    pathlib.Path(app.CAMP_FILE).write_text("test-campaign", encoding="utf-8")
    monkeypatch.setattr(app, "_find_display_campaign", lambda _name: campaign)
    app._current_stats = {"players": [{"name": "Cached", "level": 1}]}

    app._load_stats()

    assert app._current_stats["players"] == []
    assert "broken.md" in app._current_stats["roster_error"]
    assert "HP" in app._current_stats["roster_error"]


def test_missing_campaign_sheets_are_reported(monkeypatch, tmp_path):
    app = _import_app()
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    monkeypatch.setattr(app, "CAMP_FILE", str(tmp_path / ".campaign"))
    pathlib.Path(app.CAMP_FILE).write_text("test-campaign", encoding="utf-8")
    monkeypatch.setattr(app, "_find_display_campaign", lambda _name: campaign)
    app._current_stats = {"players": [{"name": "Cached", "level": 1}]}

    app._load_stats()

    assert app._current_stats["players"] == []
    assert "No character sheets" in app._current_stats["roster_error"]
