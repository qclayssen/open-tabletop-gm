"""dice.py's injectable, secrets-seeded RNG."""
import random
import dice
from tactics.roller import Roller


def test_default_seed_is_secret_and_recorded():
    a, b = dice.new_rng(), dice.new_rng()
    assert a.seed_value != b.seed_value
    assert dice.new_rng(a.seed_value).random() == a.random()


def test_seeded_rng_is_replayable():
    r1 = [dice.run("2d6+1", silent=True, rng=dice.new_rng(7)) for _ in range(1)]
    r2 = [dice.run("2d6+1", silent=True, rng=dice.new_rng(7)) for _ in range(1)]
    assert r1 == r2


def test_seed_default_and_restore():
    prev = dice.get_rng()
    try:
        dice.seed_default(42)
        a = [dice.run("d20", silent=True) for _ in range(5)]
        dice.seed_default(42)
        assert a == [dice.run("d20", silent=True) for _ in range(5)]
    finally:
        dice.set_rng(prev)


def test_module_random_untouched_and_patchable(monkeypatch):
    state = random.getstate()
    dice.run("d20", silent=True, rng=dice.new_rng(1))
    assert random.getstate() == state
    monkeypatch.setattr(random, "randint", lambda a, b: 20)   # play.py's seam
    assert random.randint(1, 20) == 20


def test_roller_default_rng_has_seed():
    assert isinstance(Roller().rng.seed_value, int)
