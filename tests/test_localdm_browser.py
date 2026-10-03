"""The local DM played from the browser: display directives, ability checks, party push,
campaign notes for the DM and advisors, and a resumed scene."""
from __future__ import annotations

import json

from tests.localdm_fakes import FakeBridge, FakeClient
from localdm import context, llm
from localdm.memory import Memory
from localdm.play import Session

NULLS = '\n{"escalate": null, "command": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")
SHEET = """# Kairos
## Identity
- **Race:** Kenku (Multiverse) | **Class:** Wizard 1 | **Level:** 1
## Combat Stats
- **HP:** 6 / 8 | **Temp HP:** 0
- **AC:** 12 | **Initiative:** +2 | **Speed:** 30 ft
## Skills
| Skill | Ability | Bonus | Proficient |
|-------|---------|-------|-----------|
| Insight | WIS | +3 | - |
| Investigation | INT | +5 | yes |
## Known Spells / Cantrips
- **Cantrips:** Fire Bolt, Mind Sliver
"""
TEMPLATE_WORLD = """# World
## Factions
- **Goals:**
- **Attitude toward party:** neutral
### Three Truths
- **Obvious:**
| Name | Role |
|------|------|
"""


def camp(tmp_path, **files):
    d = tmp_path / "demo"
    (d / "characters").mkdir(parents=True, exist_ok=True)
    (d / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    (d / "characters" / "Kairos.md").write_text(SHEET, encoding="utf-8")
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def session(tmp_path, responder=None, **files):
    c = FakeClient(responder or (lambda m, msgs, role: "Rain falls." + NULLS))
    return c, Session("demo", c, MODELS, camp_dir=camp(tmp_path, **files), bridge=FakeBridge())


def last_user(c):
    return c.calls[-1][2][1]["content"]


def test_party_stats_reads_the_sheet(tmp_path):
    (p,) = context.party_stats(camp(tmp_path))
    assert p["name"] == "Kairos" and p["race"] == "Kenku" and p["class"] == "Wizard"
    assert p["hp"] == {"current": 6, "max": 8, "temp": 0} and p["ac"] == 12
    assert p["initiative"] == "+2" and p["speed"] == 30


def test_skill_bonus_from_the_sheet(tmp_path):
    d = camp(tmp_path)
    assert context.skill_bonus(d, "insight") == ("Kairos", "Insight", 3)
    assert context.skill_bonus(d, "Basketry") is None


def test_a_blank_notes_template_costs_nothing(tmp_path):
    assert context.notes_digest(camp(tmp_path, **{"world.md": TEMPLATE_WORLD})) == ""


def test_filled_notes_reach_the_dm_and_the_advisors(tmp_path):
    c, s = session(tmp_path, **{"npcs.md": "| Name | Role |\n|---|---|\n| Bress | Innkeeper |\n"})
    s.handle("I look around.")
    # Both facts used to be asserted against the system message specifically.
    # They are campaign-derived, so they now travel in the user message, which
    # is what keeps the system message a static cache prefix. What this test is
    # for is that the notes REACH the DM at all, so it reads the whole payload.
    sent = "\n".join(m["content"] for m in c.calls[0][2])
    assert "Bress" in sent
    assert "Player character: Known Spells / Cantrips" in sent
    assert "Bress" in context.notes_digest(s.camp_dir)


def test_small_length_directive_is_a_cap_and_the_default_is_ignored(tmp_path):
    c, s = session(tmp_path)
    s.handle("[[Narration length for this turn: aim for ~150 words.]] I wave.")
    assert "Narration cap: 150 words at most." in last_user(c)
    s.handle("[[Narration length for this turn: aim for ~500 words.]] I wave again.")
    assert "Narration" not in last_user(c).split("## Player now")[-1]
    assert "I wave again." in last_user(c) and "[[" not in last_user(c)


class _FixedRoll:
    """The face this test needs, without touching the `random` module.

    This used to be `monkeypatch.setattr("localdm.play.random.randint", ...)`, which
    reached into the module-level generator to force a 12 -- so the rest of the
    process drew from a patched generator for the rest of the test. The check now
    rolls off this module's own stream, so forcing a face is a local stub. The
    `(1, 20)` assertion is the part worth keeping: the check is still a d20.
    """
    seed_value = 12

    def randint(self, low, high):
        assert (low, high) == (1, 20)
        return 12


def test_ability_check_rolls_then_the_dm_narrates_the_outcome(tmp_path, monkeypatch):
    replies = iter(['You start to search the shed.\n{"escalate": null, "command": null, '
                    '"check": "Investigation 16"}', "Behind the crates, a torn sleeve." + NULLS])
    c, s = session(tmp_path, lambda m, msgs, role: next(replies))
    monkeypatch.setattr("localdm.play._CHECK_RNG", _FixedRoll())
    out = s.handle("I search the shed.")
    assert out[0] == "You start to search the shed."
    assert "17 against DC 16: success" in out[1]
    assert "Kairos rolled an Investigation check: 17 against DC 16" in last_user(c)
    assert out[-1] == "Behind the crates, a torn sleeve."


def test_check_is_rolled_in_the_browser_when_a_display_is_up(tmp_path):
    # The outcome narration names a cost (reply.is_costless_failure): the guard in
    # _check_narration rewrites a failure that moved the world for free, which costs a
    # second DM call. This test is about the roll reaching the display, not the rewrite.
    replies = iter(['You watch her face.\n{"check": "Insight 16"}',
                    "Her eyes flick away, and the guard beside her turns toward you." + NULLS])
    c, s = session(tmp_path, lambda m, msgs, role: next(replies))

    class Display:
        registered = True
        asked = []

        def narrate(self, text):
            self.asked.append(("narrate", text))

        def request_roll(self, who, mod, label, dc):
            self.asked.append(("roll", who, mod, label, dc))
            return 4

    s.display = Display()
    out = s.handle("I read her face.")
    assert Display.asked == [("narrate", "You watch her face."), ("roll", "Kairos", 3, "Insight check", 16)]
    assert "4 against DC 16: failure" in out[1]


def test_a_new_campaign_resumes_from_the_scene_the_display_showed(tmp_path):
    d = camp(tmp_path)
    (d / "session_tail.json").write_text(json.dumps(
        [{"text": "Rain needles the shutters."}, {"text": "Maddoc has not come home."}]), encoding="utf-8")
    c, s = session(tmp_path)
    assert [t["role"] for t in s.memory.turns()] == ["dm", "dm"]
    s.handle("I listen.")
    assert "Maddoc has not come home." in last_user(c)
    assert Memory(d).seed_from_tail() == 0                # only when nothing is remembered yet
