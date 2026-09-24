# Milestone 2: CLI and GM loop

## Goal

One-call GM commands on top of the engine, a GM procedure a small local model can follow, and a scripted demo.

## Shipped

- `scripts/tactics/combat.py` (logic in `scripts/tactics/cli.py`): start, status, options, choose, move, preview, attack, dash, disengage, dodge, stand, death-save, undo-move, end-turn, condition, adjust, log, reachable, targets, end. 1 to 4 plain lines; `--json`; exit code 2 means "waiting for a roll or decision, nothing changed".
- `scripts/tactics/ai.py`: deterministic numbered enemy options with hit chance, expected damage and tags (can finish them, lowest AC, stay out of melee, flee below 25% HP, avoid opportunity attacks).
- `systems/dnd5e/tactics_sheet.py`: markdown sheet to token, and write-back (HP, temp HP, slots, hit dice, death saves, lasting conditions).
- `scripts/tactics/sync.py`: tracker.json, display sidebar, `state.md` Active Combat, and on `end` the campaign sheet (`.bak`, short diff) and a session-log summary.
- `scripts/tactics/maps.py` and `display/maps/*.json`: five player-facing maps, rectangle format, `display/maps/README.md`.
- `scripts/tactics/demo.py`: Kairos against two giant frogs on Frog Pond, in a throwaway campaign.
- GM integration: `scripts/tactics.md`, `/gm combat grid` in `SKILL-commands.md` and `SKILL-branches.md`, `docs/TACTICAL-COMBAT.md`.
- Tests: `tests/test_tactics_cli.py`.

## Findings

- **Module name clash.** `import combat` finds `scripts/combat.py` (the initiative tracker). The CLI logic is imported as `tactics.cli`; `combat.py` is only a wrapper.
- **The demo is deadly.** Two giant frogs (18 HP each, grappling bites) against solo level 1 Kairos (8 HP) is a deadly encounter by 5e math. The demo Kairos usually goes down. A real session probably wants one frog, or allies.
- **Instant death is real.** A crit on an unconscious PC within 5 ft is two death save failures, or instant death if the damage reaches max HP.
- **Kairos's kit** comes from his sheet (Fire Bolt, Mind Sliver, Dagger; Magic Missile, Shield, Mage Armor, Silvery Barbs), not from the original prompt (which listed Ray of Frost).
- **The reference map page** included DM-only maps and undiscovered places. Only the five player-facing maps were ported, and the published copy was stripped before the first push.

## Decisions

- User: `end` writes only the installed campaign copy (`characters/<Name>.md`), after a `.bak`, printing a short diff; lasting conditions are kept, combat-only ones dropped; a 3 to 5 line summary goes to `session-log.md`.
- Implementer: monster riders (the frog's grapple) print as "GM decides the rider"; the GM applies them with `condition`.
- Implementer: `cast` (save spells, Magic Missile) moved to milestone 4 with area templates.

## Open

- Riders are not structured (see milestone 4 questions).
