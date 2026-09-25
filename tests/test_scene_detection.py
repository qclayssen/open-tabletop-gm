"""Which scene the display picks from the narration.

The opening of a new campaign at an inn ("the Last Lantern inn ... every
lantern along the road") read as "The Temple": "lantern" was a temple word and
keywords matched inside other words ("ale" in "pale", "bar" in "barely").
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "display" / "gm-display-app.py"


def _fresh_app():
    spec = importlib.util.spec_from_file_location("gm_display_app_scene", str(APP))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scene(text: str) -> str | None:
    app = _fresh_app()
    app._current_scene_name = "night"   # not a scene any of these texts means
    found = app._detect_scene(text)
    return found["name"] if found else None


def test_an_inn_full_of_lanterns_is_the_inn():
    text = ("Rain needles the shutters of the Last Lantern inn. Every lantern "
            "along the village road has gone dark tonight, and old Maddoc the "
            "lamplighter has not come home.")
    assert _scene(text) == "tavern"


def test_keywords_do_not_match_inside_other_words():
    # "pale" holds "ale", "barely" holds "bar", "dinner" holds "inn".
    assert _scene("A pale figure barely stirs before dinner.") is None


def test_plural_and_longer_forms_still_count():
    assert _scene("Candles gutter as the innkeepers pour ales.") == "tavern"
    assert _scene("The altars of the shrines are cold.") == "temple"
