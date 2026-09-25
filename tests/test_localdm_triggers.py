"""Milestone 6: deterministic moments that call for the advisor."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import triggers        # noqa: E402


def tok(tid, side, hp, max_hp, dead=False):
    return {"id": tid, "name": tid.title(), "side": side, "hp": hp, "max_hp": max_hp,
            "dead": dead, "controller": "player" if side == "pc" else "gm"}


def snap(*tokens, status="active"):
    return {"status": status, "round": 1, "key": ",".join(sorted(t["id"] for t in tokens)),
            "current": None, "tokens": list(tokens)}


def test_nothing_outside_an_active_fight():
    assert triggers.check(None) == []
    assert triggers.check(snap(tok("kairos", "pc", 8, 8), status="ended")) == []


def test_a_new_fight_asks_the_tactician_and_director():
    found = triggers.check(snap(tok("kairos", "pc", 8, 8), tok("frog-1", "enemy", 18, 18)))
    assert [t.key for t in found] == ["start:frog-1,kairos"]
    assert "Frog-1" in found[0].question and found[0].advisors == ("tactician", "director")


def test_boss_down_and_dead():
    s = snap(tok("kairos", "pc", 0, 8), tok("mira", "pc", 0, 10, dead=True),
             tok("ogre", "enemy", 59, 59), tok("frog-1", "enemy", 18, 18))
    keys = [t.key for t in triggers.check(s)]
    k = s["key"]
    assert keys == [f"start:{k}", f"boss:{k}:ogre", f"down:{k}:kairos", f"dead:{k}:mira"]


def test_fresh_filters_seen_keys_and_advisors_merge_in_order():
    found = triggers.check(snap(tok("kairos", "pc", 0, 8), tok("ogre", "enemy", 59, 59)))
    new = triggers.fresh(found, {found[0].key})
    assert [t.key.split(":")[0] for t in new] == ["boss", "down"]
    assert triggers.advisors_for(found) == ["tactician", "director"]
    assert triggers.question(new).count("?") == 2
