"""The puppeting lens detector: regex behaviour pinned, no model and no network.

WHY THESE TESTS EXIST
=====================

The lens measures a trip rate. A rate measured by a detector with an unknown
false-positive rate is a measurement of the detector, so the false-positive side
is the part worth pinning: every rule below has a sentence it must NOT flag,
usually the correct narration of the same situation, and those are the tests
that fail first when a rule is loosened.

The other direction is pinned too, but deliberately thin. The rules are written
to miss rather than shout, so a missed puppeting is a known cost, not a defect.
The tests that assert a trip exist to stop a future edit from quietly disabling
a rule; they are not a claim that the rule is complete.

Coverage of the four shapes is in the SHAPE section, and it is a shape matrix
rather than prose samples: the same player line is run through every rule, so a
rule that starts firing on another rule's territory shows up as a duplicate
finding on one line instead of hiding in a paragraph.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from puppet_lens_detector import (  # noqa: E402
    KINDS, UNWRITTEN_RATIO, attribute, content_tokens, detect, emotion_findings,
    kinds_present, preresolved_findings, quoted_spans, unwritten,
)

PC = ("Kairos",)


def kinds(text: str, line: str = "I look at the desk.", rolled: bool = False) -> list:
    return kinds_present(detect(line, text, PC, rolled))


# ── the four shapes, positive ─────────────────────────────────────────────────

def test_pc_speaking_in_words_the_player_never_typed_is_flagged():
    text = ('The archivist does not look up. "I am not here for the books," you say, '
            'and the lie sits fine in your mouth.')
    assert "invented-dialogue" in kinds(text)


def test_assigned_emotion_is_flagged():
    assert "assigned-emotion" in kinds("You feel a chill of recognition as you read it.")


def test_an_interior_conclusion_is_flagged():
    assert "assigned-emotion" in kinds("You realize at once that Hesper has lied to you.")


def test_an_unstated_physical_act_is_flagged():
    assert "narrated-action" in kinds("You draw the dagger and set your feet.")


def test_a_bare_present_tense_verb_is_flagged_like_any_other():
    """The form the loop's own prompts produce. A verb list that spelled only -s
    and -ed missed "you push" and "you reach" in the same sentence as "you lean",
    which was found, for the identical defect."""
    text = "You push back from the desk and reach for your silver quill."
    got = kinds(text, line="I try to read the margin of the ledger")
    assert "narrated-action" in got


def test_an_attempt_belonging_to_another_verb_is_not_an_attempt_at_this_one():
    """Scoped to the 30 characters after the verb. A clause-wide test read "You
    push back against the ledger ... ready to make an entry" as an attempt at
    pushing, and the finding was lost."""
    text = "You push back against the cold leather and reach for your quill, ready to make an entry."
    assert "narrated-action" in kinds(text, line="I put my hand on the ledger")


def test_an_attempt_at_the_verb_itself_is_not_puppeting():
    """The player stated the intent to try; the DM narrating the trying is its
    job."""
    text = "You try the handle and it will not turn, so you try to lift the whole thing."
    assert "narrated-action" not in kinds(text, line="I try to open the door")


def test_third_person_puppeting_is_flagged():
    """The shape the shipped guardrail misses, and the reason this lens is not
    reply.speaks_for_player. The DM wrote the PC's interior state in the third
    person; the guardrail only reads second person."""
    assert "assigned-emotion" in kinds("Kairos feels the shape of the trap before he moves.")


def test_pre_resolved_is_flagged_when_the_player_only_stated_intent():
    text = "You lean over the ledger and run a finger down the column. A name, inked."
    assert "pre-resolved" in kinds(text, line="I try to see if a name is written in the margin")


def test_pre_resolved_is_silent_once_the_engine_has_rolled():
    """After a check the outcome is earned, and naming it is the entire job of
    the narration. The caller passes rolled=True for the post-roll chunk only."""
    text = "You lean over the ledger and run a finger down the column. A name, inked."
    found = detect("I try to see if a name is written in the margin", text, PC, True)
    assert "pre-resolved" not in kinds_present(found)


def test_pre_resolved_needs_the_player_to_have_stated_an_intent():
    """A player who said "I open the door and it opens" has resolved it. The DM
    narrating the swing is correct, so the rule must not fire."""
    text = "The door swings open on a breath of cold air and you step through."
    assert "pre-resolved" not in kinds(text, line="I open the door and it opens")


# ── false positives: the four that decide whether the rate means anything ─────

def test_npc_dialogue_is_never_flagged():
    """The most common quotation in the genre."""
    text = 'The archivist turns a page. "State your business," she says, not looking up.'
    assert kinds(text) == []


def test_the_pcs_own_name_in_a_quote_is_not_speech():
    text = '"Kairos," the archivist says, and the pen stops.'
    assert kinds(text) == []


def test_a_quote_of_only_the_pcs_name_cannot_be_invented():
    """The name is dropped before the overlap test, so a quote that is nothing
    but the PC's name has no content words left to be absent."""
    assert not unwritten("Kairos", "I look around the archive.", PC)
    # An address around the name still has content words of its own, so it is
    # judged on those. "here" is on the stop list, "come" is not.
    assert unwritten("Come here, Kairos", "I look around the archive.", PC)


def test_the_dm_echoing_the_players_own_words_is_not_flagged():
    line = 'I tell the archivist, "I only want the catalogue, and then I leave."'
    text = '"Only the catalogue," you say, and the archivist’s pen stops.'
    assert kinds(text, line) == []


def test_a_faithful_compression_of_a_long_line_is_not_flagged():
    """The reason UNWRITTEN_RATIO is 0.5 and not 1.0."""
    line = 'I tell him to leave me alone and I turn away from the desk.'
    text = '"Leave me alone," you snap, turning away.'
    assert kinds(text, line) == []


def test_a_yes_from_a_player_who_nodded_is_invented():
    assert unwritten("Yes.", "I nod and let it pass.")
    assert not unwritten("Yes.", "I tell her yes and let it pass.")


def test_an_offered_choice_is_the_opposite_of_the_defect():
    assert kinds("You could step back, or you could draw the dagger.") == []


def test_a_question_to_the_player_is_not_an_assignment():
    assert kinds("What do you feel about the offer?") == []


def test_the_dm_narrating_an_involuntary_consequence_is_not_puppeting():
    text = ("The frog lunges. You flinch, and the copy of Hesper's letter slides off the "
            "desk into the water.")
    assert kinds(text) == []


def test_ordinary_world_narration_is_clean():
    text = ("Rain ticks on the skylight. The archivist's lamp gutters, and somewhere "
            "below, a cart wheel squeals on the flagstones.")
    assert kinds(text) == []


def test_a_sensation_with_no_emotion_in_it_is_the_world_acting():
    """The boundary that keeps rule 2 honest: physical sensation is the DM's
    job, interior state is the player's."""
    assert kinds("You feel the cold of the stone under your palm.") == []
    assert "assigned-emotion" in kinds("You feel a chill of recognition.")


def test_generic_second_person_you_never_know_is_not_an_interior_state():
    assert kinds("You never know how a fumble goes until the dice are down.") == []


def test_the_dm_deciding_what_the_pc_has_worked_out_is_flagged():
    """"know" is in the cognition list, which looked like it would collide with
    the generic above. It cannot: the pattern needs the verb directly after "you"
    and "never" sits between. Both lines are from the same live run."""
    assert "assigned-emotion" in kinds(
        "You know no such name has been written yet by anyone else in this place.")


def test_a_result_stated_as_knowledge_is_a_pre_resolved_result():
    """Same sentence, read by the other rule. A live run produced exactly this
    beat: no roll anywhere, and the DM telling the player what the margin does
    not contain."""
    text = "You lean closer to the quill and scan the margin. You know there is no name here."
    assert "pre-resolved" in kinds(text, line="I try to see if a name is written in the margin")


def test_posture_is_not_a_choice_the_player_made():
    """"You lean against the sill" after "I stay by the window" describes the
    position the player just took. A flagged version of this is the one finding
    in the first live run, and it was the weakest of the two, which is the shape
    of a rule that has learned a boundary from a live transcript rather than
    from a principle. Posture joins pause and stand."""
    assert kinds("You lean against the cold stone sill and watch the courtyard.",
                 line="I stay by the window and watch the courtyard.") == []


# ── attribution ───────────────────────────────────────────────────────────────

def test_attribution_reads_a_tag_after_the_quote():
    text = '"Leave," you say.'
    assert attribute(text, 0, 7, PC) == "pc"


def test_attribution_reads_a_tag_before_the_quote():
    text = 'You mutter, "Not again."'
    s, e, _ = quoted_spans(text)[0]
    assert attribute(text, s, e, PC) == "pc"


def test_attribution_by_the_pcs_name_counts_as_the_pc():
    text = 'Kairos says, "I said no."'
    s, e, _ = quoted_spans(text)[0]
    assert attribute(text, s, e, PC) == "pc"


def test_attribution_of_a_third_party_is_not_the_pc():
    text = 'The archivist murmurs, "Again."'
    s, e, _ = quoted_spans(text)[0]
    assert attribute(text, s, e, PC) == "other"


def test_an_unattributed_quote_is_never_the_pc():
    """A known miss, pinned so that widening it has to be a deliberate edit to
    this test rather than an accident in a regex."""
    text = 'The lamp gutters.\n\n"Not again."\n\nThe rain keeps up.'
    s, e, _ = quoted_spans(text)[0]
    assert attribute(text, s, e, PC) == ""


def test_a_bare_third_person_pronoun_is_never_the_pc():
    """The lens cannot tell which "he" it is reading, so it resolves to "other"
    and never to the PC. "other" rather than unidentified, because the speaker
    is identified and is simply not this character: the difference matters only
    for the report, which can say who was talking."""
    text = 'He says, "I have nothing."'
    s, e, _ = quoted_spans(text)[0]
    assert attribute(text, s, e, PC) != "pc"


# ── mechanics the rules lean on ───────────────────────────────────────────────

def test_quoted_spans_keep_the_offsets_of_the_original_text():
    text = 'Rain. "Come in," you say. Rain.'
    s, e, inner = quoted_spans(text)[0]
    assert text[s:e] == '"Come in,"' and inner == "Come in,"


def test_a_full_stop_inside_a_quote_does_not_split_the_sentence():
    """Sentences are found on a masked copy, or "Mr. Vale sent me." is two
    sentences and the attribution is lost."""
    text = 'The note reads: "Mr. Vale sent me." You fold it away.'
    s, e, _ = quoted_spans(text)[0]
    assert attribute(text, s, e, PC) == ""      # the speaker really is not stated


def test_keywords_inside_a_quote_do_not_trip_a_rule():
    text = 'The archivist says, "You feel nothing at all in these stacks, boy."'
    assert kinds(text) == []


def test_content_tokens_drop_function_words():
    assert content_tokens("the and but catalogue") == {"catalogue"}


def test_content_tokens_drop_the_pcs_name():
    assert content_tokens("Kairos draws the dagger", {"kairos"}) == {"draws", "dagger"}


def test_the_overlap_threshold_is_half():
    """Pinned because it is the difference between flagging a faithful echo and
    missing a chosen word, and the number is the only place that choice lives."""
    assert UNWRITTEN_RATIO == 0.5
    assert unwritten("Only the catalogue.", 'I want the catalogue') is False
    assert unwritten("Not again.", "I tell her not to come back") is True


# ── shape matrix: one line, every rule, no double counting ────────────────────

def test_a_clean_reply_produces_no_findings_of_any_kind():
    text = ("The archivist sets down her pen. The rain has stopped without your noticing, "
            "and the flagstones outside are beginning to shine.")
    assert detect("I wait by the desk and say nothing.", text, PC) == []


def test_every_kind_is_reachable_from_one_reply():
    text = ('"You are late," the archivist says. You feel a chill. You draw the dagger. '
            'You lean over the ledger and find a name, inked.')
    found = kinds_present(detect("I try to see if a name is written in the margin", text, PC))
    assert set(found) <= set(KINDS) and len(found) >= 3


def test_findings_carry_evidence_a_report_can_quote():
    found = detect("I look at the desk.", "You feel a chill of recognition.", PC)
    assert found and all(f.evidence and f.detail for f in found)
    assert all(set(f.as_dict()) == {"kind", "evidence", "detail"} for f in found)


def test_no_spelling_uses_an_em_dash():
    """The fork bans them in docs, comments and UI text, and a detector whose
    patterns are copied into prompts would carry them straight into the DM's
    instructions."""
    import puppet_lens_detector as mod
    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "\u2014" not in src

# ── false-positive sweep on hand-written clean narration ──────────────────────
# The unit tests above each pin one rule. This pins the RATE, which is what the
# lens is actually for: a detector that is clean on a sentence written to trip it
# can still be noisy on ordinary narration, and a noisy lens makes the measured
# trip rate a measurement of the regex.
#
# Every line here is narration a competent DM would write in response to the
# line beside it. They are not neutral prose: they are the hardest cases, with
# the DM's own NPC dialogue, the player's own dialogue echoed back, involuntary
# reactions, physical sensation, an offered choice, a name in a quote, and
# spelled-out world detail. A trip on any of these is a false positive, and the
# rate is the number the report is allowed to lean on.

CLEAN = [
    ("I look around the room.",
     "The lamp gutters. Rain ticks on the skylight, and somewhere below a cart "
     "wheel squeals on the flagstones."),
    ("I ask the archivist what happened to the last student.",
     "The archivist sets down her pen. \"The fourteenth of Tarsus,\" she says. "
     "\"He signed, and then he did not.\""),
    ("I go to the door.",
     "The door is heavier than it looks. Cold air comes through the gap, "
     "carrying lamp oil and wet stone."),
    ("I hand over the ledger.",
     "The archivist takes the ledger and does not open it. Her thumb finds the "
     "spine and stops there."),
    ("I wait.",
     "Rain. The lamp gutters twice and holds. A cart moves somewhere below, and "
     "then the street is quiet."),
    ("I nod.",
     "The archivist turns a page. She does not look up, and the pen keeps moving."),
    ("I draw my dagger.",
     "The steel comes out slow. Your hand knows the weight of it before your "
     "eyes do."),
    ('I tell her, "I only want the catalogue."',
     '"Only the catalogue," you say. The archivist looks up for the first time.'),
    ("I try the lock.",
     "The lock is a plain ward-lock, badly made. The keyhole is worn at the top "
     "edge, which means it is used often."),
    ("I stand by the window.",
     "Below, the courtyard is a sheet of wet stone and one guttering lamp. A cart "
     "is already loading under the arch."),
    ("I search the desk.",
     "The desk has three drawers and a false bottom that has not been opened in "
     "years. Paper dust, an inkwell, a pen."),
    ("I push the door open and step through.",
     "The hinges complain. Beyond is a corridor of stacked crates, and the smell "
     "of lamp oil."),
    ("I look at the archivist.",
     "The archivist is perhaps sixty, with ink to the second knuckle. She has the "
     "look of someone who has stopped being surprised by visitors."),
    ("I hold still.",
     "Nobody moves for a while. Then the lamp goes out, and the dark is total "
     "except for the grey of the skylight."),
    ("I ask what the debt was for.",
     '"Ask the fourteenth of Tarsus," she says. "He is the one who signed it. He '
     'is not here, and that is the trouble with him."'),
    ("I step back from the desk.",
     "The desk is old enough that it has learned the room. Your back finds the "
     "edge of it, and the inkwell shifts a quarter inch and holds."),
    ("I wait for the archivist to finish.",
     "The pen keeps moving for a while. Outside, the cart comes back, loaded, "
     "and the axles complain about the last two miles."),
    ("I ask about Magister Vael.",
     '"Vael?" The archivist looks at you for the first time. "That is a long '
     'time ago, and a poor subject for this counter."'),
    ("I look at the name on the slip.",
     "The ink is brown at the edges and black in the middle, which means it was "
     "not written in one sitting."),
    ("I open the ledger and read the first page.",
     "The first page is a list of names in three hands, two of them faded past "
     "reading. The third is fresh enough to smell of ink."),
    ("I say nothing and let her work.",
     "She works. The lamp burns down a finger's width, and the rain stops "
     "without either of you noticing."),
    ("I put my hand flat on the closed ledger.",
     "The leather is cold, and colder still where the clasp is. Under your palm "
     "the pages sit flat and unbroken, which is its own kind of answer."),
]


def test_no_false_positives_on_clean_narration():
    noisy = [(line, text, d.as_dict()) for line, text in CLEAN
             for d in detect(line, text, PC)]
    assert not noisy, noisy


def test_the_clean_corpus_is_wide_enough_to_mean_something():
    """Twenty-two lines of deliberate near-misses. A sweep over three lines would
    pass with a detector that flags any dialogue at all.

    Four of the twenty-two contain quotations, and they are the four that matter:
    NPC speech, NPC speech with a name in it, the player's own line echoed back
    compressed, and a two-line NPC speech. Requiring more than four would be
    padding the corpus with narration that has no quotation in it, which tests
    nothing this lens is about.
    """
    assert len(CLEAN) >= 20
    assert sum(1 for _, text in CLEAN if quoted_spans(text)) >= 4, (
        "the sweep must include replies that actually contain quotations")


def test_every_shape_is_reachable_in_a_single_line_of_puppeting():
    """The positive control for the sweep above: the same detector, on the
    four shapes, flags all four. Without this the sweep would also pass on a
    detector that never fires."""
    cases = [
        ("I wait in the doorway.",
         '"I know what you took," you say.', "invented-dialogue"),
        ("I look at the ledger.",
         "You feel a chill of recognition as your eyes find the name.",
         "assigned-emotion"),
        ("I go to the door.",
         "You draw your dagger before you can think better of it.",
         "narrated-action"),
        ("I try to see if a name is written in the margin.",
         "You lean over the ledger. A name, inked in a hand you know.",
         "pre-resolved"),
        ("I say nothing.",
         "Kairos nods, as if the answer had been yes all along.",
         "narrated-action"),
    ]
    for line, text, want in cases:
        got = kinds_present(detect(line, text, PC))
        assert want in got, f"{want} missed on {text!r} (got {got})"
