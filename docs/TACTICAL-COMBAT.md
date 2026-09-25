# Tactical grid combat

Turn-based combat on a 5 ft grid, in the spirit of Baldur's Gate 3's turn-based mode, using D&D 5e (2014) rules. A Python engine owns every rule: positions, movement, reach, line of sight, cover, initiative, hit rolls, damage, spells and their areas, saving throws, concentration, reactions, conditions and death saves. The GM (the language model) only narrates and makes choices. That keeps fights fair and consistent even on a small local model.

Theatre-of-the-mind combat (`/gm combat start`) is unchanged and still available.

## For the player

**Starting a fight.** The GM starts one with `/gm combat grid <map>`, or you can ask for it. With the display running, the battle map appears on its own above the story, and hides again when the fight ends. Without the display, everything is plain text in the chat.

**On the map (your turn).** Click Kairos to see where you can go: shaded squares are walking range (darker is closer), dashed squares need the Dash action. Point at a square to see the path and the feet it costs, with a red path and a warning if you would provoke an opportunity attack. Click to move (on a phone: tap to preview, tap again to go). Then pick an action: **Attack** highlights every valid target with your chance to hit; click one. **Cast** opens your spell list: an area spell shows its template as you point at squares, with every creature caught and its chance to fail the save (allies in the area are flagged before you commit); a single-target spell shows your chance to hit or theirs to fail. **Dash**, **Disengage**, **Dodge**, **Undo move** and **End turn** are one click. When the engine needs your dice, type what you rolled, press **Roll the dice** to roll in the browser, or **Roll for me**. Everything you do is also sent to the GM, who narrates it. **Hide map** folds the panel away when you want to read.

**Your turn.** Say what you do, as you normally would: "I move to D5 and cast Fire Bolt at the frog." The engine checks it is legal, and the GM narrates the result. You can:

- move up to your speed (difficult terrain and water cost double; diagonals cost 5 ft),
- take one action: attack, cast a spell, Dash, Disengage, Dodge, Help, Hide, Ready, or escape a grapple,
- cast a bonus-action spell (then only a 1-action cantrip is allowed that turn, as the rules say),
- change your mind about movement before you act ("undo that move").

**Your dice.** With `roll_mode: players` (the default), you roll your own attacks, damage and death saves. The GM asks for the number on the die, without modifiers (the engine adds them, and spots natural 20s and 1s). Say "roll it for me" any time to let the engine roll.

**Opportunity attacks.** Leaving an enemy's reach gives it a free attack, unless you Disengage first. Before you move, the GM can show you who would get one and their chance to hit. When an enemy leaves *your* reach, you're asked whether you want to take yours.

**Spells.** The engine spends the slot (or not, for a cantrip), checks range and line of sight, rolls one damage roll for everyone in an area, and a save for each creature (cover helps DEX saves). It tracks concentration (a hit asks for a CON save; a new concentration spell ends the old one) and effects with a timer, such as Mind Sliver's penalty until the end of your next turn. A spell it cannot run (an illusion, a utility spell) still costs its slot, and the GM narrates it. Magic Missile: name one target, or one per dart.

**Reactions.** Shield and Silvery Barbs are offered only when they would change the outcome: Shield when +5 AC turns a hit into a miss (never against a natural 20) or when Magic Missile flies at you; Silvery Barbs when an enemy hits or makes its save. Tell the GM "stop asking" (`reactions off`) or "always" (`auto`).

**Help, Hide, Ready.** Help gives an ally advantage on their next attack against an enemy within 5 ft of you. Hide needs you out of every enemy's sight (total cover); your Stealth then has to meet or beat each enemy's passive Perception. Attacking or casting gives your position away. Ready holds an attack, a spell (the slot is spent and held with concentration) or a move for a trigger you describe; the GM releases it when the trigger happens.

**Riders and specials.** Common monster riders are applied exactly as written: a giant frog's bite grapples and restrains (escape DC 11, use your action to escape), a wolf's bite knocks you prone on a failed save, a spider's poison halves on a successful one. Anything conditional ("unless the target is an elf") is left to the GM. The engine never guesses at rules it can't read exactly.

**After the fight.** Your HP, spent spell slots, death saves and any lasting conditions (exhaustion, or poison with time left) are written to your campaign sheet. The previous version is kept as `Kairos.md.bak`. Short-lived conditions (prone, grappled, frightened) end with the fight. A short summary goes into the session log.

## GM command reference

All commands: `python3 scripts/tactics/combat.py -c <campaign> <command> ...`. Each prints 1 to 4 lines; add `--json` for the full result. The procedure the GM follows is in `scripts/tactics.md`.

| Command | What it does |
|---------|--------------|
| `start <map> --pc NAME@SQ --monster "SRD NAME@SQ" [--ally ...] [--roll-mode players\|auto]` | Places tokens, rolls initiative, starts round 1 |
| `status` | Round, whose turn, movement left, everyone's square and HP |
| `options <id>` | Numbered choices for a GM-controlled creature, with hit chance and expected damage |
| `choose <id> <n>` | Runs option n |
| `move <id> <square>` | Moves along the cheapest path; opportunity attacks resolve on the way |
| `preview <id> <square>` | Cost of a move and who it would provoke, without moving |
| `attack <id> <target> [attack]` | One attack (weapon or attack cantrip), with cover and advantage worked out |
| `dash` / `disengage` / `dodge` / `stand <id>` | Actions, and standing up from prone (half your speed) |
| `death-save <id>` | The dying creature's death save |
| `undo-move` | Takes back the last move, until an action, reaction or roll locks it in |
| `end-turn` | Next creature in initiative (skips the dead, starts new rounds) |
| `cast <id> "<spell>" [target\|square ...] [--level N]` | Casts a spell: attack, save, area, darts, healing, Mage Armor, or narrated |
| `preview-area <id> "<spell>" <square\|target>` | Who a spell would catch, chance to fail, expected damage, allies flagged |
| `spells <id>` | Known spells, how they target, and why one cannot be cast now |
| `use <id> "<action>" <square>` | A monster's area action (breath weapon), with its recharge |
| `help <id> <ally> <target>` / `hide <id>` / `escape <id>` | Help, Hide (Stealth vs passive Perception), escape a grapple |
| `ready <id> attack\|cast\|move [what] --target X --trigger "..."` | Ready an action for a trigger |
| `trigger <id> [target]` | The readied action happens now, as a reaction |
| `reactions <id> ask\|auto\|off` | Spell reactions: ask the player, always, never |
| `condition <id> add\|remove <condition>` | GM ruling for what the engine left to you (`remove concentration` ends it) |
| `adjust <id> hp=N temp_hp=N ac=N` | GM correction |
| `log [n]` | The last n combat log lines |
| `reachable <id>` | Squares reachable walking and with Dash (used by the display) |
| `targets <id>` | Every attack and target with hit chance (used by the display) |
| `end` | Ends combat: character sheets, tracker, session log, `state.md` |

Flags for any command: `--roll N` (a player's natural roll; repeat for several), `--for-me` (the engine rolls the player's dice this time), `--react yes|no` (answer a reaction prompt; repeat when several are asked, in order), `--seed N` (repeatable engine dice, for demos).

Exit codes: `0` done; `1` not allowed (the message says why); `2` waiting for a roll or a decision, with nothing changed. Re-run the same command with the flag the message asks for; the engine replays the same enemy dice (`combat/pending.json`).

## Try it

```bash
python3 systems/dnd5e/build_srd.py --no-fvtt     # once: SRD monsters
python3 scripts/tactics/demo.py                  # Kairos vs two giant frogs, scripted
python3 scripts/tactics/demo.py --scenario mephit   # vs an ice mephit: Frost Breath, Fire Bolt, reactions
```

The demo plays a whole fight in a throwaway campaign folder and prints every command and its output.

## Where things live

- Engine: `scripts/tactics/` (grid and areas, state, roller, rules interface, engine, effects, spells, actions, ai, maps, cli)
- 5e rules, spells and the sheet reader: `systems/dnd5e/tactics_rules.py`, `tactics_spells.py`, `tactics_sheet.py`
- Maps: `display/maps/*.json` (see `display/maps/README.md` to add your own)
- The fight in progress: `<campaign>/combat/encounter.json` (a crash or restart loses nothing)
- Display: `display/static/tactics.js` and `tactics.css`; endpoints `/combat`, `/combat/state`, `/combat/do` in `display/gm-display-app.py`. `GM_DISPLAY_PORT` runs a display on another port (the engine follows it).
- Milestone plans, findings and decisions: `docs/milestones/`
