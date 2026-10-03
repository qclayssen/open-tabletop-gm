"""Which numbers on a sheet are stated, which are derived, and which are a fallback.

WHY THESE TESTS EXIST
====================

Issue #138 asks for consequential numeric defaults to be explainable. The word that
carries the weight is *consequential*: a default that only changes a label does not need
explaining, and one that changes a die roll does. So the tests here are two-sided, and
the second side matters as much as the first:

  - a sheet that states everything must produce a token with NO consequential fallback
    reported, or the mechanism is noise and a GM learns to skip it;
  - a sheet that is silent must produce a token that says which number it guessed and
    why, in words that name what the number is used for;
  - a sheet that is PRESENT BUT UNREADABLE must refuse, with a message naming the field
    and the text. This is the case that used to be indistinguishable from the second.

The line between the second and the third is the whole change. "**AC:** TBD" is not an
absent AC, and treating it as one produced an authoritative-looking AC 10 on a
half-filled sheet.

NOT ADOPTED
===========

RI10 also asks to consider `ac_base` / `ac_dex_bonus` / `ac_max_bonus`, and this does
that as LABELS beside the integer `Token.ac`, with provenance for each part. It does not
adopt the external runtime that motivated RI10, and it does not change how any attack
roll is computed. `ac_parts` reports what the sheet says and, on the repository's own
fixture, what the PHB says instead, side by side; it does not correct the sheet, which
is campaign data.

`roller.average` is pinned unchanged, because it is the other number a "consequential
default" reaches: the expected damage `spells.preview` prints for every target in an
area. A change to how a sheet's numbers are derived must not move it.
"""
from __future__ import annotations

import json
import re
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "scripts", ROOT / "tests", str(ROOT / "systems" / "dnd5e")):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import tactics_sheet as sheet                              # noqa: E402
from tactics.roller import average                         # noqa: E402
from tests.tactics_fixtures import _RAW, _build, caster, encounter, roller, start  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")

MINIMAL = """# Blank
**Level:** 1
**HP:** 4 / 4
**Speed:** 30 ft
"""


def read(text, name="pc", pos=(0, 0)):
    return sheet.read_sheet(text, name, pos)


def blank(text, *labels):
    """Blank a `**Label:** value` field in place, so the sheet is SILENT about it.

    The value, not the line: the fixture sheet packs AC, Initiative, Speed and Hit
    Dice onto one line, so removing the line would take four fields with it and the
    test would pass for the wrong reason. This is also the shape a blank template
    actually has.
    """
    for label in labels:
        text = re.sub(rf"\*\*{re.escape(label)}:\*\*\s*[^|\n]*", f"**{label}:**", text)
    return text


def drop_table(text, title):
    """Remove a whole `## Title` section, so a table the parser reads is not there."""
    return re.sub(rf"^##\s+{re.escape(title)}.*?$(.*?)(?=^##\s|\Z)", "", text,
                   flags=re.M | re.S)


# ── a sheet that states everything reports nothing ────────────────────────────

def test_the_fixture_sheet_states_everything_consequential():
    """The negative case, and the reason `explain()` is a function and not a warning.
    A parser that always warns teaches a GM to skip it."""
    token = read(FIXTURE, "Kairos")
    derived = token.extra["derived"]
    for field in ("ac", "speed", "level", "saves", "attack_bonus"):
        assert derived[field] == sheet.STATED, f"{field} is {derived[field]}"
    # dex_mod is DERIVED on a sheet that states its scores: nothing is stated about
    # the modifier itself, and calling that "stated" would be the same blur.
    assert derived["dex_mod"] == sheet.DERIVED
    assert sheet.explain(token) == []


def test_the_fixture_sheet_is_read_exactly_as_before():
    """Nothing about the numbers moved. AC 12, DEX +2, speed 30, Fire Bolt +6."""
    token = read(FIXTURE, "Kairos")
    assert token.ac == 12 and token.speed == 30 and token.dex_mod == 2
    assert token.extra["level"] == 1 and token.saves["int"] == 6
    fire = next(a for a in token.attacks if a["name"] == "Fire Bolt")
    assert fire["bonus"] == 6 and fire["damage"] == [{"dice": "1d10", "type": "fire"}]


# ── a silent field falls back, and says so ───────────────────────────────────

@pytest.mark.parametrize("field,label,value", [
    ("ac", "AC", 10),
    ("speed", "Speed", 30),
    ("level", "Level", 1),
])
def test_a_silent_field_falls_back_to_its_documented_default(field, label, value):
    """Absent is not malformed. The fallback is applied, and the token says so."""
    token = read(blank(FIXTURE, label), "Kairos")
    got = {"ac": token.ac, "speed": token.speed, "level": token.extra["level"]}[field]
    assert got == value, f"{field} is {got}, not the documented {value}"
    assert token.extra["derived"][field] == sheet.DEFAULT
    assert any(field in line for line in sheet.explain(token)), sheet.explain(token)


def test_a_silent_explained_line_says_what_the_number_is_used_for():
    """"Explainable" is not "labelled". The line has to name what the number decides,
    or a GM reading it cannot tell whether it matters."""
    token = read(blank(FIXTURE, "AC"), "Kairos")
    (line,) = sheet.explain(token)
    assert "ac is 10" in line
    assert "attack roll" in line, line
    assert "Kairos" in line, "the line must say whose sheet it is"


def test_only_consequential_fallbacks_are_explained():
    """The line #138 draws. A `temp_hp` of 0 is the truth about a character with no
    temporary hit points, and explaining it would bury the AC."""
    token = read(blank(FIXTURE, "AC", "Temp HP"), "Kairos")
    said = " ".join(sheet.explain(token))
    assert "temp" not in said.lower()
    assert "ac" in said.lower()


def test_a_sheet_with_no_saving_throws_table_reports_a_zero_save_fallback():
    """Both tables gone: the saves are a real +0 on every save, which is the strongest
    case for explaining one."""
    bare = drop_table(drop_table(FIXTURE, "Saving Throws"), "Ability Scores")
    token = read(bare, "Kairos")
    assert token.saves == {} or set(token.saves.values()) == {0}
    assert token.extra["derived"]["saves"] == sheet.DEFAULT
    assert any("every save" in line for line in sheet.explain(token)), sheet.explain(token)


def test_a_sheet_with_no_ability_scores_reports_a_zero_dex_fallback():
    bare = drop_table(FIXTURE, "Ability Scores")
    token = read(bare, "Kairos")
    assert token.dex_mod == 0 and token.extra["derived"]["dex_mod"] == sheet.DEFAULT
    assert any("Mage Armor" in line for line in sheet.explain(token))


def test_an_empty_attack_bonus_column_is_a_default_not_a_zero_statement():
    """A blank cell in the bonus column is an unfilled template, not a character with a
    +0 attack. The number is the same; the provenance is not."""
    text = FIXTURE.replace("| Fire Bolt | +6 |", "| Fire Bolt | |", 1)
    token = read(text, "Kairos")
    fire = next(a for a in token.attacks if a["name"] == "Fire Bolt")
    assert fire["bonus"] == 0
    assert token.extra["derived"]["attack_bonus"] == sheet.DEFAULT
    clean = read(FIXTURE, "Kairos")
    assert clean.extra["derived"]["attack_bonus"] == sheet.STATED


def test_an_explicit_zero_is_stated_not_defaulted():
    """"Distinguish explicit zero from unknown" is half the acceptance criteria, and
    an explicit +0 attack is a real character build."""
    text = FIXTURE.replace("| Fire Bolt | +6 |", "| Fire Bolt | +0 |", 1)
    token = read(text, "Kairos")
    fire = next(a for a in token.attacks if a["name"] == "Fire Bolt")
    assert fire["bonus"] == 0
    assert token.extra["derived"]["attack_bonus"] == sheet.STATED
    assert "attack_bonus" not in " ".join(sheet.explain(token))


def test_an_absence_that_has_no_defensible_number_stays_none():
    """The fourth answer. A missing spell DC is `None`, and `tactics_spells.resolve`
    already refuses with the caster's name rather than guessing one."""
    # The fixture states its DC in a prose line ("*Spell save DC 14, spell attack +6.*"),
    # not in a `**Label:**` field, so the removal targets the pattern the parser reads.
    text = re.sub(r"Spell save DC\s*\d+", "", FIXTURE)
    assert not re.search(r"spell save DC\s*\d+", text, re.I)
    token = read(text, "Kairos")
    assert token.extra["spell_dc"] is None
    assert token.extra["derived"]["spell_dc"] == "missing"


# ── a present-but-unreadable field refuses ───────────────────────────────────

@pytest.mark.parametrize("label,field", [("AC", "ac"), ("Speed", "speed"),
                                         ("Level", "level")])
def test_a_present_but_unreadable_field_is_refused_with_an_actionable_message(label, field):
    """The case that used to be indistinguishable from an absent one. `_int("TBD", 10)`
    produced an authoritative-looking AC 10 on a half-filled sheet."""
    text = re.sub(rf"\*\*{label}:\*\*\s*[^|\n]*", f"**{label}:** TBD", FIXTURE, count=1)
    with pytest.raises(ValueError) as caught:
        read(text, "Kairos")
    message = str(caught.value)
    assert "Kairos" in message and f"**{label}:**" in message and "TBD" in message
    assert sheet.DEFAULTS[field][2] in message, (
        "the refusal must say what the number would have decided, or the GM cannot "
        "tell whether it matters")


def test_a_dash_where_a_number_belongs_is_refused():
    text = re.sub(r"\*\*AC:\*\*\s*[^|\n]*", "**AC:** ---", FIXTURE, count=1)
    with pytest.raises(ValueError):
        read(text, "Kairos")


def test_an_absent_field_is_not_malformed_so_it_still_falls_back():
    """The other side of the same line, and the reason the change is safe: a working
    sheet that simply omits a field is not now an error."""
    token = read(blank(FIXTURE, "AC"), "Kairos")
    assert token.ac == 10


def test_a_minimal_sheet_still_loads_with_every_consequential_fallback_labelled():
    token = read(MINIMAL, "Blank")
    assert token.ac == 10 and token.speed == 30 and token.extra["level"] == 1
    said = " ".join(sheet.explain(token))
    for field in ("ac", "dex_mod", "saves"):
        assert field in said, f"{field} is not explained: {said}"
    # No Attacks table at all is neither a stated bonus nor a defaulted one, so the
    # field is absent rather than reported as a source for a number nobody has.
    assert "attack_bonus" not in token.extra["derived"]


# ── the defaults table is the explanation, and it is complete ─────────────────

def test_every_field_the_parser_labels_is_in_the_defaults_table():
    """Otherwise a labelled default has no reason and `explain()` would crash or skip
    it, which is the failure mode a table is supposed to prevent."""
    for text in (FIXTURE, blank(drop_table(drop_table(FIXTURE, "Saving Throws"),
                                           "Ability Scores"), "AC"), MINIMAL):
        for field in read(text).extra["derived"]:
            assert field in sheet.DEFAULTS, f"{field} has no DEFAULTS entry"


def test_every_defaults_row_says_what_the_number_decides():
    for field, (_value, consequential, why) in sheet.DEFAULTS.items():
        assert why and len(why) > 20, f"{field}: {why!r}"
        assert isinstance(consequential, bool)


# ── RI10: the AC decomposition, as labels ────────────────────────────────────

def test_the_ac_is_decomposed_beside_the_integer_the_engine_uses():
    parts = sheet.ac_parts(FIXTURE)
    assert parts["base"] == 12 and parts["stated_with"] == 13
    assert parts["dex_bonus"] == 2, "DEX 14 is +2"
    # The fixture says "13 with Mage Armor" where PHB p.144 gives 13 + DEX = 15, so
    # the parenthetical contributes -2 over the rule. Reported, not corrected.
    assert parts["max_bonus"] == -2
    assert "15" in parts["note"], parts["note"]
    assert read(FIXTURE).ac == 12, "the integer every attack roll uses is unchanged"


def test_a_sheet_with_no_parenthetical_reports_no_bonus_rather_than_an_unknown():
    parts = sheet.ac_parts("# X\n**AC:** 14\n")
    assert parts["base"] == 14 and parts["stated_with"] is None and parts["max_bonus"] == 0
    assert parts["provenance"] == sheet.STATED


def test_a_sheet_with_no_ac_line_is_reported_as_the_unarmoured_fallback():
    parts = sheet.ac_parts("# X\n**HP:** 1 / 1\n")
    assert parts["provenance"] == "missing" and parts["base"] is None
    assert "unarmoured" in parts["note"]


def test_the_decomposition_travels_on_the_token_for_provenance():
    token = read(FIXTURE, "Kairos")
    assert token.extra["ac_parts"]["base"] == token.ac
    json.dumps(token.extra["ac_parts"])          # it has to survive an encounter save


def test_no_attack_chance_moved():
    """The blast radius of the whole change, checked rather than asserted. The AC a
    token carries is the AC `hit_chance` reads, and it is the same number it was."""
    token = read(FIXTURE, "Kairos")
    assert token.ac == 12 and token.extra["ac_note"] == "12 (13 with Mage Armor)"


# ── roller.average is untouched ──────────────────────────────────────────────

@pytest.mark.parametrize("notation,mean", [
    ("1d6", 3.5), ("2d6", 7.0), ("1d8+2", 6.5), ("3", 3.0), ("", 0.0),
    ("not dice at all", 0.0),
])
def test_roller_average_is_unchanged(notation, mean):
    """The other number a "consequential default" reaches: the expected damage
    `spells.preview` prints for every target in an area. It never raises, and an
    unreadable string contributes 0."""
    assert average(notation) == mean


def test_the_expected_damage_in_a_preview_still_comes_from_average():
    """End to end, so the pinning is not just of the helper."""
    from tactics import spells
    enc = encounter([caster(spells=["Fireball"])], rows=["." * 20] * 20)
    out = spells.preview(enc, "kairos", "Fireball", "D4")
    for row in out["affected"]:
        assert row["expected"] > 0
        assert row["expected"] == pytest.approx(average("8d6"), rel=0.01)


# ── the sparse-monster defaults, characterised ───────────────────────────────

def test_a_sparse_monster_gets_the_documented_fallbacks_and_nothing_else():
    """`token_from_monster` is in `tactics_rules.py`, which is a shared system file,
    so the sparse-monster defaults are CHARACTERISED here rather than instrumented
    there. Two of them are consequential and both are silent:

      - an ability the SRD record omits becomes 10, i.e. +0, which is a real save
        bonus and a real Mage Armor AC;
      - a record with no walk speed walks 30 ft, which is the whole movement budget.

    `record["ac"]` is NOT defaulted: it raises, so a monster's AC is never invented.
    """
    # The module-level builder takes a record; `Rules.token_from_monster` takes a
    # NAME and goes through the SRD lookup, which is a different code path and would
    # quietly re-add the ability this fixture removed.
    from tests.tactics_fixtures import token_from_monster
    rec = _build._norm_monster(_RAW["giant-frog"])
    sparse = dict(rec)
    sparse.pop("dex", None)
    sparse.pop("saves", None)
    token = token_from_monster(sparse, "sparse-1", "Sparse Frog", (0, 0))
    assert token.dex_mod == 0, "an absent DEX becomes 10, i.e. +0"
    assert token.saves["dex"] == 0
    assert token.speed == 30, "an absent walk speed becomes 30 ft"
    assert "Mage Armor" in DEFAULTS_REASON("dex_mod")
    with pytest.raises(KeyError):
        token_from_monster({k: v for k, v in sparse.items() if k != "ac"},
                           "no-ac", "No AC", (0, 0))


def DEFAULTS_REASON(field):
    return sheet.DEFAULTS[field][2]


def test_a_monster_that_states_an_ability_keeps_it():
    from tests.tactics_fixtures import token_from_monster
    rec = _build._norm_monster(_RAW["giant-frog"])
    token = token_from_monster(rec, "frog-1", "Frog", (0, 0))
    assert token.dex_mod == (rec["dex"] - 10) // 2
    assert token.saves["dex"] == token.dex_mod, (
        "a stated ability must reach both the modifier and the save")


def test_the_new_text_uses_no_em_dash():
    """The fork bans them in docs, comments and UI text.

    Scoped to the lines this change added rather than the whole module: the
    pre-existing prose in `tactics_sheet.py` carries one in a comment about spell
    slots, and rewriting a comment nobody touched would put an unrelated hunk in a
    diff about numeric defaults.
    """
    added = [ln for ln in (ROOT / "systems" / "dnd5e" / "tactics_sheet.py")
             .read_text(encoding="utf-8").splitlines()
             if "unarmoured creature" in ln or "DEFAULTS" in ln or "ac_parts" in ln]
    assert added, "the marker lines moved, so this check is no longer looking at them"
    assert not any("\u2014" in ln for ln in added)
    assert "\u2014" not in pathlib.Path(__file__).read_text(encoding="utf-8")