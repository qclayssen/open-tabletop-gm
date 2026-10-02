"""Audit H1: a long rest must wake a character who was dropped to 0 HP.

`rest long` wrote token.hp itself, so it healed a creature at 0 HP back to full
but left the `unconscious` condition that damage() applied. Every later
move/attack/cast was then refused: Rules.can_act is False while that condition
stands, whatever the HP. The fix routes the heal through Rules.heal, the one
path that already clears the condition on the 0 -> above-0 transition.

5e reference: a creature at 0 HP is unconscious (PHB p197); a long rest restores
hit points (PHB p186), so a character who was at 0 and is now above 0 is awake.
"""
from __future__ import annotations

from tests.tactics_fixtures import RULES, encounter, kairos
from tactics import rest
from tactics.core import rules_for


def test_long_rest_wakes_a_pc_dropped_to_zero():
    k = kairos()                                  # 8/8 HP, AC 12, a PC who rolls death saves
    enc = encounter([k])

    # Drop to 0 through the real damage path: 8 damage at 8 HP lands on exactly
    # 0 with no overflow, so it is not an instant death and it applies the
    # unconscious + prone pair a real drop does.
    RULES.damage(k, [{"amount": 8, "type": "piercing"}])
    assert k.hp == 0 and k.has("unconscious"), "precondition: the drop applied unconscious"

    lines = rest.long_rest(enc)

    assert k.hp == k.max_hp == 8, lines
    assert not k.has("unconscious"), lines          # before fix: still unconscious
    assert rules_for(enc).can_act(k), lines         # before fix: False, character bricked
    assert any("healed 8 HP" in line for line in lines), lines
