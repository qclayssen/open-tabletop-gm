# CLAUDE.md

Fork of open-tabletop-gm (LLM-agnostic tabletop GM framework) adding **turn-based
tactical grid combat** for D&D 5e (2014 rules), inspired by Baldur's Gate 3's
turn-based mode. Branch: `tactical-combat`.

## Hard rules

- **Never open or read anything in a folder named `DM_SEALED`, or any campaign's
  `answer-key.md`.** The user is the player and must stay unspoiled. Demos and
  tests use only SRD monsters and the player-facing Kairos sheet
  (`tests/fixtures/Kairos_Level1.md`).
- **The engine owns the rules. The LLM only narrates and chooses.** Positions,
  HP, initiative, action economy, range, reach, movement cost and dice are
  decided by `scripts/tactics/`. The GM never computes distance, cover or damage.
- GM-facing commands: one call, plain arguments, 1 to 4 lines of plain text
  (optional `--json` for the display). Local 24B models lose the thread after
  4 or 5 chained tool calls; enemy turns are a numbered menu the GM picks from.
- Python stdlib plus Flask only (Flask is already a display dependency). Ask
  before adding any package. Code must run on Python 3.10 (CI floor) and on
  Windows with a non-UTF-8 locale (always pass `encoding="utf-8"`).
- 5e-specific rules live in `systems/dnd5e/`, behind `scripts/tactics/rules.py`.
- No em dashes in docs, comments or UI text written for this fork.
- Small focused commits. Keep every existing test passing.
- Never write to the user's source folder (`~/Claude/Projects/dnd/...`); only
  the installed campaign copy is written to.

## Architecture

```
SKILL.md, SKILL-*.md        GM instructions (always-loaded text: keep tiny)
scripts/                     dice.py, combat.py (initiative), tracker.py (conditions,
                             concentration, death saves -> <campaign>/tracker.json), paths.py
scripts/tactics/             grid combat engine (stdlib only)
  grid.py                    5 ft squares, terrain legend, diagonals "5" | "5-10-5",
                             Dijkstra movement, DMG corner line of sight and cover
  state.py                   Encounter/Token/TurnState -> <campaign>/combat/encounter.json
                             (validated, atomic write, .bak kept)
  roller.py                  dice via scripts/dice.py parser; rolls tagged
                             engine | player | verbal; PendingRoll for player rolls
  rules.py                   thin system interface + loader (see SYSTEM-PORTING.md)
  engine.py                  initiative, turns, move + opportunity attacks, undo,
                             attack, Dash/Disengage/Dodge, previews (hit %, OA warnings)
  ai.py                      numbered enemy options (deterministic) + choose
  maps.py                    display/maps/*.json (rectangles) -> engine grid
  sync.py                    tracker.json, display /stats + /combat, state.md,
                             sheets (.bak) and session-log.md on end
  cli.py                     the GM commands; run via scripts/tactics/combat.py
  demo.py                    scripted Kairos vs 2 giant frogs, in a temp campaign
scripts/tactics.md           the GM loop (loaded only at /gm combat grid)
systems/dnd5e/
  tactics_rules.py           5e rules: advantage, crits, cover, resistances, 0 HP,
                             death saves, SRD monster -> Token adapter
  tactics_sheet.py           character sheet -> Token, and write-back after combat
  build_srd.py               SRD build; monsters carry structured `actions`
  lookup.py                  SRD lookup (data/ is generated and gitignored)
display/                     Flask companion: gm-display-app.py (SSE /stream, JSON
                             messages keyed by type), send.py, push_stats.py,
                             check_input.py, templates/index.html (single file)
display/static/reference/    strixhaven_map_table.html: visual reference for the grid
```

Campaign data root: `~/open-tabletop-gm/campaigns/<name>/` (override with
`GM_CAMPAIGN_ROOT`). Combat state: `<campaign>/combat/encounter.json`.

Engine conventions:
- Squares are `(x, y)` from the top left; labels are column letter + 1-based row
  (`(3, 4)` is `D5`).
- Player dice under `roll_mode: players`: the engine raises `PendingRoll`; the
  caller supplies natural dice values (no modifiers) and retries. `Roller.for_me`
  is the "Roll for me" button. Player reactions raise `DecisionNeeded`.
- Engine functions mutate the encounter in place; callers work on a fresh load
  and save only on success, so a paused action is never half-applied.
- Monster actions with `flags` (rider, targeting, unparsed, ...) are never
  guessed at: riders are reported for the GM, not applied.

## Commands

```bash
python3 -m pytest tests/ -q                       # full suite
python3 -m pytest tests/test_tactics_*.py -q      # engine only
python3 systems/dnd5e/build_srd.py --no-fvtt      # build SRD data (network)
python3 systems/dnd5e/lookup.py monster "giant frog" --json
python3 scripts/tactics/demo.py --seed 4          # scripted fight, prints every command
python3 scripts/tactics/combat.py --help          # GM command reference
bash display/start-display.sh                     # display on http://localhost:5001
bash display/start-display.sh --lan               # LAN mode (phones, tablets)
GM_DISPLAY_PORT=5055 python3 display/gm-display-app.py   # a second display (tests)
```

## Milestones

- [x] **Setup**: branch `tactical-combat`; reference map page and Kairos fixture;
      SRD built; `check_input.py` LAN fix (separate commit, upstreamable);
      structured monster actions in `build_srd.py` with a golden lookup test.
- [x] **1. Engine core + tests**: state, grid, movement, reach, basic attacks,
      initiative, HP, death saves, opportunity attacks, undo, previews.
- [x] **Review pass**: 10 findings fixed (sight at map edges, 5-10-5 parity,
      check_input double delivery, flat damage, OA preview distance, Dodge).
- [x] **2. CLI + GM loop**: `combat.py` (start, status, options, choose, move,
      preview, attack, dash/disengage/dodge/stand, death-save, undo-move,
      end-turn, condition, adjust, log, reachable, end), `ai.py`, sheet reader
      and write-back, tracker and sidebar sync, 5 maps + README,
      `scripts/tactics.md`, `/gm combat grid`, `docs/TACTICAL-COMBAT.md`, demo.
      `cast` (save spells, Magic Missile) moves to milestone 4 with templates.
- [x] **3. Grid display**: `/combat`, `/combat/state`, `/combat/do`; `combat`
      SSE event; `display/static/tactics.{js,css}` (SVG grid, reach and dash
      shading, path preview with OA warning, action bar, targets with hit %,
      roll prompt with "Roll for me", animation, damage floaters, turn banner,
      initiative strip, log, phone layout). Verified in the built-in browser
      (desktop and 375 px). Open: the grid's own roll prompt is used instead of
      the phone dice drawer; Help/Hide/Ready are greyed out until milestone 4.
- [ ] **4. Spells and templates**: cones, spheres, lines, cubes; saves;
      concentration; Kairos's cantrips and level 1 spells first.
- [ ] **5. Polish**: cover and sight shading, fog of war, condition badges,
      keyboard controls, accessibility.

## Decisions on record

- Workflow: one session per milestone ("Read CLAUDE.md and
  docs/TACTICAL-COMBAT.md, then continue with milestone N"). Branch from
  `main`, PR to the fork (`gh pr create --repo qclayssen/open-tabletop-gm`),
  merge when CI (Actions, enabled) is green. `origin` = fork (HTTPS; SSH keys
  do not work here), `upstream` = Bobby-Gray. Push with
  `git -c credential.helper='!gh auth git-credential' push`.
- Roll mode follows the campaign's `roll_mode`; every roll logs its source.
- On `combat.py end`: write HP, temp HP, spent slots, hit dice and death saves to
  the installed `characters/Kairos.md` (backup `Kairos.md.bak` first, print a
  short diff); keep lasting conditions, drop combat-only ones; append a 3 to 5
  line summary to `session-log.md`.
- Upstream data quirk: 5e-bits `success_type` sometimes contradicts its own text;
  such actions are flagged `success_conflict`, never resolved automatically.
- Kairos's sheet is the source of truth for his kit (Fire Bolt, Mind Sliver,
  Dagger; Magic Missile, Shield, Mage Armor, Silvery Barbs), not the original prompt.
- Token size is 1 square for now; larger creatures are a later change.
- Another display (claude-dnd-skill) may be running on port 5001 on this
  machine. Test with GM_DISPLAY_PORT and TACTICS_NO_DISPLAY=1; never stop it.
- Import the CLI as `tactics.cli`, never as a module named `combat`:
  `scripts/combat.py` (the initiative tracker) shadows it.
- `display/static/reference/strixhaven_map_table.html` is a player-facing copy:
  DM-only maps and undiscovered places were removed before publishing. Never
  add content from the user's own copy or any DM folder.
- Monster riders (the giant frog's grapple) are reported as "GM decides the
  rider", applied with `combat.py condition`. The CLI never applies them.
- 2 giant frogs vs level 1 Kairos alone is a deadly encounter by 5e math; the
  demo usually ends with Kairos down. That is the rules, not a bug.
