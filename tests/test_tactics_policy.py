"""policy.py: enemy picks with no model (profiles from the stat block, difficulty)."""
from __future__ import annotations

import random

from tests.tactics_fixtures import encounter, frog, kairos, monster, roller, start
from tactics import ai, policy


def fight(*tokens, order=None):
    return start(encounter(list(tokens)), order or [t.id for t in tokens])


def test_profiles_come_from_the_stat_block():
    assert policy.profile(monster("wolf", "wolf-1", (1, 0)))["archetype"] == "pack"
    assert policy.profile(frog())["archetype"] == "beast"
    assert policy.profile(monster("goblin", "goblin-1", (1, 0)))["archetype"] == "skirmisher"
    ghoul = policy.profile(monster("ghoul", "ghoul-1", (1, 0)))
    assert ghoul["fearless"] and ghoul["hungry"]


def test_a_pinned_profile_wins():
    g = monster("goblin", "goblin-1", (1, 0))
    g.extra["ai_profile"] = "brute"
    assert policy.profile(g)["archetype"] == "brute"


def test_same_turn_same_pick():
    enc = fight(frog(pos=(1, 0)), kairos((0, 0)))
    t = enc.tokens["frog-1"]
    opts = ai.options(enc, t, limit=ai.ALL)
    assert len({policy.pick(enc, t, opts, "normal")["n"] for _ in range(20)}) == 1


def test_deadly_takes_the_best_and_easy_stays_in_the_margin():
    enc = fight(frog(pos=(1, 0)), kairos((0, 0)))
    t = enc.tokens["frog-1"]
    opts = ai.options(enc, t, limit=ai.ALL)
    prof = policy.profile(t)
    scores = {o["n"]: policy.rescore(enc, t, o, prof, "easy") for o in opts}
    best = max(scores, key=scores.get)
    assert policy.pick(enc, t, opts, "deadly")["n"] == best
    easy = [policy.pick(enc, t, opts, "easy", random.Random(i))["n"] for i in range(300)]
    assert all(scores[n] >= scores[best] - policy.DIFFICULTY["easy"][1] for n in easy)
    assert len(set(easy)) > 1


def test_a_bloodied_beast_flees():
    f = frog(pos=(1, 0))
    f.hp = 8                                     # 8/18: bloodied, above the 25% morale line
    enc = fight(f, kairos((0, 0)))
    t = enc.tokens["frog-1"]
    assert policy.pick(enc, t, ai.options(enc, t, limit=ai.ALL), "deadly")["kind"] == "retreat"


def test_undead_never_flee():
    g = monster("ghoul", "ghoul-1", (1, 0))
    g.hp = 1
    enc = fight(g, kairos((0, 0)))
    t = enc.tokens["ghoul-1"]
    assert policy.pick(enc, t, ai.options(enc, t, limit=ai.ALL), "deadly")["kind"] != "retreat"


def test_a_downed_pc_is_spared_by_a_beast():
    k = kairos((0, 0), hp=0)
    k.add_condition("unconscious")
    k.add_condition("prone")
    enc = fight(frog(pos=(1, 0)), k)
    t = enc.tokens["frog-1"]
    opts = ai.options(enc, t, limit=ai.ALL)
    for d in ("easy", "normal", "deadly"):
        for i in range(30):
            o = policy.pick(enc, t, opts, d, random.Random(i))
            assert not (o["kind"] in ("attack", "multiattack") and o["target"] == "kairos")


def test_choose_auto_runs_the_pick():
    enc = fight(frog(pos=(1, 0)), kairos((0, 0)))
    data = policy.choose_auto(enc, roller(15, 3, 3), "frog-1", "deadly")
    assert data["profile"] == "beast" and data["text"]


def test_a_healthy_melee_beast_does_not_back_off():
    f = frog(pos=(3, 0))
    enc = fight(f, kairos((0, 0)))
    t = enc.tokens["frog-1"]
    kinds = [o["kind"] for o in ai.options(enc, t, limit=ai.ALL)]
    assert "retreat" not in kinds
