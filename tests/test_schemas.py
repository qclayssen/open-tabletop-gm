"""Field types, the Token document contract, and the schema migration.

The rule these tests exist to protect: the engine refuses to save a fight it
cannot trust, so a value that does not fit its field has to be refused at the
file boundary, with the field named, whether it came from a sheet reader, a GM
command or a hand edit. Everything else here (A4.1 to A4.4) is detail in service
of that.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from tactics import schemas
from tactics.schemas import (
    AnyField,
    BooleanField,
    DictField,
    FormulaField,
    ListField,
    NumberField,
    OptionalField,
    SchemaError,
    SchemaField,
    SetField,
    StringField,
)
from tactics.state import Encounter, Token

from tests.tactics_fixtures import caster, encounter, frog, kairos, open_map, state

# ─── A4.1 field types ─────────────────────────────────────────────────────────

def test_number_field_coerces_text_and_bounds_the_value():
    f = NumberField(min=0, max=20, integer=True)
    assert f.clean("18") == 18, "a sheet reader hands over text as often as numbers"
    assert f.clean(0) == 0, "0 is inside the bounds"
    with pytest.raises(SchemaError, match="below the minimum of 0"):
        f.clean(-1)
    with pytest.raises(SchemaError, match="above the maximum of 20"):
        f.clean(21)
    with pytest.raises(SchemaError, match="whole number"):
        f.clean(1.5)
    with pytest.raises(SchemaError, match="expected a number"):
        f.clean("eighteen")


def test_number_field_refuses_a_bool_where_a_number_belongs():
    """bool is an int in Python, and hp: True must not become hp: 1."""
    with pytest.raises(SchemaError, match="expected a number"):
        NumberField(integer=True).clean(True)


def test_a_failed_coercion_is_reported_as_a_type_not_a_crash():
    """NumberField.coerce leaves unparseable text alone so validate() can name it."""
    assert NumberField().coerce("eighteen") == "eighteen"


def test_string_field_choices_pattern_and_normalising():
    with pytest.raises(SchemaError, match="not one of pc, ally, enemy, neutral"):
        StringField(choices=("pc", "ally", "enemy", "neutral")).clean("villain")
    with pytest.raises(SchemaError, match="does not match"):
        StringField(pattern=r"^\d+d\d+").clean("lots")
    conds = StringField(strip=True, lower=True)
    assert conds.clean("  Prone ") == "prone", "conditions are matched case-insensitively"
    assert StringField().clean(" Kairos ") == " Kairos ", \
        "a name is left exactly as written: no trimming, and no refusal either, \
so a save that loads today still loads"


def test_boolean_field_understands_the_usual_spellings():
    f = BooleanField()
    for text in ("yes", "Y", "1", "true", True):
        assert f.clean(text) is True
    for text in ("no", "N", "0", "false", False):
        assert f.clean(text) is False
    with pytest.raises(SchemaError, match="expected true or false"):
        f.clean("maybe")


def test_set_field_drops_duplicates_and_checks_each_item():
    f = SetField(StringField(strip=True, lower=True))
    assert f.clean(["prone", "Prone", "poisoned"]) == ["prone", "poisoned"], \
        "a condition that printed twice is a condition applied twice"
    with pytest.raises(SchemaError, match=r"\[1\]"):
        f.clean(["prone", 3])
    with pytest.raises(SchemaError, match="expected a list"):
        f.clean("prone")


def test_list_field_keeps_order_and_checks_items():
    f = ListField(NumberField(integer=True))
    assert f.clean(["2", 1]) == [2, 1]
    with pytest.raises(SchemaError, match=r"\[0\]"):
        f.clean(["two"])


def test_dict_field_checks_values_and_leaves_keys_free():
    f = DictField(NumberField(integer=True))
    assert f.clean({"dex": "2"}) == {"dex": 2}
    with pytest.raises(SchemaError, match=r"\.perception"):
        f.clean({"perception": "high"})
    with pytest.raises(SchemaError, match="expected an object"):
        f.clean([("dex", 2)])


def test_any_field_accepts_free_form_and_refuses_what_json_cannot_hold():
    """extra, attacks and effects are written by the rules layer and gain keys
    at runtime; a closed schema there would reject a live campaign."""
    d = {"abilities": {"dex": 15}, "slots": {"1": {"used": 0}}, "traits": ["Pack Tactics"]}
    AnyField().validate(d)
    with pytest.raises(SchemaError, match="cannot be stored as JSON"):
        AnyField().validate(object(), "extra")
    with pytest.raises(SchemaError, match="key 1 is not a string"):
        AnyField().validate({1: "one"}, "extra")


def test_optional_field_keeps_none_distinct_from_zero():
    """TurnState.base_speed reads None as "no adjustment" and 0 as "frozen".
    A field that coerced None to 0 would freeze every creature in every old save."""
    f = OptionalField(NumberField(integer=True))
    assert f.clean(None) is None
    assert f.clean("0") == 0
    with pytest.raises(SchemaError, match="expected a number"):
        f.clean("fast")


def test_schema_field_reports_the_path_and_refuses_an_unknown_key():
    f = SchemaField({"hp": NumberField(min=0), "spells": ListField(AnyField())},
                    required=("hp",))
    f.clean({"hp": 1, "spells": []})
    with pytest.raises(SchemaError, match=r"extra\.hp: -1 is below the minimum of 0"):
        f.clean({"hp": -1}, "extra")
    with pytest.raises(SchemaError, match="missing the required field 'hp'"):
        f.clean({}, "extra")
    with pytest.raises(SchemaError, match="unknown field 'max_hp' \\(known: hp, spells\\)"):
        f.clean({"hp": 1, "max_hp": 9}, "extra")


def test_schema_field_can_be_told_to_keep_unknown_keys():
    """`allow_unknown` has two halves and the old body checked one of them.

    It proved the field stops raising, which was worth having. It never checked
    that the unknown key *survives*, which is the half the test's name claims
    and the half a caller relies on: a GM note a schema does not model has to
    come back out of `clean()` or it is dropped on the next save.

    Preservation turns out to be unconditional. `coerce` starts from
    `dict(value)` and only overwrites the keys it knows, and `validate` is what
    consults `allow_unknown`. So a `coerce` rewritten to filter down to the
    declared fields would raise nothing, preserve nothing, and pass the old body
    untouched. Asserting the returned dict is what pins it.
    """
    f = SchemaField({"hp": NumberField()}, allow_unknown=True)
    out = f.clean({"hp": 1, "note": "GM added this", "conditions": ["prone"]})
    assert out == {"hp": 1, "note": "GM added this", "conditions": ["prone"]}
    # And the same field still refuses the key it was told about, so "keep" is
    # not the same as "ignore the setting": the flag is the only difference
    # between the two calls above and the refusal the sibling test pins.
    with pytest.raises(SchemaError, match="unknown field 'note'"):
        SchemaField({"hp": NumberField()}).clean({"hp": 1, "note": "x"})


def test_formula_field_evaluates_over_a_closed_set_of_names():
    f = FormulaField("die_average + con_mod * (level - 1)",
                     variables=("die_average", "con_mod", "level"))
    assert f.evaluate({"die_average": 5, "con_mod": 2, "level": 3}) == 9
    assert f.evaluate({"die_average": 6, "con_mod": -1, "level": 1}) == 6
    with pytest.raises(SchemaError, match="needs level"):
        f.evaluate({"die_average": 5, "con_mod": 2})


@pytest.mark.parametrize("expr", [
    "__import__('os').system('true')",
    "open('/etc/passwd')",
    "die_average.real",
    "die_average if die_average else 0",
    "lambda: 1",
])
def test_formula_field_is_not_an_eval(expr):
    """A FormulaField's own expression is a constant, but the same evaluator is
    available for a formula out of a sheet, which is untrusted text."""
    f = FormulaField(expr, variables=("die_average",))
    with pytest.raises(SchemaError):
        f.evaluate({"die_average": 5})


def test_formula_field_accepts_an_unresolved_dice_string():
    """The sheet reader stores "2d6+3" before anything rolls it: an intermediate
    state, not a malformed one."""
    f = FormulaField("level", variables=("level",))
    f.validate("2d6+3", "extra.damage")
    with pytest.raises(SchemaError, match="expected a number"):
        f.validate("lots of damage", "extra.damage")


def test_ability_mod_floors_and_die_average_rounds_up():
    """Two different roundings, deliberately not shared: 8 is -1, and the
    average of a d8 is 5."""
    assert [schemas.ability_mod(s) for s in (8, 9, 10, 15, 16, 17)] == [-1, -1, 0, 2, 3, 3]
    assert [schemas.die_average(d) for d in ("1d6", "1d8", "1d10", "1d12")] == [3, 4, 5, 6]
    assert schemas.die_average("") == 5, "an unknown die averages as a d10, not a crash"


# ─── A4.2 the Token document ──────────────────────────────────────────────────

def test_the_schema_describes_exactly_the_dataclass_fields():
    """A field with no description is a value that reaches the file unchecked,
    and a description for a field that does not exist hides a rename."""
    assert set(schemas.TOKEN_SCHEMA.fields) == {f.name for f in dataclasses.fields(Token)}
    assert schemas.TOKEN_SCHEMA.required == ("id", "name", "side", "x", "y", "hp", "max_hp", "ac")


def test_token_from_dict_coerces_what_a_sheet_reader_writes():
    d = kairos().to_dict()
    d["hp"], d["speed"], d["conditions"] = "6", "30", ["Prone", "poisoned", "prone"]
    t = Token.from_dict(d)
    assert (t.hp, t.speed, t.conditions) == (6, 30, ["prone", "poisoned"])


def test_token_from_dict_names_the_field_that_does_not_fit():
    d = kairos().to_dict()
    d["saves"]["dex"] = "good"
    with pytest.raises(SchemaError, match="saves.dex"):
        Token.from_dict(d)


def test_token_from_dict_refuses_a_misspelt_field_rather_than_dropping_it():
    """Token(**d) would raise a bare TypeError; a silent drop is worse, because
    a misspelt 'conditionz' is a condition the engine never applies."""
    d = kairos().to_dict()
    d["conditionz"] = ["prone"]
    with pytest.raises(SchemaError, match="unknown field 'conditionz'"):
        Token.from_dict(d)


def test_to_dict_validates_on_the_way_out():
    """The engine mutates tokens in place, so a bad value usually arrives
    through a rules call rather than a file."""
    k = kairos()
    k.side = "villain"
    with pytest.raises(SchemaError, match="not one of pc, ally, enemy, neutral"):
        k.to_dict()


def test_a_token_survives_a_round_trip_through_json():
    """Including the free-form parts: attacks carry keys the rules layer adds."""
    t = caster()
    again = Token.from_dict(json.loads(json.dumps(t.to_dict())))
    assert again.to_dict() == t.to_dict()
    assert again.attacks == t.attacks and again.extra == t.extra


def test_a_grid_token_may_have_negative_coordinates():
    """An offset grid is legal, and validate() owns the bounds check with the
    map. Baking map geometry into a field would reject a legal board."""
    f = frog("frog-1", (0, 0))
    f.x, f.y = -3, -2
    assert Token.from_dict(f.to_dict()).pos == (-3, -2)


def test_a_token_from_a_monster_still_loads():
    """Every existing encounter file, from every SRD creature, must still load."""
    enc = encounter([kairos(), frog("frog-1", (5, 5))])
    again = Encounter.from_dict(json.loads(json.dumps(enc.to_dict())))
    assert {k: v.to_dict() for k, v in again.tokens.items()} == \
           {k: v.to_dict() for k, v in enc.tokens.items()}


# ─── A4.3 migration ───────────────────────────────────────────────────────────

def _v1_encounter() -> dict:
    """An encounter file as v1 wrote it: hit dice as a bare die string (the
    shape build_srd.py's hp_dice has always used), no extra at all, and a
    death_saves dict missing its keys."""
    return {
        "campaign": "test", "grid": open_map(), "version": 1,
        "tokens": {
            "kairos": {
                "id": "kairos", "name": "Kairos", "side": "pc", "x": 0, "y": 0,
                "hp": 8, "max_hp": 8, "ac": 12, "speed": 30, "dex_mod": 2,
                "controller": "player", "saves": {"int": 5}, "attacks": [],
                "extra": {"hit_dice": "1d6", "level": 1, "abilities": {"dex": 15}},
                "death_saves": {"failures": 1},
            },
        },
        "order": ["kairos"], "round": 2, "turn_index": 0,
        "turn": {"actor": "kairos", "movement_budget": 30},
        "log": [], "status": "active", "system": "dnd5e", "roll_mode": "players", "meta": {},
    }


def test_a_v1_file_loads_and_comes_back_as_the_current_version():
    enc = Encounter.from_dict(_v1_encounter())
    assert enc.version == state.SCHEMA_VERSION
    k = enc.tokens["kairos"]
    assert k.extra["hit_dice"] == {"die": "1d6"}, "the bare die string is normalised"
    assert k.death_saves == {"successes": 0, "failures": 1}
    assert enc.to_dict()["version"] == state.SCHEMA_VERSION


def test_migration_normalises_a_token_with_no_extra_at_all():
    d = _v1_encounter()
    d["tokens"]["kairos"].pop("extra")
    d["tokens"]["kairos"].pop("death_saves")
    k = Encounter.from_dict(d).tokens["kairos"]
    assert k.extra == {} and k.death_saves == {"successes": 0, "failures": 0}


def test_migration_drops_hit_dice_it_cannot_read_rather_than_guessing_a_die():
    """{"remaining": 3, "max": 6} carries no die size. Inventing one would put a
    number the player never wrote into their sheet."""
    d = _v1_encounter()
    d["tokens"]["kairos"]["extra"]["hit_dice"] = {"remaining": 3, "max": 6}
    assert "hit_dice" not in Encounter.from_dict(d).tokens["kairos"].extra


def test_a_file_with_no_version_is_treated_as_the_oldest():
    d = _v1_encounter()
    d.pop("version")
    assert Encounter.from_dict(d).version == state.SCHEMA_VERSION


def test_a_file_from_a_newer_engine_is_refused_rather_than_down_converted():
    d = _v1_encounter()
    d["version"] = state.SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="newer than this engine"):
        Encounter.from_dict(d)
    d["version"] = "two"
    with pytest.raises(ValueError, match="is not a number"):
        Encounter.from_dict(d)


def test_a_gap_in_the_migration_chain_is_an_error_not_a_silent_skip():
    d = _v1_encounter()
    with pytest.raises(ValueError, match="no migration from encounter schema"):
        state.migrate(d) if not hasattr(state, "migrate") else _expect_gap(d)


def _expect_gap(d: dict) -> None:
    saved = dict(state.MIGRATIONS)
    state.MIGRATIONS.pop((1, 2))
    try:
        state.migrate(d)
    finally:
        state.MIGRATIONS.update(saved)


def test_loading_a_v1_file_does_not_rewrite_it(tmp_path):
    """A fight that is only read must not be modified behind the GM's back: the
    upgrade is persisted by the next save(), atomically, with a .bak."""
    path = tmp_path / "encounter.json"
    original = json.dumps(_v1_encounter(), indent=1)
    path.write_text(original, encoding="utf-8")
    state.load(path)
    assert path.read_text(encoding="utf-8") == original
    enc = state.load(path)
    state.save(enc, path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == state.SCHEMA_VERSION
    assert (tmp_path / "encounter.json.bak").exists(), "the old file is kept"


# ─── A4.4 derived stats ───────────────────────────────────────────────────────

def test_derived_stats_report_the_sheet_numbers_rather_than_recomputing_them():
    """A number off a character sheet or an SRD stat block is authoritative.
    A PC's saves already include proficiency; a monster's trained skills are
    totals, not raw ability modifiers."""
    k = caster()
    d = k.prepare_derived()
    assert d["init_mod"] == 2, "DEX 15 is +2"
    assert d["save_mods"] == k.saves
    assert d["skill_mods"]["stealth"] == 4, "the sheet's trained total stands"
    assert d["max_hp"] == 8 and d["max_hp_estimated"] is False


def test_a_missing_initiative_modifier_is_filled_from_the_scores():
    """A hand-made token carries abilities but no dex_mod, and initiative is a
    d20 plus that modifier. Filling it can reorder initiative, which is why
    nothing else is ever filled in."""
    k = kairos()
    k.dex_mod, k.extra["abilities"] = 0, {"dex": 15}
    assert k.prepare_derived()["init_mod"] == 2
    assert k.dex_mod == 2


def test_a_supplied_initiative_modifier_is_never_overwritten():
    k = kairos()
    k.extra["abilities"] = {"dex": 8}
    assert k.prepare_derived()["init_mod"] == 2, "DEX 8 would be -1, but the sheet said +2"
    assert k.dex_mod == 2


def test_an_initiative_bonus_adds_to_the_modifier():
    k = kairos()
    k.extra["initiative_bonus"] = 2
    assert k.prepare_derived()["init_mod"] == 4


def test_perception_falls_back_to_the_wisdom_modifier_only_when_unlisted():
    k = kairos()
    k.extra["abilities"] = {"wis": 14}
    assert k.prepare_derived()["skill_mods"]["perception"] == 2
    k.extra["skills"] = {"perception": 5}
    assert k.prepare_derived()["skill_mods"]["perception"] == 5


def test_max_hp_is_never_computed_when_the_source_had_one():
    k = caster()
    k.hp = 3
    d = k.prepare_derived()
    assert d["max_hp"] == 8 and d["max_hp_estimated"] is False
    assert "max_hp_estimated" not in k.extra


def test_max_hp_is_estimated_only_for_a_token_that_never_had_one():
    """An unstarted hand-made token, so the engine has a number to work with.
    It is labelled, because a max HP the player can see must come from the sheet."""
    t = Token(id="npc", name="Hob", side="enemy", x=1, y=1, hp=0, max_hp=0, ac=12,
              source={"kind": "sheet", "path": ""},
              extra={"hit_dice": {"die": "1d10", "remaining": 6}, "level": 3,
                     "abilities": {"con": 14}})
    d = t.prepare_derived()
    assert d["max_hp"] == 9, "d10 average 5 plus 2 CON for each of two later levels"
    assert d["max_hp_estimated"] is True
    assert t.extra["max_hp_estimated"] is True


def test_a_stat_block_max_hp_is_never_estimated():
    t = Token(id="frog-1", name="Giant Frog 1", side="enemy", x=1, y=1, hp=0, max_hp=0,
              ac=11, source={"kind": "srd", "ref": "giant-frog"},
              extra={"hit_dice": {"die": "1d12", "remaining": 4}, "level": 1,
                     "abilities": {"con": 12}})
    t.prepare_derived()
    assert t.max_hp == 0


def test_a_token_already_in_play_is_never_given_an_estimated_max_hp():
    """hp > 0 with max_hp 0 would silently turn a wounded creature into a full
    one: a free heal."""
    t = Token(id="npc", name="Hob", side="enemy", x=1, y=1, hp=40, max_hp=0, ac=12,
              extra={"hit_dice": {"die": "1d10"}, "level": 3, "abilities": {"con": 14}})
    t.prepare_derived()
    assert t.max_hp == 0


def test_derived_stats_ignore_conditions():
    """Exhaustion halves speed and gives disadvantage; it does not change an
    ability modifier, a save, a skill or max HP. A derived stat that depended on
    a condition would flip value mid-fight and stop being reproducible. The
    level-aware exhaustion work is A1's, in systems/dnd5e/tactics_rules.py."""
    k = caster()
    before = k.prepare_derived()
    k.add_condition("exhaustion")
    k.add_condition("poisoned")
    k.conditions.append("exhaustion 3")
    assert k.prepare_derived() == before


def test_prepare_derived_is_idempotent():
    k = caster()
    k.dex_mod, k.extra["abilities"] = 0, {"dex": 15}
    first = k.prepare_derived()
    assert k.prepare_derived() == first


def test_prepare_all_covers_the_whole_encounter():
    enc = encounter([caster(), frog("frog-1", (5, 5))])
    derived = state.prepare_all(enc)
    assert set(derived) == {"kairos", "frog-1"}
    assert derived["frog-1"]["max_hp"] == 18, "the SRD hit points are not touched"


def test_derived_stats_survive_a_save_and_load_only_as_base_stats():
    """cmd_start calls prepare_all; what reaches the file is the filled-in base
    field (dex_mod), never a cached derived block."""
    enc = encounter([kairos(), frog("frog-1", (5, 5))])
    k = enc.tokens["kairos"]
    k.dex_mod, k.extra["abilities"] = 0, {"dex": 15}
    state.prepare_all(enc)
    d = enc.to_dict()
    assert d["tokens"]["kairos"]["dex_mod"] == 2
    assert not any("derived" in key for key in d["tokens"]["kairos"]), \
        "derived data is recomputed on load, never persisted"
