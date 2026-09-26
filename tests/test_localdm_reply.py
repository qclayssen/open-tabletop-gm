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
