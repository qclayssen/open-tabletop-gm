# Milestone 1: engine core

## Goal

A deterministic Python engine that owns every rule of a grid fight, with hand-checked tests. No UI.

## Shipped

- `scripts/tactics/grid.py`: 5 ft squares, terrain legend, diagonals `5` (2014) or `5-10-5`, Dijkstra movement (difficult terrain, water and swim speed, crawling, creature spaces, no squeezing between corner walls), DMG corner line of sight and cover.
- `scripts/tactics/state.py`: `Encounter`, `Token`, `TurnState`; `<campaign>/combat/encounter.json`, validated on load and save, atomic write, `.bak` of the previous version.
- `scripts/tactics/roller.py`: dice through `scripts/dice.py`'s parser; every roll tagged `engine`, `player`, `verbal` or `fixed`; `PendingRoll` instead of inventing a player's roll.
- `scripts/tactics/rules.py` + `systems/dnd5e/tactics_rules.py`: the thin rules interface and the 2014 rules (advantage from conditions, crits and auto-crits, cover, resistances, temp HP, 0 HP, massive damage, death saves, hit chance).
- `scripts/tactics/engine.py`: initiative, turns, movement with opportunity attacks, Dash, Disengage, Dodge, stand up, undo, death saves at turn start, previews.
- Setup work in the same PR: structured monster `actions` in `systems/dnd5e/build_srd.py`, the `check_input.py` LAN fix, the Kairos fixture and the reference map page.
- Tests: `tests/test_tactics_grid.py`, `test_tactics_rules_dnd5e.py`, `test_tactics_engine.py`, `test_monster_actions.py`, `test_check_input.py`.

## Findings

- **SRD monster actions are prose.** Reach and range exist only in the text. Upstream's damage list also includes rider damage (a bite's poison on a failed save), so it is split into `damage` and `rider_damage`.
- **Upstream data contradicts itself.** Five 5e-bits save actions (the adult red dragon's Fire Breath among them) have a `success_type` that disagrees with their own text. They are flagged `success_conflict` and never resolved automatically.
- **Odd upstream shapes.** The hydra's multiattack count is "Number of Heads", the octopus's Ink Cloud carries an `attack_bonus`, and the druid's attack text starts with a space. All are handled or flagged.
- **Conditional resistances** ("from nonmagical attacks") must stay one entry. Splitting on commas applied them unconditionally.
- **Sight along a wall seam.** A line that runs exactly between two wall squares grazed both and slipped through. Lines are now nudged to each side and count as blocked only if both copies hit.
- **Review pass (10 findings, all fixed with regression tests):** `int()` truncation let sight escape at the top and left map edges; `5-10-5` diagonal parity reset on every move command; `check_input.py` could deliver actions twice; flat damage asked the player for a roll; the opportunity attack preview assumed 5 ft reach; Dodge was not lost at speed 0; plus four cleanups (duplicated average, grid rebuilt per call, double Dijkstra, Dodge stored as a condition).

## Decisions

- User: the engine follows the campaign's `roll_mode`; every roll logs its source; "Roll for me" on every request.
- User: 5e-specific rules live in `systems/dnd5e/`, behind a thin interface documented in `SYSTEM-PORTING.md`.
- User: unparseable monster actions keep their raw text and a flag. Nothing is guessed.
- Implementer: player-controlled reactions (opportunity attacks) are the player's choice (`DecisionNeeded`); GM creatures always take them.
- Implementer: creatures are one square; larger sizes come later.

## Open

- Monster saving throw bonuses are ability modifiers (the SRD build does not carry save proficiencies yet).
- Token sizes larger than one square.
