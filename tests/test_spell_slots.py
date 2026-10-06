"""Phase A3: spell slot tracking — spending, refusing, reporting, restoring.

    A3.1  the slots have a type, a migration and a Token accessor
    A3.2  casting spends a slot and prints what is left
    A3.3  a cast with no slot of that level is refused, and costs nothing
    A3.4  a long rest restores every slot
    A3.5  a short rest restores what the class's features restore
    A3.6  `status` says how many slots are left, in one readable line
    A3.7  a caster with no table on their sheet gets one from class and level

Everything here is deterministic: dice come from the scripted roller in
tactics_fixtures, and the CLI tests run in-process against a temporary campaign
with the display switched off.
"""
from __future__ import annotations

import json
import sys

import pytest

from tests.tactics_fixtures import (RULES, ROOT, _RAW, _build, caster, encounter, frog,
                                    roller, start, state)
from tactics import cli, rest, schemas, slots, spells
from tactics.core import CombatError
from tactics.state import Encounter, Token

rules_mod = sys.modules[type(RULES).__module__]
sheet_mod = rules_mod._sheet_module()
srd_slots = sheet_mod._slots_module()
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")


# ─── builders ─────────────────────────────────────────────────────────────────

def wizard(level: int = 5, spent=(0, 0, 0), **extra) -> Token:
    """Kairos with a filled-in class and level, and slots to spend."""
    t = caster(spells=["Magic Missile", "Fire Bolt"], slots=4)
    t.extra.update(slots={"1": {"total": 4, "used": spent[0]},
                          "2": {"total": 3, "used": spent[1]},
                          "3": {"total": 2, "used": spent[2]}},
                   level=level, spellcasting={"class": f"Wizard {level}", "level": level})
    t.extra.update(extra)
    return t


def pact_warlock(level: int = 3) -> Token:
    t = caster(spells=["Eldritch Blast"], slots=0)
    t.extra.pop("slots")
    t.extra.update(level=level, spellcasting={"class": "Warlock", "level": level})
    slots.write(t, srd_slots.spent_table("warlock", level))
    return t


def board(*tokens) -> Encounter:
    """An encounter to hand to the rest calls, which take one rather than a fight."""
    enc = Encounter(campaign="t", grid={"name": "t", "rows": ["." * 6] * 6}, tokens={})
    for t in tokens:
        enc.tokens[t.id] = t
    return enc


def enc_with(*tokens) -> Encounter:
    return board(*tokens)


def for_me(*faces) -> "Roller":
    """A roller that will spend a player's hit dice, with every face scripted."""
    from tests.tactics_fixtures import ScriptedDice
    from tactics.roller import Roller
    return Roller(rng=ScriptedDice(*faces), for_me=True)


def fight(*tokens) -> Encounter:
    """A running fight, with the engine's own dice rather than the players'."""
    enc = start(encounter(list(tokens), roll_mode="auto"), [t.id for t in tokens])
    return enc


# ─── A3.1: the slots have a type, a migration and an accessor ─────────────────

def test_the_schema_types_a_slot_and_a_slot_table():
    assert schemas.SPELL_SLOTS.clean({"1": {"total": 2, "used": 0}}) == {"1": {"total": 2, "used": 0}}
    with pytest.raises(schemas.SchemaError, match="slot level 'x' is not a number"):
        schemas.SPELL_SLOTS.validate({"x": {"total": 1, "used": 0}}, "kairos")
    with pytest.raises(schemas.SchemaError, match="below 1"):
        schemas.SPELL_SLOTS.validate({"0": {"total": 1, "used": 0}}, "kairos")
    with pytest.raises(schemas.SchemaError, match="unknown key 'left'"):
        schemas.SPELL_SLOTS.validate({"1": {"left": 1}}, "kairos")


def test_a_bad_slot_table_is_named_when_the_file_is_saved(tmp_path):
    """The one closed shape inside `extra` is checked, so a typo is caught at
    the file boundary and not as a KeyError six commands into a fight."""
    t = wizard()
    t.extra["slots"] = {"1": {"totl": 2, "used": 0}}
    enc = Encounter(campaign="test", grid={"name": "t", "rows": ["." * 4] * 4}, tokens={t.id: t})
    enc.order, enc.round = [t.id], 1
    with pytest.raises(ValueError, match=r"kairos: spell slots.1: unknown key 'totl'"):
        state.save(enc, tmp_path / "encounter.json")


def test_a_token_exposes_its_slots_without_a_second_copy():
    t = wizard()
    assert t.spell_slots == t.extra["slots"]
    t.spell_slots = {"1": {"total": 4, "used": 1}, "2": {"total": 3, "used": 0}}
    assert t.extra["slots"]["1"] == {"total": 4, "used": 1}, "one value, two names"
    assert t.spell_slots["2"]["total"] == 3
    assert Token(id="f", name="F", side="pc", x=0, y=0, hp=1, max_hp=1, ac=10).spell_slots == {}


def test_slots_read_normalises_and_never_hands_out_the_stored_dict():
    t = Token(id="w", name="W", side="pc", x=0, y=0, hp=1, max_hp=1, ac=10)
    t.extra["slots"] = {"3": {"total": 2, "used": 1}, "10": {"total": 1, "used": 0},
                        "1": {"max": 4, "remaining": 3},        # the display's shape
                        "2": 3,                                 # a bare count
                        "junk": {"total": 1, "used": 0}}         # not a level
    assert slots.read(t) == {1: {"total": 4, "used": 1}, 2: {"total": 3, "used": 0},
                             3: {"total": 2, "used": 1}, 10: {"total": 1, "used": 0}}
    copy = slots.read(t)
    copy[1]["used"] = 99
    assert t.extra["slots"]["1"] == {"max": 4, "remaining": 3}, \
        "read() normalises into a copy; it never rewrites what is stored"


def test_more_slots_spent_than_exist_is_clamped_not_refused():
    """Only a hand-edited file can produce this, and the running fight is worth
    more than the typo."""
    t = Token(id="w", name="W", side="pc", x=0, y=0, hp=1, max_hp=1, ac=10)
    t.extra["slots"] = {"1": {"total": 2, "used": 7}}
    assert slots.read(t) == {1: {"total": 2, "used": 2}}
    assert slots.remaining(t, 1) == 0


# ─── A3.2: casting spends a slot and reports what is left ─────────────────────

def test_casting_spends_a_slot_and_reports_what_is_left():
    enc = fight(wizard(), frog("frog-1", (3, 0)))
    out = spells.cast(enc, roller(3), "kairos", "Magic Missile", ["frog-1"])
    assert out["slot"] == "1"
    assert out["text"].startswith("Kairos casts Magic Missile (level 1 slot).")
    assert slots.read(enc.tokens["kairos"])[1] == {"total": 4, "used": 1}
    assert out["slots"] == "Spell slots: 1st: 3/4, 2nd: 3/3, 3rd: 2/2"
    assert out["slots"] in out["text"], "what is left is on the line the GM reads"


def test_upcasting_spends_the_higher_slot_not_the_spells_own():
    enc = fight(wizard(spent=(4, 3, 0)), frog("frog-1", (3, 0)))
    out = spells.cast(enc, roller(3), "kairos", "Magic Missile", ["frog-1"], level=3)
    assert out["slot"] == "3"
    assert slots.read(enc.tokens["kairos"])[3] == {"total": 2, "used": 1}
    assert slots.read(enc.tokens["kairos"])[1] == {"total": 4, "used": 4}, "1sts stay spent"
    assert out["slots"] == "Spell slots: 1st: 0/4, 2nd: 0/3, 3rd: 1/2"


def test_a_reaction_spends_the_cheapest_slot_that_will_do():
    """Shield on a 3rd-level wizard with a 1st left spends the 1st (PHB p222)."""
    t = wizard(spent=(3, 1, 0))
    assert slots.lowest_with(t, 1) == "1"
    slots.spend(t, slots.lowest_with(t, 1))
    assert slots.read(t)[1] == {"total": 4, "used": 4}
    assert slots.read(t)[2] == {"total": 3, "used": 1}, "the 2nd is still there"
    assert slots.lowest_with(t, 1) == "2", "the next reaction takes the 2nd"


# ─── A3.3: a cast with no slot is refused, and costs nothing ─────────────────

def test_casting_with_no_slot_of_that_level_is_refused():
    enc = fight(wizard(spent=(4, 0, 2)), frog("frog-1", (3, 0)))
    before = slots.read(enc.tokens["kairos"])
    with pytest.raises(CombatError, match=r"no level 3 slot left \(left: level 2\)\."):
        spells.cast(enc, roller(3), "kairos", "Magic Missile", ["frog-1"], level=3)
    assert slots.read(enc.tokens["kairos"]) == before, "a refused cast costs nothing"
    assert not enc.turn.action_used, "and does not spend the action"


def test_the_refusal_names_the_levels_that_are_left():
    t = wizard(spent=(4, 0, 0))
    with pytest.raises(CombatError, match=r"left: level 2, 3\)\."):
        slots.check(t, 1)
    with pytest.raises(CombatError, match="no spell slots"):
        slots.check(Token(id="b", name="B", side="pc", x=0, y=0, hp=1, max_hp=1, ac=10), 1)


def test_a_creature_with_no_slots_cannot_be_told_it_has_spent_one():
    t = caster(spells=["Fire Bolt"])
    t.extra["slots"] = {}
    with pytest.raises(CombatError, match="no spell slots"):
        slots.check(t, 1)


# ─── A3.4: a long rest restores every slot ────────────────────────────────────

def test_a_long_rest_restores_every_slot():
    t = wizard(spent=(4, 3, 2))
    t.hp, t.temp_hp = 2, 5
    t.extra["hit_dice"] = {"die": "d6", "remaining": 0, "total": 5}
    t.conditions = ["frightened", "poisoned", "prone"]
    enc = board()
    enc.tokens[t.id] = t
    lines = rest.long_rest(enc)
    assert "Kairos: all spell slots restored." in lines
    assert slots.read(t) == {1: {"total": 4, "used": 0}, 2: {"total": 3, "used": 0},
                             3: {"total": 2, "used": 0}}
    assert (t.hp, t.temp_hp) == (8, 0)
    assert t.extra["hit_dice"]["remaining"] == 3, "half of five, rounded up (PHB p201)"
    assert t.conditions == ["prone"], "frightened and poisoned end; prone is the fight's"


def test_a_long_rest_re_arms_arcane_recovery():
    t = wizard(spent=(4, 0, 0))
    t.extra["arcane_recovery_used"] = True
    enc = board()
    enc.tokens[t.id] = t
    lines = rest.long_rest(enc)
    assert t.extra["arcane_recovery_used"] is False
    assert "Kairos: Arcane Recovery is available again." in lines


def test_hit_dice_are_the_players_unless_the_gm_says_otherwise():
    """Carried in from A2, and the reason `rest short` never surprises a player
    with fewer hit points than they expected."""
    t = wizard()
    t.hp = 2
    t.extra["hit_dice"] = {"die": "d6", "remaining": 2, "total": 5}
    t.extra["abilities"] = {"con": 14}
    assert rest.short_rest(enc_with(t), None, roller(6, 6))[0] == \
        "Kairos: 2/5 Hit Dice (d6 +2 each) available. Use `rest short --for-me` to spend them."
    assert t.hp == 2, "a player's hit points are the player's to roll"
    spent = rest.short_rest(enc_with(t), None, for_me(6, 6))
    assert "Kairos: spent 1 Hit Die (d6 +2), healed 6 HP (now 8/8)." in spent
    assert t.extra["hit_dice"]["remaining"] == 1, "it stops at full HP, not at zero dice"


def test_a_dead_creature_does_not_rest():
    t = wizard(spent=(4, 0, 0))
    t.dead, t.hp = True, 0
    assert rest.long_rest(enc_with(t)) == ["Kairos is dead; long rest has no effect."]
    assert slots.read(t)[1] == {"total": 4, "used": 4}


def test_exhaustion_goes_down_by_one_and_stops_at_zero():
    t = wizard()
    t.conditions = ["exhaustion"]
    t.extra["exhaustion_level"] = 2
    rest.long_rest(enc_with(t))
    assert t.conditions == ["exhaustion"] and t.extra["exhaustion_level"] == 1
    rest.long_rest(enc_with(t))
    assert t.conditions == [] and t.extra["exhaustion_level"] == 0


# ─── A3.5: a short rest restores what the class's features restore ────────────

def test_warlock_pact_magic_refills_on_a_short_rest():
    t = pact_warlock(3)
    assert slots.read(t) == {3: {"total": 3, "used": 0}}, "three 3rd-level pact slots"
    slots.spend(t, 3)
    slots.spend(t, 3)
    assert slots.short_rest(t) == ["Kairos: Pact Magic slots restored (2 back)."]
    assert slots.read(t) == {3: {"total": 3, "used": 0}}


def test_wizard_arcane_recovery_buys_back_up_to_half_the_level_once_per_day():
    t = wizard(level=5, spent=(4, 0, 2))
    line = slots.short_rest(t)[0]
    assert "Arcane Recovery recovered 3x 1st" in line and "up to 3 slot levels" in line
    assert slots.read(t) == {1: {"total": 4, "used": 1}, 2: {"total": 3, "used": 0},
                             3: {"total": 2, "used": 2}}
    assert slots.short_rest(t) == ["Kairos: Arcane Recovery is already used today."]
    enc = board()
    enc.tokens[t.id] = t
    rest.long_rest(enc)
    assert t.extra.get("arcane_recovery_used") is False, "a long rest is what re-arms it"


def test_arcane_recovery_spends_the_cheapest_slots_first():
    """The rule sets a ceiling, not a list: a level 8 wizard recovers 1sts and
    a 2nd before anything worth more, because that is what costs the caster
    least — and stops when the ceiling is reached, not when it runs out."""
    t = wizard(level=8, spent=(0, 0, 0))          # half of eight, rounded up: 4 levels
    t.extra["slots"] = {"1": {"total": 4, "used": 2}, "2": {"total": 3, "used": 2},
                        "3": {"total": 2, "used": 2}, "4": {"total": 1, "used": 1}}
    assert "recovered 2x 1st, 1x 2nd" in slots.short_rest(t)[0]
    assert slots.read(t)[1] == {"total": 4, "used": 0}
    assert slots.read(t)[2] == {"total": 3, "used": 1}
    assert slots.read(t)[3] == {"total": 2, "used": 2}, "the budget is spent by then"
    assert slots.read(t)[4] == {"total": 1, "used": 1}, "and the 4th was never in reach"


def test_a_level_one_wizard_has_no_arcane_recovery():
    t = wizard(level=1, spent=(2, 0, 0))
    t.extra["spellcasting"] = {"class": "Wizard", "level": 1}
    t.extra["slots"] = {"1": {"total": 2, "used": 2}}
    assert slots.short_rest(t) == [], "it is a 2nd-level feature (PHB p112)"


def test_a_sheet_that_lists_arcane_recovery_is_believed_over_the_level():
    t = wizard(level=1, spent=(2, 0, 0))
    t.extra["spellcasting"] = {"class": "Wizard", "level": 1}
    t.extra["slots"] = {"1": {"total": 2, "used": 2}}
    t.extra["features"] = ["Arcane Recovery", "Spellcasting"]
    assert "Arcane Recovery recovered 1x 1st" in slots.short_rest(t)[0]


def test_a_sorcerer_is_told_about_sorcery_points_rather_than_given_slots():
    """Sorcery Points are spent to make slots, not to get them back (PHB p43)."""
    t = wizard(spent=(0, 0, 0))
    t.extra["spellcasting"] = {"class": "Sorcerer 5", "level": 5}
    t.extra["sorcery_points"] = {"available": 4, "max": 5}
    line = slots.short_rest(t)[0]
    assert "4 Sorcery Points available" in line and "spent to create slots" in line
    assert slots.read(t)[1] == {"total": 4, "used": 0}, "the engine does not convert them"


def test_a_class_with_no_short_rest_recovery_is_left_alone():
    t = wizard(spent=(4, 0, 0))
    t.extra["spellcasting"] = {"class": "Cleric 5", "level": 5}
    assert slots.short_rest(t) == []


# ─── A3.6: `status` shows the slots ───────────────────────────────────────────

def test_status_shows_the_slots_left_in_one_readable_line():
    t = wizard(spent=(2, 1, 2))
    t.x, t.y = 0, 0
    enc = start(encounter([t, frog("frog-1", (3, 0))]), ["kairos", "frog-1"])
    out = cli.cmd_status(enc)
    assert "(Spell slots: 1st: 2/4, 2nd: 2/3, 3rd: 0/2)" in out
    assert "Frog 1 D1 18/18" in out, "a creature with no slots shows none"


def test_status_golden_text():
    """The whole line, so a change to the wording cannot pass unnoticed."""
    t = wizard(spent=(2, 1, 2))
    f = frog("frog-1", (3, 0))
    f.hp = 11
    f.add_condition("prone")
    enc = start(encounter([t, f]), ["kairos", "frog-1"])
    assert cli.cmd_status(enc) == (
        "Round 1, Kairos's turn (30 ft left, action ready).\n"
        "Kairos A1 8/8 (Spell slots: 1st: 2/4, 2nd: 2/3, 3rd: 0/2) | "
        "Frog 1 D1 11/18 [prone]")


def test_the_slots_line_sits_after_the_conditions_when_there_are_any():
    t = wizard(spent=(2, 0, 0))
    t.conditions = ["prone"]
    enc = start(encounter([t, frog("frog-1", (3, 0))]), ["kairos", "frog-1"])
    assert "Kairos A1 8/8 [prone] (Spell slots: 1st: 2/4, 2nd: 3/3, 3rd: 2/2)" in \
        cli.cmd_status(enc)


# ─── A3.7: a caster with no table gets one from class and level ───────────────

def test_a_sheet_with_no_slot_table_is_filled_from_class_and_level():
    sheet = KAIROS_MD.replace("| 1st | 2 | 0 |", "| 1st |  |  |")
    t = sheet_mod.read_sheet(sheet, "kairos", (0, 0))
    assert t.extra["slots"] == {"1": {"total": 2, "used": 0}}, "Wizard 1: two 1st-level slots"
    assert t.extra["spellcasting"] == {"class": "Wizard 1 / Fighter 1 (Chronurgy at 3)", "level": 1}


def test_a_sheet_with_a_table_is_never_overwritten():
    t = sheet_mod.read_sheet(KAIROS_MD, "kairos", (0, 0))
    assert t.extra["slots"] == {"1": {"total": 2, "used": 0}}


def test_a_non_casting_class_gets_no_slots_from_the_table():
    sheet = KAIROS_MD.replace("**Class:** Wizard 1 / Fighter 1 (Chronurgy at 3)", "**Class:** Fighter 3")
    sheet = sheet.replace("| 1st | 2 | 0 |", "| 1st |  |  |")
    t = sheet_mod.read_sheet(sheet, "kairos", (0, 0))
    assert t.extra["slots"] == {}
    assert t.extra["spellcasting"] is None


def test_a_homebrew_class_keeps_whatever_the_sheet_says():
    """No SRD table means no opinion: a campaign's own class is the sheet's
    business, and the engine fills in nothing rather than zeroing it."""
    sheet = KAIROS_MD.replace("**Class:** Wizard 1 / Fighter 1 (Chronurgy at 3)", "**Class:** Loremaster 3")
    t = sheet_mod.read_sheet(sheet, "kairos", (0, 0))
    assert t.extra["slots"] == {"1": {"total": 2, "used": 0}}


def test_the_srd_tables_themselves():
    assert srd_slots.for_level("wizard", 1) == {"1": 2}
    assert srd_slots.for_level("wizard", 3) == {"1": 4, "2": 2}
    assert srd_slots.for_level("wizard", 5) == {"1": 4, "2": 3, "3": 2}
    assert srd_slots.for_level("wizard", 17) == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2,
                                                 "6": 1, "7": 1, "8": 1, "9": 1}
    assert srd_slots.for_level("wizard", 20) == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 3,
                                                 "6": 2, "7": 2, "8": 1, "9": 1}
    assert srd_slots.for_level("paladin", 1) == {}, "a half caster starts at 2"
    assert srd_slots.for_level("paladin", 5) == {"1": 4, "2": 2}
    assert srd_slots.for_level("paladin", 20) == {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2}
    assert srd_slots.for_level("warlock", 3) == {"3": 3}, "count = level, level = level"
    assert srd_slots.for_level("warlock", 9) == {"5": 9}, "capped at 5th level (PHB p107)"
    assert srd_slots.for_level("fighter", 5) == {}
    assert srd_slots.for_level("wizard", 0) == {} and srd_slots.for_level("wizard", 21) == {}
    assert srd_slots.for_level("wizard", "five") == {}
    assert srd_slots.normalise_class("Wizard 1 / Fighter 1 (Chronurgy at 3)") == "wizard"
    assert srd_slots.normalise_class("Rogue 3 / Sorcerer 2") == "sorcerer"
    assert srd_slots.normalise_class("cook") == ""
    assert srd_slots.spent_table("wizard", 1) == {"1": {"total": 2, "used": 0}}


# ─── migration: a campaign that was already running keeps working ─────────────

def _v2_document() -> dict:
    """The file every live campaign has on disk today.

    A wizard who cast in the last session has {"1": {"remaining": 1, "max": 2}}
    in the shape the display has always spoken, and the level key is a number
    because something in the pipeline made it one.
    """
    return {
        "campaign": "old",
        "grid": {"name": "t", "rows": ["." * 3] * 3},
        "version": 2,
        "tokens": {
            "wiz": {
                "id": "wiz", "name": "Wiz", "side": "pc", "x": 0, "y": 0,
                "hp": 8, "max_hp": 8, "ac": 12,
                "extra": {"slots": {1: {"remaining": 1, "max": 2},
                                    "2": {"max": 3, "remaining": 3}}},
            },
        },
    }


def test_a_v2_encounter_file_with_display_shaped_slots_still_loads():
    enc = Encounter.from_dict(_v2_document())
    assert enc.version == state.SCHEMA_VERSION == 4
    assert slots.read(enc.tokens["wiz"]) == {1: {"total": 2, "used": 1},
                                             2: {"total": 3, "used": 0}}
    assert enc.tokens["wiz"].extra["slots"]["1"] == {"total": 2, "used": 1}
    slots.check(enc.tokens["wiz"], 1)               # the fight is still playable
    assert slots.remaining(enc.tokens["wiz"], 2) == 3


def test_migration_rewrites_the_file_only_when_the_fight_is_touched(tmp_path):
    path = tmp_path / "encounter.json"
    path.write_text(json.dumps(_v2_document()), encoding="utf-8")
    loaded = state.load(path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2, "reading never writes"
    state.save(loaded, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 4
    assert saved["tokens"]["wiz"]["extra"]["slots"] == {"1": {"total": 2, "used": 1},
                                                        "2": {"total": 3, "used": 0}}


def test_a_file_from_the_future_is_still_refused():
    with pytest.raises(ValueError, match="newer than this engine"):
        Encounter.from_dict({"campaign": "x", "grid": {"name": "t", "rows": ["."]},
                             "version": state.SCHEMA_VERSION + 1, "tokens": {}})


# ─── the rest command, end to end ────────────────────────────────────────────

@pytest.fixture
def camp(tmp_path, monkeypatch):
    """A temporary campaign with Kairos' sheet, the display switched off.

    The same fixture test_tactics_cli.py uses, restated here rather than
    imported: a test module's fixtures are its own, and reaching across into
    another one to get an environment is how a test starts passing because
    somebody reorganised a file.
    """
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(rules_mod, "_lookup_monster",
                        lambda name: _build._norm_monster(_RAW[name.lower().replace(" ", "-")]))
    return d


def run(capsys, *argv) -> tuple:
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def _enc_on_disk(camp, capsys, **slot_state):
    """Start a fight in a temporary campaign and give Kairos the given slots."""
    assert cli.main(["-c", "demo", "start", "frog-pond", "--pc", "Kairos@B7",
                     "--monster", "giant frog@J5", "--seed", "3", "--roll-mode", "auto"]) == 0
    capsys.readouterr()                            # the start text, not this test's
    path = camp / "combat" / "encounter.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["order"], saved["turn_index"], saved["turn"]["actor"] = ["kairos"], 0, "kairos"
    saved["tokens"]["kairos"]["extra"]["slots"] = slot_state
    path.write_text(json.dumps(saved), encoding="utf-8")
    return path


def _saved(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_rest_long_command_restores_slots_and_saves(camp, capsys):
    path = _enc_on_disk(camp, capsys, **{"1": {"total": 2, "used": 2}})
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "all spell slots restored." in out
    assert _saved(path)["tokens"]["kairos"]["extra"]["slots"] == {"1": {"total": 2, "used": 0}}
    assert _saved(path)["version"] == state.SCHEMA_VERSION


def test_rest_short_believes_the_sheet_over_the_srd_on_arcane_recovery(camp, capsys):
    """Kairos' sheet writes him Arcane Recovery at Wizard 1 ("1/day, short
    rest"), which the SRD gives at 2. The sheet wins: it is the character's
    actual sheet, and the engine is not here to overrule a GM's ruling."""
    path = _enc_on_disk(camp, capsys, **{"1": {"total": 2, "used": 2}})
    assert "Arcane Recovery" in sheet_mod.read_sheet(KAIROS_MD, "k", (0, 0)).extra["features"]
    code, out = run(capsys, "rest", "short", "--token", "kairos")
    assert code == 0 and "Arcane Recovery recovered 1x 1st" in out
    assert _saved(path)["tokens"]["kairos"]["extra"]["slots"] == {"1": {"total": 2, "used": 1}}
    code, out = run(capsys, "rest", "short", "--token", "kairos")
    assert "already used today" in out, "once per long rest, not once per short rest"


def test_rest_short_gives_a_warlock_their_pact_magic_back(camp, capsys):
    path = _enc_on_disk(camp, capsys, **{"3": {"total": 3, "used": 3}})
    saved = _saved(path)
    extra = saved["tokens"]["kairos"]["extra"]
    extra["spellcasting"] = {"class": "Warlock", "level": 3}
    extra["level"] = 3
    extra["features"] = ["Pact Magic"]
    path.write_text(json.dumps(saved), encoding="utf-8")
    code, out = run(capsys, "rest", "short", "--token", "kairos")
    assert code == 0 and "Pact Magic slots restored" in out
    assert _saved(path)["tokens"]["kairos"]["extra"]["slots"] == {"3": {"total": 3, "used": 0}}


def test_rest_writes_the_fight_back_and_keeps_it_playable(camp, capsys):
    path = _enc_on_disk(camp, capsys, **{"1": {"total": 2, "used": 2}})
    run(capsys, "rest", "short", "--token", "kairos")
    saved = _saved(path)
    assert saved["tokens"]["kairos"]["extra"]["hit_dice"]["remaining"] >= 0
    assert saved["status"] == "active", "a rest is not the end of the fight"
    code, out = run(capsys, "status")
    assert code == 0 and "Spell slots: 1st: 1/2" in out


def test_a_rest_with_nobody_to_rest_is_refused(camp, capsys):
    """#179 changed what this command needs: `rest` no longer requires a running
    fight, it reads the party's own sheets (see tests/test_rest_out_of_combat.py).
    The refusal that remains is the one with no party to name, and it has to be a
    message rather than a silent success healing nobody."""
    for sheet in (camp / "characters").glob("*.md"):
        sheet.unlink()
    code, out = run(capsys, "rest", "long")
    assert code == 1 and "No character sheets" in out


def test_the_rest_command_is_documented_in_the_help():
    text = cli.parser().format_help()
    assert "rest short|long" in text and "--token NAME" in text
