"""The terminal game (scripts/tactics/play.py): a human plays the PC, the game plays the GM.

Each test plays a whole fight in-process with a scripted player. Monster
lookups use the real SRD records in tests/fixtures, as in test_tactics_cli,
plus the kobold (srd_monsters_play.json, the same upstream 5e-bits source; kept
apart so the golden lookup file in test_monster_actions stays as it was).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys

import pytest

from tests.tactics_fixtures import ROOT, RULES, _build, _RAW
from tactics import maps

rules_mod = sys.modules[type(RULES).__module__]
MONSTERS = dict(_RAW, **{r["index"]: r for r in json.loads(
    (ROOT / "tests" / "fixtures" / "srd_monsters_play.json").read_text(encoding="utf-8"))})
_spec = importlib.util.spec_from_file_location("tactics_play", ROOT / "scripts" / "tactics" / "play.py")
play = importlib.util.module_from_spec(_spec)
sys.modules["tactics_play"] = play                  # the dataclass looks its module up
_spec.loader.exec_module(play)


@pytest.fixture(autouse=True)
def srd(monkeypatch):
    monkeypatch.setattr(rules_mod, "_lookup_monster",
                        lambda name: _build._norm_monster(MONSTERS[name.lower().replace(" ", "-")]))


class Player:
    """Answers the game's prompts: y/n reactions, dice (Enter: the game rolls),
    and at `> ` the scripted command, else the one a fresh lesson suggests,
    else free play."""

    def __init__(self, script=(), react="y", free=("attack 1", "attack 2", "end"), limit=400):
        self.script, self.react, self.free = list(script), react, list(free)
        self.out, self.asked, self.queue, self.limit = [], [], [], limit
        self.seen = 0

    def say(self, *parts):
        self.out.append(" ".join(str(p) for p in parts))

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        if len(self.asked) > self.limit:
            raise EOFError
        if "[y/n]" in prompt:
            return self.react
        if "Your roll" in prompt:
            return ""
        if self.script:
            return self.script.pop(0)
        new = "\n".join(self.out[self.seen:])
        self.seen = len(self.out)
        lesson = re.search(r"\[Lesson \d+/\d+: [^\]]+\]\n(.*)", new, re.S)
        cmd = re.search(r"`([^`]+)`", lesson.group(1)) if lesson else None
        if cmd:
            return cmd.group(1)
        if not self.queue:
            self.queue = list(self.free)
        return self.queue.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.out)


def game(argv, player) -> int:
    return play.main([*argv, "--no-color"], ask=player.ask, out=player.say)


def test_the_training_yard_map_compiles():
    rows = maps.load("training-yard")["grid"]["rows"]
    assert len(rows) == 9 and len(rows[0]) == 12
    assert rows[0][6] == "#" and rows[4][7] == "o" and rows[6][3] == "," and rows[7][9] == "~"


def test_the_tutorial_teaches_every_lesson_then_the_fight_ends():
    p = Player()
    code = game(["tutorial", "--seed", "2"], p)
    for n, lesson in enumerate(play.tutorial_lessons(), 1):
        assert f"[Lesson {n}/10: {lesson.title}]" in p.text
    assert "[Tutorial complete]" in p.text or code == 0    # a quick win can end it early
    assert code in (0, 3) and ("Victory!" in p.text or "Defeat." in p.text)
    assert "Combat ended after round" in p.text
    assert "Kairos moves B5 to D4" in p.text             # the move lesson really moved him
    assert "AC 15" in p.text                             # Mage Armor already up in the tutorial
    assert "Next: options" not in p.text and "Then: end-turn" not in p.text
    # Surprise: whatever the initiative order, each kobold's first turn is lost.
    for name in ("Kobold 1", "Kobold 2"):
        first = p.text.split(f"-- {name}'s turn --", 1)
        assert len(first) == 1 or first[1].lstrip().startswith("surprised: loses its turn")
    assert "surprised: loses its turn" in p.text
    assert "@ Kairos (you) B5" in p.text                 # @, not K: column K holds the kobolds


def test_lessons_complete_in_any_order_and_end_only_counts_once():
    g = play.Game.__new__(play.Game)
    g.lessons, g.paint, shown = play.tutorial_lessons(), lambda text, code: text, []
    g.out, g.show_lesson = shown.append, lambda: None
    g.learn("attack")                                    # out of order: lesson 6 is done
    assert [ls.done for ls in g.lessons][:7] == [False] * 5 + [True, False]
    g.learn("end")
    assert g.lessons[6].done and not g.lessons[9].done   # `end` ticks one lesson at a time
    for verb in ("map", "reach", "preview", "move", "targets", "spells", "cast", "end"):
        g.learn(verb)
    assert all(ls.done for ls in g.lessons) and any("[Tutorial complete]" in s for s in shown)


def test_the_player_rolls_their_own_dice_unless_auto_dice():
    p = Player(script=["attack 1", "end"])
    game(["kobolds", "--seed", "2"], p)
    assert any("Your roll" in q for q in p.asked)
    p = Player(script=["attack 1", "end"])
    game(["kobolds", "--seed", "2", "--auto-dice"], p)
    assert not any("Your roll" in q for q in p.asked)


def test_a_typed_roll_is_used_as_the_die_face():
    rolls = iter(["20", "6"])                            # a natural 20, then the damage dice
    p = Player(script=["attack 1", "quit"])
    ask = p.ask
    p.ask = lambda q: next(rolls, "") if "Your roll" in q else ask(q)
    game(["kobolds", "--seed", "5"], p)
    assert re.search(r"Fire Bolt -> Kobold 1: 25 vs AC 1\d.*hit \(CRIT\)", p.text)


def test_map_symbols_stand_for_creatures_and_squares_are_left_alone():
    p = Player(script=["preview D4", "targets", "quit"])
    code = game(["kobolds", "--seed", "2"], p)
    assert code == play.RESULT_CODES["quit"]
    assert "Kairos to D4:" in p.text
    assert "Fire Bolt -> Kobold 1" in p.text and "Fire Bolt -> Kobold 2" in p.text
    assert "You leave the fight." in p.text


def test_resolve_maps_symbols_but_not_flag_values():
    g = play.Game.__new__(play.Game)
    g.symbols = lambda enc: {"kairos": "K", "kobold-1": "1", "kobold-2": "2"}
    g.enc = lambda: None
    assert g.resolve(["magic", "missile", "1", "2", "--level", "2"]) == \
        ["magic", "missile", "kobold-1", "kobold-2", "--level", "2"]
    assert g.resolve(["D4"]) == ["D4"]


def test_reach_marks_walking_and_dash_squares():
    p = Player(script=["reach", "quit"])
    game(["kobolds", "--seed", "2"], p)
    assert "* walk  + Dash" in p.text
    board = p.text.split("* walk  + Dash")[0]
    row5 = [ln for ln in board.splitlines() if ln.startswith("  5 ")][-1]
    assert "*" in row5 and "+" in row5


def test_the_prompt_shows_hp_movement_and_action():
    p = Player(script=["move D4", "quit"])
    game(["tutorial", "--seed", "2"], p)
    assert "Kairos 8/8 HP | 30 ft | action ready > " in p.asked
    assert "Kairos 8/8 HP | 20 ft | action ready > " in p.asked


def test_an_enemy_turn_is_its_own_block_one_event_per_line():
    p = Player(script=["end", "quit"])
    game(["kobolds", "--seed", "2"], p)
    block = p.text.split("-- Kobold 1's turn --\n", 1)[1].split("\n-- ")[0]
    assert all(line.startswith("  ") for line in block.splitlines())
    assert len(block.splitlines()) >= 2


def test_help_unknown_and_incomplete_commands_do_not_end_the_turn():
    p = Player(script=["help", "fly D4", "move", "quit"])
    game(["kobolds", "--seed", "2"], p)
    assert "Commands (squares like D4" in p.text
    assert "Unknown command 'fly'" in p.text
    assert "`move` needs more" in p.text


def test_end_of_input_leaves_the_game_and_cleans_up(tmp_path, monkeypatch):
    folder = tmp_path / "t"
    folder.mkdir()
    monkeypatch.setattr(play.tempfile, "mkdtemp", lambda prefix: str(folder))

    def eof(_prompt):
        raise EOFError

    code = play.main(["kobolds", "--seed", "1", "--auto-dice", "--no-color"], ask=eof,
                     out=lambda *a: None)
    assert code == play.RESULT_CODES["quit"]
    assert not folder.exists()


def test_a_fight_of_your_own_on_any_map():
    p = Player(react="n")
    code = game(["--map", "frog-pond", "--at", "B7", "--monster", "goblin@F7", "--auto-dice",
                 "--seed", "3"], p)
    assert "Grid combat on Frog Pond" in p.text and "Goblin" in p.text
    assert code in (0, 3, 4)
    assert "Combat ended after round" in p.text


@pytest.mark.parametrize("scenario", ["frogs", "mephit"])
def test_the_other_scenarios_play_to_the_end(scenario):
    p = Player()
    code = game([scenario, "--seed", "1", "--auto-dice"], p)
    assert code in (0, 3, 4), p.text[-400:]
    assert "Combat ended after round" in p.text
