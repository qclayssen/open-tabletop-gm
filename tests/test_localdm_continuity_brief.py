"""The continuity advisor brief must actually carry the three jobs assigned to it.

It did not. The brief was 19 lines and grepping it for `dead`, `died`, `death`,
`permanent`, `knows` or `knowledge` returned nothing: no permanence for the dead, no
per-NPC knowledge boundary, and no method at all. The two `fact` hits were both the
word "factions".

These tests pin structure and content, not effect. A green run means the brief says the
things it was asked to say; whether the DM then honours them needs a seat run. That is
the same bargain `tests/test_fail_forward.py:96-108` and `tests/test_injection_guard.py`
make when they pin exact prompt sentences -- prose-as-test is an established convention
here, and the discipline it requires is that each assertion must fail on *absence*.

That last point is why the two Method tests slice the section out instead of searching
the whole file. The pre-fix brief already contained the words "quote the line that proves
any contradiction" in its Responsibilities list, so a flat assertion on citation
language would have passed on the code it was written to fix.
"""

import pathlib

BRIEF = pathlib.Path(__file__).resolve().parents[1] / (
    "scripts/localdm/prompts/advisors/continuity.md"
)

# "dies" is deliberately excluded: it is a substring of a present-tense clause about
# the world, and a brief could carry it while saying nothing about permanence. The
# words below are the ones that only appear if the rule is actually written down.
DEAD_WORDS = ("dead", "died", "death", "permanent", "permanence")


def _brief() -> str:
    return BRIEF.read_text(encoding="utf-8")


def _method(brief: str) -> str:
    """The Method section only.

    Scoped deliberately: a whole-file assertion on citation language passes on the
    pre-fix brief, which already says "quote the line that proves any contradiction".
    """
    assert "## method" in brief.lower(), "brief has no Method section"
    return brief.lower().split("## method", 1)[1]


def test_brief_names_dead_permanence():
    """The brief states that the dead do not come back, on a trigger rather than a hunt.

    Fails on the 19-line pre-fix brief: `'method' in low -> False`, and zero hits for
    every word in DEAD_WORDS.
    """
    low = _brief().lower()
    assert any(word in low for word in DEAD_WORDS), (
        "the continuity brief never mentions the dead"
    )
    assert "## method" in low, "the rule is asserted with no method to enforce it"


def test_dead_rule_is_scoped_to_a_trigger_not_a_standing_duty():
    """The permanence rule must not become an unconditional obligation.

    `advisor.FALLBACK` makes continuity the shadow advisor's default and `_start_shadow`
    runs it every turn, so a standing duty pushes the advisor to always find something,
    which defeats the "nothing" filter on the shadow reply and feeds the DM continuity
    noise it did not ask for mid-scene. Pinned because that regression is invisible to
    every other test in this file.
    """
    duties = _brief().lower().split("## method", 1)[0]
    assert "not a standing hunt" in duties, (
        "the dead-permanence rule must be scoped to a trigger, not a per-reply "
        "obligation: continuity is the shadow advisor's default and runs every turn"
    )
    assert "exception you raise on" in " ".join(duties.split()), (
        "expected the brief to scope the rule to the trigger appearing, as written"
    )


def test_brief_is_honest_about_no_death_record():
    """Nothing in the tree records a death; the brief must say so rather than assume one.

    `canon.KINDS` is `("dialogue", "interaction", "reveal")` with no `death` kind, so an
    advisor that assumes a death is recorded will invent one from a scene that merely
    ended badly.
    """
    low = _brief().lower()
    # Flattened throughout: this file is prose first, and the phrases that carry the
    # honest branch ("no `death` kind", "pin it") fall across line breaks by chance.
    # Asserting on the raw text would pin the wrapping rather than the meaning.
    flat = " ".join(low.split())
    assert "death" in flat, "the brief does not discuss deaths at all"
    assert (
        "do not record" in flat
        or "does not record" in flat
        or "no `death` kind" in flat
    ), "the brief must say what to do when no line records a death"
    assert "pin it" in flat, (
        "with no backend, the brief must ask the GM to record the death rather than "
        "inferring it"
    )


def test_brief_bounds_npc_knowledge_against_npcs_md():
    """The knowledge clause must name the file that holds the fact.

    `templates/npcs.md` already carries `**Secret:**` and `**Knows:**`, and
    `notes_digest` reads it, so naming that file turns this into a fact check against a
    real source rather than a vibe about believability.
    """
    low = _brief().lower()
    assert "npcs.md" in low, (
        "the per-NPC knowledge clause must cite npcs.md, the file that records who knows "
        "what -- otherwise it is unfalsifiable"
    )
    assert "secret" in low, "the clause does not reference the Secret field it polices"


def test_brief_has_a_method_section():
    """A Method section, so the advisor has a defined shape of reply.

    Floor, not ceiling: this keeps passing if Method is one line of mush, and no more is
    claimed for it. `arbiter.md` is the model -- its rewrite added one for the same
    reason.
    """
    low = _brief().lower()
    assert "## method" in low, "the brief has Responsibilities and When Consulted only"
    assert "## when consulted" in low, "the Method section replaced When Consulted"


def test_method_section_requires_citing_the_line_it_rests_on():
    """Scoped to the Method slice on purpose -- see the module docstring.

    Fails on the pre-fix brief because `'method' in low -> False`, which is the only
    honest reason this test exists: the pre-fix Responsibilities list already contained
    the phrase a flat assertion would have matched on.
    """
    method = _method(_brief())
    assert "cite" in method, "Method does not require citing the line it rests on"
    assert "do not settle this" in method or "do not settle" in method, (
        "Method must allow 'the files do not settle this' as an answer"
    )


def test_method_has_an_unresolvable_branch():
    """A brief that can only produce opinions will produce a confident wrong one.

    The 09-29 audit caught a transport error saved as continuity guidance, so the shape
    that matters is: name the line, or decline to rule.
    """
    method = _method(_brief())
    assert "never invent" in method, (
        "Method must forbid inventing a thread, NPC or off-screen event to fill a gap"
    )
    assert "do not settle" in method, (
        "Method must have a branch for a contradiction the files do not resolve"
    )
