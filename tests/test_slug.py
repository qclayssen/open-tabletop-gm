"""Shared slug contract tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from slug import slug
from map_to_chartdown import slug as chartdown_slug
from map_to_atlas import slug as atlas_slug, token_image_name
from tactics import formations, maps, scenes


def test_ascii_slug_collapses_separators_and_lowercases():
    assert slug("  Firejolt Café... Rooftops  ") == "firejolt-caf-rooftops"


def test_slug_drops_non_ascii_and_returns_empty_for_no_ascii_words():
    assert slug("Æther") == "ther"
    assert slug("東京") == ""


def test_slug_coerces_non_strings():
    assert slug(None) == "none"


def test_migrated_call_sites_share_the_canonical_rule():
    value = "  Firejolt Café... Rooftops  "
    expected = "firejolt-caf-rooftops"
    assert chartdown_slug(value) == expected
    assert atlas_slug(value) == expected
    assert scenes.slug(value) == expected
    assert formations.slug(value) == expected
    assert maps._slug(value) == expected
    assert token_image_name("!!!") == "token.png"
