"""Milestone 6: splitting a DM reply into narration and its JSON block."""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

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


# ── B6: a directive followed by trailing prose crashed `parse` ──
#
# `_LAST_OBJECT` is the last resort for exactly this reply shape: the directive
# is in the reply but is not the last thing in it, a stray tag or a trailing
# aside. It shipped with a NON-capturing group (e915db5) while `parse` reads
# `m.group(1)`, so every input that reached this branch raised
# `IndexError: no such group` out of `parse()` instead of parsing anything.
#
# The cost was not a mangled turn. `parse` is what turns a raw model string into
# the turn the session saves, so the exception unwound past the campaign state
# and the fight and the transcript. Reported as B6 in the playtest sweep and
# reproduced on 1 model of 5, which is why the suite never caught it: the other
# four put the directive last, where `_BARE` matches and has a group.
#
# Parametrized over all four directive names, because the four are one
# alternation and a fix applied to three of them is not a fix.

_DIRECTIVES = ("escalate", "command", "check", "cast")


def _body(directive: str) -> str:
    """A reply whose directive object is followed by prose that is not JSON."""
    return ('The torchlight gutters across the flagstones.\n'
            '{"%s": "Investigation 13"}\n'
            'Rain drums on the shutters. The lantern gutters.' % directive)


@pytest.mark.parametrize("directive", _DIRECTIVES)
def test_a_directive_followed_by_trailing_prose_does_not_crash_parse(directive):
    """The crash itself: this shape raised IndexError out of `parse`.

    Before the fix every one of these four raised `IndexError: no such group`.
    """
    r = reply.parse(_body(directive))
    assert isinstance(r, reply.DMReply)


@pytest.mark.parametrize("directive", _DIRECTIVES)
def test_a_directive_followed_by_trailing_prose_is_still_read(directive):
    """And it is read, not merely survived.

    This is the assertion that separates the fix from swallowing the error: a
    change that only widened the `except` returns all-None here and leaves the
    JSON in the narration, which is the same player-visible defect `_LAST_OBJECT`
    was added to prevent.
    """
    r = reply.parse(_body(directive))
    assert getattr(r, directive) == "Investigation 13"
    assert "torchlight" in r.narration
    assert "Investigation 13" not in r.narration
    assert "{" not in r.narration


@pytest.mark.parametrize("directive, tail", [
    ("escalate", "\nThe lantern gutters."),
    ("command", "\n\nWhat do you do?\n"),
    ("check", "\n\n**Options**\n- look at the latch"),
    ("cast", "\n</response>\nThe door is shut."),
])
def test_the_shapes_that_reached_the_last_resort_all_parsed(directive, tail):
    """The other measured shapes, not just the one sentence in the report.

    B6 came from a playtest sweep where the directive was followed by the
    prompt's own trailing question, a markdown options block and a stray
    response tag. All four raise `IndexError` before the fix.

    Kept as its own parametrization rather than folded into the test above
    because these are different tails on the same branch: the first test pins
    the crash, this one pins that each real tail is actually handled.
    """
    r = reply.parse('Rain falls.\n{"%s": "Investigation 13"}%s' % (directive, tail))
    assert getattr(r, directive) == "Investigation 13"
    assert "{" not in r.narration


@pytest.mark.parametrize("directive", _DIRECTIVES)
def test_the_two_working_shapes_are_unchanged_by_the_group(directive):
    """Control: `_FENCED` and `_BARE` with nothing after them are untouched.

    Passes before and after by design, and says so. The group is added to the
    third pattern and the `except` is widened, and neither touches the two that
    already worked; this is what proves it. A test for "unchanged" cannot fail
    pre-fix, so it is a control and is not claimed as evidence of the fix.
    """
    bare = reply.parse('A sigil is carved.\n{"%s": "Investigation 13"}' % directive)
    fenced = reply.parse('A sigil is carved.\n```json\n{"%s": "Investigation 13"}\n```'
                         % directive)
    assert getattr(bare, directive) == "Investigation 13"
    assert bare.narration == fenced.narration == "A sigil is carved."
    assert getattr(fenced, directive) == "Investigation 13"


def test_no_directive_key_means_no_damage_to_ordinary_prose():
    """An ordinary brace pair is still not a directive, and still stays.

    The alternation that makes the group safe: a non-directive key does not
    match, so widening what `parse` tolerates cannot start eating braces.
    """
    assert reply.parse('The door has a {crack} in it.\nRain falls.') \
        == reply.DMReply('The door has a {crack} in it.\nRain falls.')


def test_a_pattern_without_a_capture_group_cannot_escape_parse(monkeypatch):
    """The widened `except`, pinned deliberately.

    With the capture group in place nothing reaches `IndexError` any more, so the
    second half of the fix is unpinnable through the public path: reverting it
    leaves every other test in this file green. This drives it directly instead,
    by handing `parse` a pattern in the broken shape and asserting the contract
    holds -- an unreadable directive degrades to narration rather than
    unwinding the caller's turn.

    A test that reaches inside the module is normally the wrong shape. It is
    right here because the thing under test IS the guarantee that a malformed
    internal pattern stays internal, and that guarantee has no other observable
    effect once the bug is fixed. Without it, the `except` is unfalsifiable and
    a later pattern edit can silently reintroduce the crash.
    """
    broken = re.compile(r'\{\s*"(?:escalate|command|check|cast)"[^{}]*\}', re.S)
    monkeypatch.setattr(reply, "_LAST_OBJECT", broken)
    text = 'Rain falls.\n{"check": "Investigation 13"}\nThe lantern gutters.'
    r = reply.parse(text)               # must not raise IndexError
    assert r.check is None              # and must not invent the field
    assert r.narration == text          # and must leave the reply intact


def test_prose_AFTER_a_mid_reply_directive_is_not_recovered():
    """A limit of the fix, pinned so it cannot be mistaken for intended behaviour.

    `parse` truncates at `m.start()`, so prose that FOLLOWED a mid-reply
    directive is dropped along with the JSON. Before the fix this shape raised
    IndexError and no narration came back at all, so this is strictly better --
    but the prose is still gone, and a reader of the tests should not have to
    infer that. Content loss, not a crash: see the brief's section 6.
    """
    r = reply.parse('Rain falls.\n{"check": "Perception 13"}\nThe lantern gutters.')
    assert r.check == "Perception 13"
    assert r.narration == "Rain falls."
    assert "gutters" not in r.narration


def test_a_nested_object_with_trailing_prose_is_still_not_extracted():
    """The other limit: `[^{}]*` cannot span a nested object, so a cast object
    followed by prose matches nothing and stays in the narration.

    No crash either way. Pinned because the natural next edit to this pattern --
    dropping `[^{}]*` for `.*` to catch nested casts -- would reach across the
    narration, and this says what it would be trading against.
    """
    text = 'Rain falls.\n{"cast": {"spell_name": "mage armor"}}\nThe lantern gutters.'
    r = reply.parse(text)
    assert r.cast is None
    assert r.narration == text


def test_a_directive_named_but_not_parsable_falls_back_to_narration():
    """Unreadable JSON with trailing prose: no raise, and nothing invented.

    The widened `except` must not turn a failed read into a fabricated field.
    """
    text = 'Rain falls.\n{"check": }\nThe lantern gutters.'
    r = reply.parse(text)
    assert r.check is None
    assert r.narration == text


# ── RI6: a narrated number the engine did not produce ─────────────────────────

# The 2026-09-30 sweep's real turn: "one slot left, AC 15" against an engine state of
# ac=12, slots 2/2 (TEST-REPORT-strixhaven-sweep-llm-2026-09-30.md:547-560).
SHEET = {8, 12}                 # Kairos: 8/8 HP, AC 12


@pytest.mark.parametrize("draft, claim", [
    ("You land hard and take 7 damage.", "7 damage"),
    ("Nine fire damage sears the frog.", "9 fire damage"),
    ("You regain twenty-three hit points.", "23 hit points"),
    ("Your AC climbs from 12 to 15 instantly.", "AC climbs from 12 to 15"),
    ("One slot left, AC 15.", "AC 15"),
    ("You roll a natural 20!", "roll a natural 20"),
    ("The lock is a DC 15 at least.", "DC 15"),
    ("You are down to 3/8 HP.", "3/8 HP"),
])
def test_an_invented_mechanical_number_is_reported(draft, claim):
    assert reply.unbacked_numbers(draft, SHEET) == [claim]


@pytest.mark.parametrize("draft", [
    "Three guards stand ten feet away in room 12.",
    "The barrel rolls 3 feet down the slope.",
    "The ledger is dated 1492 and lists two hundred names.",
    "Your hit points, and the 3 guards, are the least of it.",
    "Kairos rolls 1d20+5 for the bolt.",
    "Two swords deal damage at once.",
    "A natural 12-foot drop opens below.",
])
def test_a_number_that_is_scenery_is_never_a_claim(draft):
    """The direction that costs everyone: a guard that read bare numbers would
    rewrite every other turn. None of these state a mechanical result."""
    assert reply.number_claims(draft) == []


def test_a_number_the_engine_produced_is_backed():
    engine = ("Kairos Fire Bolt -> Giant Frog 1: 17 vs AC 11, hit. "
              "8 fire damage; Giant Frog 1 10/18 HP.")
    backed = reply.engine_numbers(engine)
    draft = "The bolt hits for 8 fire damage, and the frog is at 10/18 HP against AC 11."
    assert reply.number_claims(draft)                    # it does state numbers
    assert reply.unbacked_numbers(draft, backed) == []   # and every one is the engine's


def test_engine_numbers_reads_text_ints_and_nested_fields_and_skips_none():
    assert reply.engine_numbers("8 / 8 HP, AC 12", 3, None, [(5, 9), None],
                                {"x": 1}, True) == {8, 12, 3, 5, 9}
