"""Engine-owned check policy (scripts/localdm/checks.py) and its wiring in play.py."""
from __future__ import annotations

import random
import sys

from tests.localdm_fakes import FakeBridge, FakeClient, fixed_check_roll
from localdm import checks, reply
from localdm.play import Session
from tests.test_localdm_play import MODELS, NULLS, camp_dir

SHEET = ("## Skills\n| Skill | Ability | Bonus |\n|---|---|---|\n"
         "| Stealth | Dex | +4 |\n| Perception | Wis | +2 |\n| Investigation | Int | +6 |\n")


def test_tier_table_is_the_2014_one():
    assert [checks.tier_dc(t) for t in
            ("very easy", "easy", "Moderate", "hard", "very_hard", "nearly impossible")] == \
        [5, 10, 15, 20, 25, 30]
    assert checks.tier_dc("banana") is None


def test_named_tier_resolves_to_dc_and_legacy_number_passes_through():
    assert checks.parse_request("Perception hard").dc == 20
    assert checks.parse_request("Perception DC 13").dc == 13
    assert checks.parse_request("Perception 13", strict=True).dc == 15   # snapped to a tier
    assert checks.parse_request("Perception").dc == 15                   # no level: moderate
    assert checks.parse_request("Perception 99").dc == 30


def test_passive_arithmetic():
    assert checks.passive_score(2) == 12
    assert checks.passive_score(2, 1) == 17 and checks.passive_score(2, -1) == 7


def test_passive_beats_dc_means_no_roll_but_not_under_time_pressure():
    led = checks.Ledger()
    easy = checks.parse_request("Perception easy", {"stakes": "x"})
    assert checks.decide(easy, actor="Kairos", bonus=2, ledger=led).kind == "auto_success"
    hard = checks.parse_request("Perception moderate", {"stakes": "x"})
    assert checks.decide(hard, actor="Kairos", bonus=2, ledger=led).kind == "roll"
    rushed = checks.parse_request("Perception easy", {"stakes": "x", "time_pressure": True})
    assert checks.decide(rushed, actor="Kairos", bonus=2, ledger=led).kind == "roll"
    stealth = checks.parse_request("Stealth easy", {"stakes": "x"})
    assert checks.decide(stealth, actor="Kairos", bonus=9, ledger=led).kind == "roll"


def test_a_roll_needs_stakes():
    led = checks.Ledger()
    hard = checks.parse_request("Stealth hard", {"stakes": ""})
    assert checks.decide(hard, actor="K", bonus=4, ledger=led).kind == "no_stakes"
    easy = checks.parse_request("Stealth easy", {"stakes": "  "})
    assert checks.decide(easy, actor="K", bonus=4, ledger=led).kind == "auto_success"
    legacy = checks.parse_request("Stealth 16")            # no stakes key at all
    assert checks.decide(legacy, actor="K", bonus=4, ledger=led).kind == "roll"
    assert checks.decide(legacy, actor="K", bonus=4, ledger=led, mode="strict").kind == "no_stakes"


def test_identical_retry_is_refused_unless_circumstances_changed():
    led = checks.Ledger()
    led.record_failure("Kairos", "Investigation", "desk")
    same = checks.parse_request("Investigation hard", {"stakes": "x", "target": "Desk"})
    assert checks.decide(same, actor="Kairos", bonus=6, ledger=led).kind == "refused"
    other = checks.parse_request("Investigation hard", {"stakes": "x", "target": "shelf"})
    assert checks.decide(other, actor="Kairos", bonus=6, ledger=led).kind == "roll"
    changed = checks.parse_request("Investigation hard",
                                   {"stakes": "x", "target": "desk", "changed": True})
    assert checks.decide(changed, actor="Kairos", bonus=6, ledger=led).kind == "roll"
    led.begin()                                             # a new scene
    assert checks.decide(same, actor="Kairos", bonus=6, ledger=led).kind == "roll"


def test_object_form_is_unwrapped_by_the_reply_parser():
    r = reply.parse('Kairos peers.\n{"escalate": null, "check": {"skill": "Perception", '
                    '"tier": "moderate", "stakes": "the guard hears", "target": "door"}}')
    assert r.check == "Perception moderate"
    assert r.check_meta == {"stakes": "the guard hears", "target": "door"}
    assert reply.parse('Hi.\n{"check": "Stealth 13"}').check_meta is None
    assert reply.parse('Hi.\n{"check": {"tier": "easy"}}').check is None


# --- scripted scenario through the Session, harness style -------------------------------

def _session(tmp_path, directives):
    camp = camp_dir(tmp_path)
    (camp / "characters").mkdir()
    (camp / "characters" / "Kairos.md").write_text(SHEET, encoding="utf-8")
    it = iter(directives)

    state = {"i": 0}

    def next_turn(msgs):
        task = msgs[-1]["content"]
        if "First decide the outcome" in task:
            d = directives[state["i"]]
            state["i"] += 1
            return "Kairos begins.\n" + d
        return "The world answers." + NULLS

    return Session("demo", FakeClient(lambda m, msgs, role: next_turn(msgs)), MODELS, camp_dir=camp, bridge=FakeBridge())


def _roll_counter(monkeypatch, face):
    """Count skill-check rolls and pin the face.

    This patched the `random` module, which stopped working when `play.py` moved
    its check onto `dice.new_rng()` -- the patch became a no-op and these
    assertions silently stopped counting. The seam is `play._CHECK_RNG` now; see
    `tests.localdm_fakes.fixed_check_roll`.
    """
    return fixed_check_roll(monkeypatch, face)


def test_scenario_passive_stakes_and_retry(tmp_path, monkeypatch):
    def chk(**k):
        import json
        return json.dumps({"escalate": None, "command": None, "check": k})
    s = _session(tmp_path, [
        chk(skill="Perception", tier="easy", stakes="a hidden guard"),          # passive 12 >= 10
        chk(skill="Investigation", tier="very hard", stakes="the ledger burns", target="desk"),
        chk(skill="Investigation", tier="very hard", stakes="the ledger burns", target="desk"),
        chk(skill="Stealth", tier="hard", stakes=""),                            # no stakes
    ])
    rolls = _roll_counter(monkeypatch, 3)
    t1 = "\n".join(s.handle("I look around."))
    assert "Passive Perception 12 meets DC 10" in t1 and not rolls
    t2 = "\n".join(s.handle("I search the desk."))
    assert len(rolls) == 1 and "against DC 25: failure" in t2
    t3 = "\n".join(s.handle("I search the desk again."))
    assert len(rolls) == 1 and "already tried Investigation (desk)" in t3
    t4 = "\n".join(s.handle("I sneak."))
    assert len(rolls) == 1 and "no stakes" in t4


def test_policy_off_restores_the_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("GM_CHECK_POLICY", "off")
    s = _session(tmp_path, ['{"escalate": null, "command": null, "check": "Perception 10"}'])
    rolls = _roll_counter(monkeypatch, 9)
    text = "\n".join(s.handle("I look around."))
    assert len(rolls) == 1 and "against DC 10" in text
