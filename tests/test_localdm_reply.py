"""Milestone 6: splitting a DM reply into narration and its JSON block."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import reply        # noqa: E402


def test_plain_text_is_all_narration():
    assert reply.parse("The door creaks open.") == reply.DMReply("The door creaks open.")


def test_a_fenced_json_block_is_split_off():
    r = reply.parse('The frog lunges.\n```json\n{"escalate": null, '
                    '"command": "attack kairos frog-1 fire bolt"}\n```')
    assert r == reply.DMReply("The frog lunges.", None, "attack kairos frog-1 fire bolt")


def test_a_bare_trailing_json_line_is_split_off():
    r = reply.parse('A sigil is carved into the altar.\n'
                    '{"escalate": "Is this sigil tied to the lich cult?", "command": null}')
    assert r.narration == "A sigil is carved into the altar."
    assert r.escalate == "Is this sigil tied to the lich cult?" and r.command is None


def test_think_blocks_are_dropped():
    assert reply.parse("<think>\nplan the scene\n</think>\nRain falls.").narration == "Rain falls."
    assert reply.strip_think("<think></think>\n\n3") == "3"


def test_blank_or_wrong_typed_fields_become_none():
    assert reply.parse('Ok.\n{"escalate": "   ", "command": 3}') == reply.DMReply("Ok.")


def test_broken_json_stays_in_the_narration():
    text = 'Hm.\n{"escalate": }'
    assert reply.parse(text) == reply.DMReply(text)


def test_guard_flags_speech_and_feelings_for_the_player():
    assert reply.speaks_for_player('"So," you say, "what is this?"')
    assert reply.speaks_for_player("You feel a chill as the door opens.")
    assert reply.speaks_for_player('"Later," you murmur.')


def test_guard_allows_world_and_npc_narration():
    assert not reply.speaks_for_player('The student flinches. "Orientation," he whispers.')
    assert not reply.speaks_for_player("Your satchel holds a spellbook and a quill.")


def test_cut_off_json_line_is_not_narration():
    r = reply.parse('The door creaks open.\n{"escalate": ')
    assert r == reply.DMReply("The door creaks open.")


def test_check_field_and_prompt_tail():
    r = reply.parse('You reach the lectern.\n\nWhat do you do?\n'
                    '{"escalate": null, "command": null, "check": "Investigation 13"}')
    assert r.check == "Investigation 13" and r.narration == "You reach the lectern."


# ── D3: a cast beat may not state a mechanical number the engine did not give ──
#
# The measured defect. On qwen3.5:9b, 0 of 3 probes emitted the `cast` field, so
# `_cast_spell` never ran, the sheet kept "1st | 2 | 0" — and the DM said, in
# prose, "Your AC climbs from 12 to 15 instantly". The player is told a number
# their character sheet does not contain. This is the class the dnd-skill
# grounding check cannot see: the *name* is canon, only the *possession* is
# invented.
#
# The real narration from the 2026-09-29 playtest is the first case below. It is
# the regression test that matters — a hand-written paraphrase would not have
# caught the actual sentence.

def test_guard_flags_the_real_d3_narration_from_the_playtest():
    assert reply.states_an_unbacked_cast_result(
        "The silver quill in your hand drifts upward as you whisper the incantation "
        "for Mage Armor; invisible light swells around your skin, hardening into "
        "shimmering scales that ripple like water before settling. Your AC climbs "
        "from 12 to 15 instantly, and a faint hum vibrates through your bones "
        "where Hesper gifted this focus.")


def test_guard_flags_a_stated_stat_change_on_a_cast_beat():
    assert reply.states_an_unbacked_cast_result("He casts Shield. His AC is now 15.")
    assert reply.states_an_unbacked_cast_result(
        "She casts Mage Armor and her AC goes from 12 to 15.")
    assert reply.states_an_unbacked_cast_result(
        "She casts the binding. Her spell save DC rises to 16.")
    assert reply.states_an_unbacked_cast_result(
        "He invokes the blessing. His spell attack is now +7.")


def test_guard_flags_a_slot_count_stated_in_either_order():
    """Both orders because the model writes both, and "1 level 1 slot remains"
    puts "level 1" between the count and the verb."""
    assert reply.states_an_unbacked_cast_result(
        "He casts Shield, and 1 level 1 slot remains.")
    assert reply.states_an_unbacked_cast_result(
        "He casts Fire Bolt. The slots now read 1 of 2.")


def test_guard_flags_a_claim_with_no_cast_verb_at_all():
    """The model frequently drops the verb and reports the bookkeeping."""
    assert reply.states_an_unbacked_cast_result(
        "Mage Armor settles. Two level 1 slots spent, one left.")


def test_guard_allows_a_cast_beat_with_no_number():
    """The false-positive side, and the reason a digit is required. Describing a
    ward is not asserting a stat, and rewriting it for the crime of mentioning
    armour class is exactly how a gate teaches a GM to ignore it."""
    assert not reply.states_an_unbacked_cast_result(
        "Kairos traces a ward of shimmering light around himself, and the air "
        "tastes of ozone.")
    assert not reply.states_an_unbacked_cast_result(
        "She chants, and silver light settles over his shoulders like cold rain.")
    assert not reply.states_an_unbacked_cast_result(
        "The ward knits itself into his armour class, layer on layer.")
    # Naming a spell is context, not a claim.
    assert not reply.states_an_unbacked_cast_result(
        "Mage Armor is a 1st-level abjuration spell, and every wizard knows it.")


def test_guard_ignores_narration_that_is_not_a_cast_beat():
    """Scoped deliberately: a door described by its armour class is not a cast
    claim, and neither is a third party's save DC.

    Note the guardrail runs in `_player_turn`, so combat narration never reaches
    it: an engine-resolved fight is summarized by `_engine`, not narrated here.
    "Your AC is 12" in a combat line is therefore not a live false positive —
    and if it ever were, the cost is one rewrite, not a wrong number."""
    assert not reply.states_an_unbacked_cast_result(
        "The AC 12 door has been reinforced with iron bands.")
    assert not reply.states_an_unbacked_cast_result(
        "The buckler bears the marks of the 3rd ward, cast long ago by another hand.")
    # A stat change with no cast signal is not a cast beat, and rewriting it
    # would be a false positive on an ordinary line of narration.
    assert not reply.states_an_unbacked_cast_result(
        "The binding settles; her spell save DC rises to 16.")


# ── the cast field, in the shape a small model actually returns it ──────────
#
# The prompt asks for {"cast": "Mage Armor"} — a bare string, because that is
# what `_cast_spell` looks the spell up by. Models answer with an object instead:
#
#     {"cast": {"spell_name": "mage armor", "mechanics_applied_by_engine": true,
#               "description_prose": "Silvery luminescence blooms from your palm."}}
#
# which was dropped on the floor, so the cast was never resolved: no slot spent,
# no AC recorded, and the turn narrated a spell that did not mechanically happen.
# Measured on qwen3.5:9b, which produced exactly this shape unprompted.
#
# `mechanics_applied_by_engine: true` is the field most likely to cause it: a
# model asked to name a spell often volunteers a flag saying the mechanics are
# handled, which is precisely the belief that must not be taken at face value.

def test_cast_field_accepts_the_bare_string_form():
    assert reply.parse('The ward settles.\n{"escalate": null, "command": null, '
                       '"cast": "Mage Armor"}').cast == "Mage Armor"


def test_cast_field_unwraps_the_object_form():
    r = reply.parse('{"cast": {"spell_name": "mage armor", '
                    '"mechanics_applied_by_engine": true, '
                    '"description_prose": "Silvery light."}}')
    assert r.cast == "mage armor"


def test_cast_field_unwraps_alternative_object_keys():
    assert reply.parse('{"cast": {"spell": "Shield"}}').cast == "Shield"
    assert reply.parse('{"cast": {"name": "Fire Bolt"}}').cast == "Fire Bolt"


def test_a_model_claiming_the_engine_applied_the_mechanics_changes_nothing():
    """The flag is ignored entirely. Nothing is applied until the engine applies
    it, so a model asserting otherwise must not change what happens."""
    with_flag = reply.parse('{"cast": {"spell_name": "mage armor", '
                            '"mechanics_applied_by_engine": true}}')
    without = reply.parse('{"cast": {"spell_name": "mage armor"}}')
    assert with_flag.cast == without.cast == "mage armor"


def test_cast_field_still_rejects_shapes_it_cannot_use():
    assert reply.parse('{"cast": null}').cast is None
    assert reply.parse('{"cast": {"nonsense": 1}}').cast is None
    assert reply.parse('{"escalate": null}').cast is None


def test_a_nested_json_object_is_still_extracted():
    """The extractor used a `[^{}]*` class, which cannot span a nested object —
    that is why the object form above was silently dropped. Guard the fix, and
    guard that braces in prose are not mistaken for a JSON block."""
    assert reply.parse('Silver light.\n{"escalate": null, "cast": {"name": "Shield"}}').cast \
        == "Shield"
    assert reply.parse("The door has a {crack} in it.").cast is None
    assert "crack" in reply.parse("The door has a {crack} in it.").narration
