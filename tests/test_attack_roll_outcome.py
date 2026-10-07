"""Hit and damage live on the d20 attack roll, not beside it and not on the damage die.

One attack appends two Rolls: the d20, then (only on a hit) the damage dice.
The display reads one outcome off the d20. A miss sets hit to False and leaves
damage unset, so the dict has no damage key: 0 would mean a hit that dealt
nothing. Saves and damage dice never have to supply either field.
"""
from __future__ import annotations

from tests.fake_rules import RULES as FAKE
from tests.tactics_fixtures import RULES, frog, goblin, kairos, roller
from tactics.roller import Roll
from tactics.rules import AttackContext

MELEE = AttackContext(distance=5, melee=True)
RANGED = AttackContext(distance=30, melee=False)


def scimitar(g):
    return next(a for a in g.attacks if a["name"] == "Scimitar")


def test_a_non_attack_roll_does_not_have_to_supply_hit_or_damage():
    """Defaults, and neither key on the wire. A save is not a miss."""
    rec = Roll("k", "longsword", "1d20+3", [11], 11, 14, "engine")
    assert rec.hit is None and rec.damage is None
    wired = rec.to_dict()
    assert "hit" not in wired and "damage" not in wired
    r = roller(4)
    RULES.saving_throw(frog(), "dex", 15, r, player=False)
    save = r.log[0].to_dict()
    assert "hit" not in save and "damage" not in save


def test_a_hit_puts_the_outcome_on_the_d20_and_not_on_the_damage_die():
    # Goblin scimitar +4 vs Kairos AC 12: d20 8 -> 12, hits. 1d6+2 with a 3 -> 5.
    r = roller(8, 3)
    k = kairos()
    res = RULES.attack(goblin(), k, scimitar(goblin()), MELEE, r, player=False)
    assert res["hit"] and res["damage"]["total"] == 5
    attack, damage = r.log
    wired = attack.to_dict()
    assert wired["hit"] is True
    assert wired["damage"] == 5 and isinstance(wired["damage"], int)
    assert wired["damage"] == res["damage"]["total"]
    other = damage.to_dict()
    assert "hit" not in other and "damage" not in other


def test_a_miss_has_hit_false_and_no_damage_key():
    # Natural 1 misses even with a huge bonus, and no damage die is rolled.
    big = dict(scimitar(goblin()), bonus=30)
    r = roller(1)
    res = RULES.attack(goblin(), kairos(), big, MELEE, r, player=False)
    assert res["hit"] is False and res["damage"] is None
    assert len(r.log) == 1
    wired = r.log[0].to_dict()
    assert wired["hit"] is False
    assert "damage" not in wired


def test_a_hit_for_nothing_keeps_damage_zero():
    """Immunity is a hit that dealt 0. That is not a miss, so the key stays."""
    # Fire Bolt +6 vs frog AC 11: d20 5 -> 11, hits. 1d10 of 4, immune to fire -> 0.
    f = frog()
    f.immunities = ["fire"]
    r = roller(5, 4)
    res = RULES.attack(kairos(), f, kairos().attacks[0], RANGED, r, player=False)
    assert res["hit"] and res["damage"]["total"] == 0
    wired = r.log[0].to_dict()
    assert wired["hit"] is True and wired["damage"] == 0
    assert "hit" not in r.log[1].to_dict() and "damage" not in r.log[1].to_dict()


def test_the_toy_system_puts_the_same_outcome_on_its_d20():
    """fake_rules used to set only odds. A coverage run would stay green while
    the toy stopped matching the roll the real rules hand the display."""
    # d20 10 + 3 vs frog AC 11 hits. 1d6 of 4, no mitigation, so total is 4.
    atk = {"name": "Club", "damage": [{"dice": "1d6", "type": "blunt"}]}
    r = roller(10, 4)
    res = FAKE.attack(kairos(), frog(), atk, MELEE, r, player=False)
    assert res["hit"]
    attack, damage = r.log
    wired = attack.to_dict()
    assert wired["hit"] is True
    assert wired["damage"] == res["damage"]["total"] == 4
    assert "hit" not in damage.to_dict() and "damage" not in damage.to_dict()
    miss = roller(1)
    FAKE.attack(kairos(), frog(), atk, MELEE, miss, player=False)
    missed = miss.log[0].to_dict()
    assert missed["hit"] is False and "damage" not in missed
