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
                             Dijkstra movement, DMG corner line of sight and cover,
                             areas of effect (centre-of-square rule, walls block)
  state.py                   Encounter/Token/TurnState -> <campaign>/combat/encounter.json
                             (validated, atomic write, .bak kept)
  roller.py                  dice via scripts/dice.py parser; rolls tagged
                             engine | player | verbal; PendingRoll for player rolls
  rules.py                   thin system interface + loader (see SYSTEM-PORTING.md)
  core.py                    CombatError, DecisionNeeded, rules_for, hostile, decide()
  engine.py                  initiative, turns, move + opportunity attacks, undo,
                             attack (+ reactions, riders), Dash/Disengage/Dodge, previews
  effects.py                 Token.effects lifecycle, concentration, grapples, Shield,
                             Silvery Barbs
  spells.py                  cast, preview (area, fail %), castable, monster area actions
  actions.py                 Help, Hide, Escape, Ready, trigger
  ai.py                      numbered enemy options (deterministic) + choose
  maps.py                    display/maps/*.json (rectangles) -> engine grid
  sync.py                    tracker.json, display /stats + /combat, state.md,
                             sheets (.bak) and session-log.md on end
  cli.py                     the GM commands; run via scripts/tactics/combat.py
  demo.py                    scripted Kairos vs 2 giant frogs, in a temp campaign
  play.py                    playable terminal game + tutorial (docs/TUTORIAL.md)
scripts/tactics.md           the GM loop (loaded only at /gm combat grid)
systems/dnd5e/
  tactics_rules.py           5e rules: advantage, crits, cover, resistances, 0 HP,
                             death saves, saves and save chance, SRD monster -> Token
  tactics_spells.py          spell name -> spec for a caster (SRD mechanics + BUILTIN)
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
python3 scripts/tactics/play.py tutorial          # play a fight yourself (lessons)
python3 scripts/tactics/combat.py --help          # GM command reference
bash display/start-display.sh                     # display on http://localhost:5001
bash display/start-display.sh --lan               # LAN mode (phones, tablets)
bash display/start-display.sh --campaign NAME     # display for a campaign + preflight checks
GM_DISPLAY_PORT=5055 python3 display/gm-display-app.py   # a second display (tests)
```

## Milestones

Details, findings, decisions and open items: `docs/milestones/` (one file per
milestone; read the one you are working on).

- [x] 1. Engine core, [x] 2. CLI and GM loop, [x] 3. Grid display
- [x] 4. Spells and templates
- [ ] 5. Polish (next: `docs/milestones/05-polish.md`)

At the end of a milestone: update its file (Shipped, Findings, Decisions,
Open), tick it here, and keep this file under 150 lines.

## Decisions on record (cross-cutting)

- Workflow: one session per milestone ("Read CLAUDE.md and
  docs/milestones/0N-*.md, then continue with milestone N"). Branch from
  `main`, PR to the fork (`gh pr create --repo qclayssen/open-tabletop-gm`),
  merge when CI (Actions) is green. `origin` = fork (HTTPS; SSH keys do not
  work here), `upstream` = Bobby-Gray. Push with
  `git -c credential.helper='!gh auth git-credential' push`.
- Roll mode follows the campaign's `roll_mode`; every roll logs its source;
  "Roll for me" rolls only what the player has not supplied.
- Nothing is guessed: unparseable SRD data keeps its raw text and a flag.
  Riders that parse exactly (grapple, save or prone/condition, save for damage)
  are applied; the rest prints as "GM decides: ...". Spells the engine cannot
  run are still cast and narrated.
- A paused command (exit 2) keeps its seed in `combat/pending.json`; the re-run
  with `--roll` / `--react` replays the same engine dice.
- The dnd-gm advisor council (Game Designer, Tactical Combat Designer, Grid
  Strategist, ...) can be consulted as subagents for rulings; their advice is
  recorded under Decisions in the milestone file.
- Import the CLI as `tactics.cli`, never as a module named `combat`:
  `scripts/combat.py` (the initiative tracker) shadows it.
- Another display (claude-dnd-skill) may hold port 5001 on this machine. Test
  with GM_DISPLAY_PORT and TACTICS_NO_DISPLAY=1; never stop it; remove test
  runtime files from `display/` afterwards.
- `display/static/reference/strixhaven_map_table.html` is a player-facing copy
  (DM-only maps and undiscovered places removed). Never add content from the
  user's own copy or any DM folder.
