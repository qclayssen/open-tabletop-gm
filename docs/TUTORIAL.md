# Tutorial: your first grid fight

This guide gets you playing a tactical grid fight in about five minutes, with nothing but a terminal. You play Kairos, a first-level wizard. The game plays the enemies the way the GM would, and the same rules engine the GM uses in a real session decides every roll, move and spell.

## 1. Set up (once)

You need Python 3.10 or newer. The fight uses SRD monster data, which is built once from the public 5e SRD (network needed):

```bash
python3 systems/dnd5e/build_srd.py --no-fvtt
```

## 2. Start the tutorial

```bash
python3 scripts/tactics/play.py tutorial
```

Two kobolds are climbing into the Training Yard. Kairos spots them first, so they are surprised and lose their first turn (the 5e surprise rule): you get a full turn before anything can hurt you. The game draws the map, then walks you through ten short lessons, one command at a time. Each lesson ends when you have done what it asks, in any order. After the last one you are on your own: win the fight.

Nothing is saved to your campaigns. The fight runs in a throwaway folder that is deleted when you leave (`--keep` keeps it, if you want to look at the combat log or the updated character sheet).

## 3. Reading the map

```
      A  B  C  D  E  F  G  H  I  J  K  L
  1   .  .  .  .  .  .  #  .  .  .  .  .
  2   .  .  .  .  .  .  #  .  .  .  1  .
  3   .  .  .  o  .  .  #  .  .  .  .  .
  4   .  .  .  .  .  .  .  .  .  .  .  .
  5   .  @  .  .  .  .  .  o  .  .  .  .
  6   .  .  .  .  .  .  .  o  .  .  2  .
  7   .  .  .  ,  ,  ,  .  .  .  .  .  .
  8   .  .  .  ,  ,  ,  .  .  .  ~  ~  .
  9   .  .  .  .  .  .  .  .  .  .  .  .
    . ground  # wall  o cover  , difficult  ~ water  x fallen
    1 Kobold 1 K2 5/5 HP AC 12
    @ Kairos (you) B5 8/8 HP AC 15
    2 Kobold 2 K6 5/5 HP AC 12
```

- Each square is 5 feet. A square is named by its column letter and row number: Kairos stands on **B5**.
- **@** is you. Enemies are numbered **1**, **2**, ... and you can use those numbers in commands (`attack 1`). A fallen creature shows as **x**.
- `.` open ground (5 ft to enter), `#` wall (you cannot enter it or see through it), `o` crate or hay bales (10 ft to enter, and half cover: +2 AC for whoever is behind it), `,` mud and `~` water (10 ft to enter).
- The list under the map is the initiative order: who acts first, their square, HP and armour class.

## 4. A turn, step by step

On your turn you can **move** up to your speed (Kairos: 30 ft, six squares), take one **action**, and move again with whatever movement is left. A diagonal step costs 5 ft, the same as a straight one.

| Lesson | You type | What you learn |
|--------|----------|----------------|
| 1 | `map` | Reading the board (above) |
| 2 | `reach` | Squares you can walk to now (`*`) and the ones that need the Dash action (`+`) |
| 3 | `preview D4` | The path and cost of a move, and whether it gives an enemy an opportunity attack. Nothing moves |
| 4 | `move D4` | Moving. The path goes around walls on its own. `undo` takes the move back until you act |
| 5 | `targets` | Every attack you can make from here, with your chance to hit (cover already counted) |
| 6 | `attack 1` | Your best attack at creature 1: Fire Bolt, 1d10 fire |
| 7 | `end` | Ending your turn. The kobolds act next |
| 8 | `spells` | What Kairos knows and what can be cast right now |
| 9 | `cast magic missile 1 1 1` | Spells: three darts that never miss (one target per dart). `area mind sliver 1` shows a save spell's odds first |
| 10 | `dodge`, `disengage`, `dash`, then `end` | Defending yourself, and reactions (below) |

The prompt always shows what you have left: `Kairos 8/8 HP | 20 ft | action ready >`.

### Your dice

In the tutorial you roll your own dice, the way you would at a table. When the game asks, type the number the die shows, **without modifiers** (the game adds Kairos's +5 and spots natural 20s and 1s):

```
Kairos 8/8 HP | 20 ft | action ready > attack 1
Kairos rolls 1d20+5 for Fire Bolt vs Kobold 1.
  Your roll (Enter = roll for me): 14
Kairos Fire Bolt -> Kobold 1: 19 vs AC 12, hit. 7 fire damage; Kobold 1 dies.
```

Press Enter instead to let the game roll. Start with `--auto-dice` if you would rather it always rolls for you.

### Reactions

Some things happen outside your turn, once per round. The game asks with a `[y/n]` question:

- **Shield** (a 1st-level slot): offered only when +5 AC would turn a hit into a miss.
- **Silvery Barbs** (a 1st-level slot): offered when an enemy hits or succeeds; it rerolls and keeps the lower roll.
- **Opportunity attack**: when an enemy walks out of your reach.

Kairos has two 1st-level slots, shared by Shield, Silvery Barbs and Magic Missile. Spend them wisely. `reactions auto` always casts when it would help; `reactions off` stops the questions.

### How the kobolds fight

They are cunning. A kobold steps out from behind the wall, slings a stone, and steps back into cover in the same turn (the rules let a creature split its movement around its action). Stand where the wall or the hay bales block their line of sight and make them come to you. Kobolds are small and weak (5 HP): one good Fire Bolt drops one.

If Kairos drops to 0 HP he is not dead yet: on each of his turns the game asks for a **death save** (a d20: 10 or more is a success). Three successes and he is stable; three failures and he dies.

## 5. Every command

Type `help` in the game for this list.

| Command | Does |
|---------|------|
| `map`, `reach`, `status`, `log [n]` | Look: the board, where you can move, everyone's HP, what just happened |
| `preview D4`, `move D4`, `undo` | Check a move, make it, take it back |
| `targets`, `attack 1 [attack name]` | Your attacks and odds; attack creature 1 (`attack 1 dagger` for a named one) |
| `spells`, `cast <spell> [targets]`, `area <spell> <target>` | Spells: list, cast, preview who and what chance |
| `dash`, `disengage`, `dodge`, `hide`, `stand`, `escape` | The other actions |
| `reactions ask\|auto\|off` | How Shield and Silvery Barbs are offered |
| `end`, `quit` | End your turn; leave the game |

Short forms: `m` map, `r` reach, `s` status, `t` targets, `a` attack, `c` cast, `e` end, `q` quit.

## 6. More fights

```bash
python3 scripts/tactics/play.py kobolds      # the tutorial fight, no lessons
python3 scripts/tactics/play.py mephit       # an ice mephit: Frost Breath is a 15 ft cone, Fire Bolt hurts it double
python3 scripts/tactics/play.py frogs        # two giant frogs: hard. Their bites grapple and restrain
```

Build your own from any map in `display/maps/` and any SRD monster:

```bash
python3 scripts/tactics/play.py --map mage-tower --at B5 --monster "goblin@H4" --monster "goblin@H8"
```

Other options: `--sheet path/to/YourCharacter.md` plays your own character sheet, `--seed N` replays the same enemy dice, `--auto-dice` rolls for you, `--no-color` for plain output. The exit code is 0 for a victory, 3 for a defeat, 4 for a draw (the fight passed round 30) and 5 if you quit.

## 7. On the battle map display

With the display running (`bash display/start-display.sh`), add `--display` and the map, tokens and HP bars appear in the browser as you play in the terminal:

```bash
python3 scripts/tactics/play.py tutorial --display
```

In a real session the GM (the language model) runs the enemies and narrates, and you can play your turns by clicking on that map. See [TACTICAL-COMBAT.md](TACTICAL-COMBAT.md).

## For developers

`play.py` drives the same GM commands as a session (`scripts/tactics/cli.py`). The game side runs `options <enemy>` and then `choose <enemy> 1`, and your commands become `move`, `attack`, `cast` and so on for your token. When a command stops for a roll or a reaction (exit code 2), the game asks you and re-runs it with `--roll` or `--react`, which replays the same enemy dice. The tests in `tests/test_tactics_play.py` play whole fights from a scripted player:

```bash
python3 -m pytest tests/test_tactics_play.py -q
```

The Training Yard map is `display/maps/training-yard.json`. The lessons are `tutorial_lessons()` in `scripts/tactics/play.py`.
