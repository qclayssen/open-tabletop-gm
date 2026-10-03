# Changelog

All notable changes to open-tabletop-gm are documented here. The skill follows [semantic versioning](https://semver.org/) — `MAJOR.MINOR.PATCH` where MAJOR breaks an existing campaign or workflow, MINOR adds significant new capability, and PATCH fixes bugs without changing behavior.

The current installed version is recorded in the `VERSION` file at the repo root. Run `python3 scripts/update_skill.py --check` (or `/gm update --check`) to compare your local copy against `origin/main`.

Versions before **0.7.0** are reconstructed retroactively from git history; the dates reflect the commit each version is anchored on. Going forward, every release lands in the same commit as a `VERSION` bump and a CHANGELOG entry.

This project is the LLM-agnostic, system-flexible fork of [claude-dnd-skill](https://github.com/Bobby-Gray/claude-dnd-skill). It tracks behind on features that need adaptation for non-Claude tooling and for system-agnostic design; the goal is parity on what makes sense to port and an independent track on what doesn't.

---

## [Unreleased]

### Added: a GM-only ledger of agency violations and how each was corrected

`scripts/localdm/agency.py` writes one JSON line per guardrail trip to
`<campaign>/localdm/agency.jsonl`, and `/agency [n]` reads the tail back.

Nine forms, one per guardrail in the DM loop: `agency`,
`injection`, `name-reuse`, `unbacked-number`, `check-outcome`,
`unbacked-cast`, `fail-forward`, `mid-fight-check`, `mid-fight-cast`.
Each line records the turn and scene, the form, how many drafts
tripped it, the outcome, the quoted evidence and a local timestamp.

The outcome is the point, and it is three-way rather than two:

- **caught** - the guardrail fired and the text the player was shown
  does not trip it. The correction worked.
- **narrated** - the guardrail fired and the shown text still trips it.
  The retry was dirty, so `Session._dm` kept the first draft and the
  player saw the violation. A log that recorded only "a trip" would
  report the guardrail working every time, which is the opposite of what
  a flag means.
- **refused** - the engine refused outright, with no correction step.
  The two mid-fight forms are always this: nothing was rolled or spent.

A fourth, `unknown`, records a draft that tripped a guard and whose
region never settled, because a silently dropped trip is
indistinguishable from no trip at all.

Retries dedupe on (turn, scene, form): the same guard firing on the
retry is the same violation being corrected twice, so it is one line
with a count rather than a rate inflated by however many retries the
model needed.

No extra model call: every trip is a regex the loop already ran, and
the caught/narrated decision is a second pass of the same regex over
the final text. GM-only and never fed to the DM, for the reason
`notes.md` documents for advisor notes: a DM briefed on its own
guardrail report learns to write to the detector.

The shapes these guards cannot see are a separate question, measured
with denominators in `docs/DM-BOUNDARY-BASELINE.md`. This ledger is
not evidence about those.
### Fixed: a spell slot could be spent and the receipt thrown away, and a question could spend one

Two findings from `docs/test-reports/TEST-REPORT-pt2-spells-advisors-2026-09-30.md`,
re-pinned against the current build in `tests/test_spell_command_boundaries.py`.

**A committed cast survives a narration failure.** `Session._cast_spell` mutates
persistent state *before* it narrates: the slot is incremented, the sheet is written
and `tracker.cmd_effect` starts the 8-hour effect. The narration call had no handler,
so the REPL's `except llm.LLMError` replaced the whole turn with one error line and the
player never learned a slot was spent. `_narrate` already handled exactly this case
("report the engine's own words and let the error stand"); `_cast_spell` now does the
same, and says on stderr that the narration was lost.

**A question no longer spends a slot.** `cast` is a field the *model* sets, and nothing
checked that the player's line was a cast, so a DM that answered a status question with
a cast spent a level on it. The report's own input no longer reproduces --
`fightq.classify(scope="explore")` claims an AC question and answers it from the sheet
with no model call at all -- but the router has no topic for spell slots, so
"how many first-level slots do I have left" and "can I cast magic missile" still reach
the model. A `cast` on a question-shaped line is now refused in one visible engine
line. The direction is deliberately conservative: a cast phrased as a question loses
the cast, and the player can always ask again in the imperative.

Still true from the same report, and pinned rather than fixed:
`spells._effect`'s "already covered" branch is unreachable out of combat (every cast
re-reads the sheet, and `write_back` never persists the AC field), and `state.md` is
never refreshed after a cast, so a GM reading it sees the pre-Mage-Armor AC.
### Fixed: a consequential default on a character sheet is now labelled, and a malformed one is refused

`systems/dnd5e/tactics_sheet.py` gained a `DEFAULTS` table naming every numeric
fallback, whether it **changes an outcome**, and what it is used for; `read_sheet`
records where each number came from on `extra["derived"]`; and `explain(token)`
renders the consequential ones as lines a GM can act on.

| Field | Fallback | Consequential | Refused elsewhere |
|---|---|---|---|
| `ac` | 10 | yes: every attack roll against the creature | |
| `speed` | 30 ft | yes: the whole movement budget | |
| `level` | 1 | yes: proficiency, slots, hit dice | |
| `dex_mod` | +0 | yes: Dex saves and Mage Armor | |
| `saves` | `{}` | yes: every save at +0 | |
| `attack_bonus` | 0 | yes: a real attack roll | |
| `spell_dc` / `spell_attack` | `None` | no | `tactics_spells.resolve` already raises with the caster's name |
| `passive_perception` | `None` | no | `checks` only reads a stated one |
| `temp_hp`, `hit_dice` | 0 / `None` | no | a long rest asks |

**A present-but-unreadable field is now refused.** `_int("TBD", 10)` produced an
authoritative-looking AC 10 on a half-filled sheet, indistinguishable from a finished
one. `_required_int` raises with the sheet name, the field, the text that could not be
read, and what the number would have decided. An *absent* field is not malformed and
still falls back, which is why this is a change to the unreadable case only.

**`_field` no longer reads past the end of a line.** Its `\s*` ate a newline, so a
`**Speed:**` at the end of a line with nothing after it read the *next* line and
returned `1d6 (remaining: 1)`, which `_int` turned into a speed of 1. The same failure
the table above is about, reached from the other direction.

**RI10, as labels.** `tactics_sheet.ac_parts(text)` returns `ac_base`, `ac_dex_bonus`
and `ac_max_bonus` beside the integer `Token.ac`, each with provenance, and the result
travels on the token. `Token.ac` is unchanged and no attack roll is computed
differently: the external runtime RI10 was written against is **not** adopted here. On
the repository's own fixture sheet it reports what is worth reporting - the sheet says
`12 (13 with Mage Armor)` where PHB p.144 gives 13 + DEX = 15, so the parenthetical
contributes `-2` over the rule. The sheet is campaign data and is not corrected.

`roller.average` is untouched and pinned, since it is the other number a default
reaches: the expected damage `spells.preview` prints for every target in an area.

### Fixed: the static system prompt was charged against the dynamic budget, evicting every recent turn

`context.build_messages` counted `len(sys_msg)` inside the same character
budget as the conversation (`fixed = len(sys_msg) + ...`). `prompts/dm.md` is
9030 chars and the default `--budget` is 12000, so roughly 2070 chars were left
for the campaign digest *and* the recent turns together, while the digest's own
per-file caps allow 8500 (`state_digest` 3000 + `sheet_digest` 3000 +
`notes_digest` 2500).

Measured with `dnd-gm`'s `scripts/measure_turn_tokens.py` against the real
builder:

| digest chars | turns kept (before) | turns kept (now) | total tokens (now) |
|---|---|---|---|
| 0 | 8 | 8 | 2420 |
| 2184 | 5 | 8 | 2697 |
| 4797 | **0** | 8 | 3025 |
| 8455 | **0** | 8 | 3482 |
| 10192 | 0 | 8 | 3698 |

Past about 4800 chars of digest the `## Recent turns` section vanished, so any
campaign with a filled-in `state.md`, sheet or `npcs.md` got a full load of lore
and zero conversation history. The static prompt does not compete for context
with the conversation, it competes for cache, so it is no longer charged. The
budget still bounds the dynamic message, the digest is still never trimmed, and
the unit is still characters: converting it to tokens would move every existing
session's effective context and is deliberately its own change.

`/usage` now prints one line naming the split, so this cannot be invisible
again:

    prompt budget: 9030 static chars, cacheable and not charged  |  2951 dynamic
    chars of 12000  |  recent turns 8/8 kept

### Fixed: three assertions that could not fail, and three test files that could not be run

An audit (`qclayssen/dnd-gm` #234) named four suspected false greens. Each was
checked before being repaired, and the recorded failure modes did not all hold.

- **`tests/test_map_to_atlas.py` compared a path against a file name.** The
  sidecar's `mapPath` is a vault-relative path
  (`atlas-vtt/collections/Strixhaven/Test Cave.atlasmap`); the set was built from
  `{p.name for p in ...glob("*.atlasmap")}`, which holds bare names. The two could
  never be equal on any machine, and the assertion ended in `or True`. The audit
  recorded the `or True` as "a marker that a machine difference was papered over";
  it was not. Dropping `or True` alone turns the test red with `assert
  'atlas-vtt/collections/Strixhaven/Test Cave.atlasmap' in {'Test Cave.atlasmap'}`.
  Now compares names, and fails loudly if nothing was exported at all, since an
  empty set would satisfy the `in` trivially.
- **`tests/test_schemas.py::test_schema_field_can_be_told_to_keep_unknown_keys`
  asserted nothing.** It proved the field stops raising and never checked that the
  unknown key survives. Preservation turns out to be unconditional -- `coerce`
  starts from `dict(value)` and `validate` is what consults `allow_unknown` -- so
  a `coerce` rewritten to filter down to the declared fields would pass the old
  body untouched. Now asserts the returned dict, and that the same key is still
  refused without the flag.
- **`tests/test_gm_watch_sh.py`'s 8s bound was undocumented, and the audit had the
  direction backwards.** It recorded "asserts wall-clock deadlines with no
  margin"; measured, the trap latency is 1.0993-1.1272s over eight runs (spread
  0.028s), so 8s is roughly 7x headroom rather than a tight race. The 1.1s is the
  watcher's `--interval 1` poll. The number is now documented with the measurement
  so nobody re-derives it to find out whether it is safe to move.
- **The real machine dependence in that file was a different assertion.**
  `test_simultaneous_starts_yield_exactly_one_watcher` used a bare
  `time.sleep(4)` and `assertEqual(len(running), 1)`, which goes red on a loaded
  runner -- six `/bin/sh` processes starting at once under a parallel pytest --
  because a loser that has not finished failing startup is still alive at 4s.
  Every other wait in the file already used the file's own `wait_for`; this one
  reached for a literal. Now it does too.
- **`tests/conftest.py` (new): three test files could not be collected alone.**
  `test_schemas.py`, `test_dice_rng.py` and `test_localdm_stall.py` import
  `tactics`, `dice` and `localdm` from `scripts/` and never put it on
  `sys.path`. They passed in a full-suite run only because another module adds it
  while the collection is being built. Measured: each of the 137 files in
  `tests/` run alone with CI's exact invocation, 3 error at import. This is not
  cosmetic: a mutation proof against those files reports a *collection error*
  where it should report a failed assertion, and both are non-zero.

Not changed, because the finding did not hold:

- **The two portrait tests at `tests/test_token_portraits.py` are already
  superseded.** Both guard on `_manifest_art_on_disk()` and `pytest.skip`, and the
  docstring records that the guards were rewritten so a *partial* install is
  checked rather than skipped -- which is the audit's stated concern, answered on
  purpose. The hermetic twin of the machine-dependent half
  (`test_a_full_install_is_a_clean_check`) already exists.
- **"Explicit optional skips" already holds.** Measured: 2893 passed, 100 skipped,
  and every skip names either the environment variable that enables it
  (`OTGM_NETWORK_TESTS=1`, `DISPLAY_RENDER_TESTS=1`) or the command that would
  produce what is missing (`npm install`, an SRD build, the portrait install).

### Fixed: four entry points asked whether a campaign *exists* instead of whether it *is* one
`paths.find_campaign` documents its own contract at `scripts/paths.py:121-124`: on a miss it returns `campaign_dir(name)`, "a path that does not exist unless a shell is sitting there, so callers must not read its existence as a hit. Ask `_is_campaign`." Four callers did not ask.

- `scripts/tactics/cli.py:136` (`_camp_dir`) — `d.exists()`
- `scripts/localdm/play.py:1408` (`main`) — `camp_dir.exists()`
- `scripts/npc_rename.py:340` (`main`) — `camp_dir.exists()`
- `scripts/map_to_atlas.py:753` (`main`) — `camp_dir.is_dir()`

A campaign that has moved leaves an empty shell at the old path, and because every entry point resolves a *name*, the shell wins over the real campaign: it sits at the configured root, so nothing reaches the legacy fallback. The guard passed and each entry point read an empty campaign — or, for `npc_rename`, **renamed into one**. Reproduced on the fixed code by reverting one guard: `npc_rename` reported `no occurrences of 'Ash' found in strixhaven-kairos` from inside the shell, where it should have reported a miss.

This is bug B1 (`docs/guides/seat-harness-known-bugs.md`) returning through a different door. `_is_campaign` shipped in open-tabletop-gm#99 and `display/preflight.py` adopted it there and then; the four CLIs were missed, because nothing asserted the call sites — `tests/test_paths_campaign_resolution.py` pins the resolver, not the callers.

- `tests/test_campaign_resolution_callers.py` (new, 11 tests): pins all four guards, so the name-only check cannot come back in a place the existing file does not cover. The write case is behavioural — `npc_rename` against a shell, asserting it refuses *and* that the shell is left with exactly the two entries it started with.
- `tests/test_map_to_atlas_formations.py`: the `camp` fixture created `campaigns/demo` with no `state.md` — a two-entry shell, so it only passed *because* of this bug. Now writes a `state.md`, per the convention already documented at `test_campaign_lint.py:414` and `test_display_preflight.py:89`.

### Fixed: the checkout was 39 commits behind `origin/main` (B13)
`open-tabletop-gm` sat on `feat/statblock-export-and-portrait-matching` while the outer repo's gitlink pointed at that branch tip rather than main, so the working tree read as missing `start.py`, `scripts/pin*.py`, `.github/workflows/tests.yml` and 22 test files. All exist on main. See `BUGS.md` B13 — the recurrence note matters more than the fix, because two gitlinks and no `.gitmodules` means nothing prevents it again.

### Added: named landmarks and the state card (A)
> (kept below, above the `day` entry, in the order it landed on main)

- **The gap.** A map's features were scenery. `compile_map` painted them into the terrain and, at most, wrote a floating label, so nothing could say *which* one. "Move behind the altar" or "put him by the north door" was unanswerable: the model had no handle to ask about and no way to check its own answer. It also had no source for distance or cover mid-fight, so feet got invented and disagreed with the engine that rolls the attack.
- **Every map feature is now an addressable landmark.** `maps.compile_map` returns `meta["landmarks"]`: the feature's own `name` slugged (`north-door`) when the map gives one, otherwise its type plus a per-type counter (`crate-1`, `crate-2`), so every map written before names existed still gets stable handles and no shipped map needed editing. A duplicate explicit name gets `-2`. Squares are tracked per feature through later rectangles — a feature painted over entirely is dropped but **still advances the counter**, so the other handles do not shift under anything that already cited them. Display and GM text only; the engine's rules read `rows` and never this.
- **`$T card <token>`: the one command for where things are.** Every creature with square, HP and feet from the actor; the map's landmarks by name with their squares, feet and the cover the actor has against them. **Every number comes from the engine** (`Grid.distance` for feet, `sight.sight` for cover and line of sight), so the card and a real attack always agree. North is fixed and stated on the card (row 1 top, columns A, B, C west to east) because "north" is otherwise the model's guess. `--players` applies the display's fog filter, leaving hidden and unseen creatures out of both the card and the text, exactly as `sight(players=True)` does.
- **The fight now reads it, so the DM prompt can trust it.** `_engine_context` appends the current actor's card to the engine section of the Local DM's context; `dm.md` tells the model the card is the truth for squares and feet, to move toward the landmark the player named using the squares given, and never to work out a distance, square or cover level itself. `card` is in `READ_ONLY`, so like `status` it is a read-only probe, not a command the DM asked for — the two Local DM tests that enumerate context probes were updated to include it, and their guard is unchanged: an unreadable line still runs no action.
- 8 new tests in `tests/test_tactics_statecard.py`, plus an assertion over **every shipped map** that landmarks compile with unique names, so the counter scheme cannot rot as maps are added. `scripts/tactics.md` and `dm.md` document the card and the handles.

### Added: `combat.py day` — what a whole adventuring day costs, not one fight
- **The gap.** `budget` answers what one encounter costs and `rate` answers what a
  fight just cost. Neither answered the question a GM asks when planning a session
  rather than rating a fight: *is this a day, or is this three days?* The 2014 DMG
  tabulates a whole day separately from an encounter, and nothing read it.
- `ADVENTURING_DAY_XP` (20 rows) and `ENCOUNTERS_PER_DAY` in `systems/dnd5e/xp.py`,
  not in `encounter.py`. The module states for itself that a second copy of an XP
  table is a second thing to get wrong, and the day table is an XP table;
  `encounter.py` imports it the way it already imports `XP_THRESHOLDS`. Source: the
  2014 Basic Rules "The Adventuring Day" (DMG p. 84), whose own column heading is
  "Adjusted XP per Adventuring Day per Character".
- **`--plan` is the feature; the budget is only the frame.** The day budget alone is
  close to useless, and the reason is the non-obvious part: the DMG puts a day at
  about six to eight *medium or hard* encounters and the table is calibrated to that
  blend, so dividing it by the Medium column gives 5 to 8 and by the Hard column 3 to
  5, at **every level from 1 to 20**. "A day holds about seven Medium fights" is true
  at level 1 and at level 20 and tells a GM planning a level 20 day nothing they had
  not already assumed. The budget can never come out over or under on its own. So
  `day --plan "goblin x4 | orc x2"` costs a day the GM has already designed, fight by
  fight, and reports the share with a banded verdict: room for more / a full day /
  two days / far over.
- **The bands do not name a condition level.** An earlier draft ended the far-over
  band with "exhaustion 5 (speed 0) is likely by the end of it". The exhaustion number
  is correct 2014 5e and was still wrong here: `day` is read-only, loads no encounter
  and sees no tokens, and the day XP budget measures expected XP earned rather than
  resource depletion. Exhaustion lives in the tracker, where only a long rest reduces
  it. The sentence asserted a specific mechanical game state the tool did not compute,
  and a GM would have put it on a character sheet. The editorial half survives: "the
  party will be spent" is a GM's judgement, offered as advice.
- **Each planned fight is rated through `rate()`**, so a day cannot be costed by a
  second implementation that drifts from the one that costs a single fight, and the
  2014 monster-count multiplier is applied per fight rather than to the day total
  (two fights of two is x1.5 twice, not x2 once).
- **The fight count reported is the count the GM typed.** The first version converted
  the XP back into a "fights' worth" figure and reported four planned fights as
  "roughly 1 fights' worth". A GM who planned four fights learns nothing from that,
  and it is the exact confident-wrong-number failure this command was written against.
- **`|` separates fights, `,` separates monsters within one.** Reusing the comma for
  both would make `goblin x4, orc x2` ambiguously one fight or two.
- **2024 is refused, not derived.** 2024 removed the adventuring day outright, so
  there is no table to read and nothing to port. An earlier draft justified the
  refusal as "2024's three tiers are a whole day's share, not an encounter cost",
  which is a true observation standing in for a false reason; the refusal now says
  the thing that is actually true and points at `rate`.
- **A mixed party gets each character's own day, not a mean.** `party_total //
  party_size` is the arithmetic mean of the party's days: for a L1 and a L20 that is
  20,150, a figure belonging to no character on the table, printed beneath a
  threshold row built from the *average level's* data. Two different averages
  presented as one table. Each character is now reported by name, and the mean is
  not reported at all: it is arithmetic, not a budget.
- `day` is in `READ_ONLY`, and a test asserts it writes nothing: no encounter, no
  `pending.json`, no session-log line. This was a real defect on the first run, not a
  hypothetical — `day` reached the save path with `enc` still `None` and died on
  `enc.board()`. A planning tool that mutates fight state can be called at the wrong
  moment and cost a real roll. Verified mid-fight as well: with a live encounter and a
  three-receipt chain, every campaign file is byte-identical afterwards and the chain
  still verifies.
- Documented where the GM will find it: `scripts/tactics.md` (the text loaded at
  `/gm combat grid`), the `rules.py` design-contract block, and the `SYSTEM-PORTING.md`
  Design table, so a second system implementing the interface has a row to read.
- 26 tests in `tests/test_adventuring_day.py`. The guard is proven red rather than
  assumed: with the six source files stashed and the tests kept, **all 26 fail**.

### Added: random-event oracle and the World Queue (E0, E1)
- `scripts/oracle.py`: chaos factor (`## Session Flags`), yes/no, and Random Event Focus, ported from the mature tree without `scene_meaning()` (unanchored word pairs dilute an authored world). Rolls go through `scripts/dice.py`; `--seed` replays them. `/gm oracle` documented.
- `scripts/world_queue.py` and `## World Queue` in `templates/state.md`: off-screen events stored as decisions (`ask`, `if_ignored`, `expires_by`) with an optional `demands:` pressure claim. `roll` seeds at most one entry while fewer than 3 are pending and never sets `demands`; nothing auto-fires; fired entries are kept; three dismissals of one id surface at start; `validate` reports expired entries; there is no date field. The section is optional and is in `DIGEST_SECTIONS` (fired entries in full, pending ones marked do-not-reveal).
- `campaign_lint.py` no longer treats the first yaml fence in `state.md` as the Campaign Arc; it reads the fence under `## Campaign Arc`.

### Fixed: an exported Atlas scene is the shape Atlas reads, not a shape that happens to load
- **The gap.** `map_to_atlas.py` wrote a scene wrong in three ways, and every one of them failed silently — the export printed its success line and reported the files it had written. `name` instead of `title`, so `MapPersistence.ts` had no display name and the scene opened **untitled**. No `mapPath` at all, and Atlas stores the background twice — the asset index resolves the scene against `mapPath` while the renderer paints `background` — so the grid drew over **no artwork**. And `walls`/`lights` as `{}`, where `MapPersistence.ts` reads them straight into the store and `WallRenderer` iterates them.
- **The dict is the one that survived longest, because an empty `{}` iterates as zero walls.** It looks exactly right until someone draws a wall by hand, at which point the renderer is handed an object where it expects a list.
- **It stayed hidden because the exporter was never used on its own.** The Strixhaven vault's `export-maps.sh` ran a normalization pass over every scene afterwards, rewriting them into the correct shape — so the bug only ever surfaced for anyone calling `map_to_atlas.py` directly, which is what that pass was working around. The fix is at the source; the normalization is now belt-and-braces rather than the thing holding it up.
- **The defaults Atlas itself saves are written too**: `camera: null`, empty `widgetValues`/`widgetSettings`, `dmNotePath`, `tokenSettings`, `initiative`, `diceLog`, `pinnedNotePreviews`, `lootRoller`. A scene missing these does not raise — the store falls back — so the result is a scene that opens with a saved camera, widgets on and fog the GM did not ask for.
- **`name` is kept alongside `title`, deliberately.** The sidecar in `scenes/<id>.json` is what the asset index reads the name from, and the two files are read by different code paths, so dropping `name` would fix the title and break adoption.
- **No behaviour change for an existing vault.** Scenes already normalized on disk re-export byte-identical — verified against Bow's End Tavern and the six generated scenes.
- 2 new tests in `tests/test_map_to_atlas.py`: the full key set against a known-good scene, and the container types. The array test exists precisely because the dict shape looked correct for as long as it did.

### Fixed: the off-screen world is written where the DM reads it, not left to memory
- **The gap.** `world.py` ticked the faction clocks, printed the result GM-only, and told the GM to "record it under `## Faction Moves`" in `state.md`. `scripts/localdm/context.py`'s `DIGEST_SECTIONS` reads that section. So the loop closed only if the GM remembered: a GM who forgot left `state.md` without the section, the digest without the content, and the DM unable to put the consequence in front of the player. The off-screen world that faction clocks exist to create was computed and then dropped.
- **A tick now writes the move itself.** `append_faction_moves()` appends one line per faction that actually moved, plus a line when a clock fires, under `## Faction Moves` in `state.md`. Party interference via `clock` writes there too, since the DM reads the same section either way. Rolls that changed nothing write nothing: they are GM bookkeeping, `faction_log.md` already keeps them, and a daily "nothing happened" line would drown the moves that mattered.
- **Append, never clobber.** The section body is bounded by the next `## ` heading, so every other section and every earlier move is copied through untouched. A `state.md` with no such section gets one created just above `## Recent Events`, which is where `templates/state.md` keeps it, rather than at the end of the file where it would land under the DM-only notes. The `*(none yet)*` marker and the template's italic helper line are dropped when a real move lands above them, since `is_template_line` already hides them from the digest and leaving them would read as "empty section" to the GM.
- **Atomic, with the repo's existing `.bak` convention.** The write reuses `scripts/tactics/state.py`'s `save()` idiom verbatim: temp file, `flush()` + `os.fsync()`, `shutil.copy2` to `state.md.bak`, one `os.replace`. A campaign with no `state.md` is left alone rather than having one invented. The write happens *after* `factions.json` is safely on disk, so a crash between the two loses the state.md note rather than the tick.
- **The GM's terminal is unchanged, which was the point.** The same per-faction report, the same `⚡ ... COMPLETE` line, and the same `complete "<name>"` instruction still print. Only the wording of the second fired line moved: it now says the result *is* recorded and asks for the visible change on top of it, because that is the work that is still the GM's. Writing the clock result is not narrating it, and `SKILL.md` still asks for the GM's own words.
- 12 new tests in `tests/test_world_clocks.py` (the entry is written; the digest actually reads it through `state_digest`; a second tick appends; the template-only section is filled in; a missing section is created in the right place; every unrelated section survives; the previous `state.md` is kept as `.bak` and no `.tmp` is left behind; a no-op tick leaves the file byte-identical; a campaign with no `state.md` is untouched; and the GM-facing output still says what it always said).

### Fixed: a character sheet written the way the SRD writes one no longer ends the session
- **The SRD names a crit in parentheses — `1d6 (1d8 crit)` — and that is correct as written.** `_attack()` normalised it with the same `replace(" ", "")` that tidies `2d6 + 1`, which produced `1d6(1d8crit)`. `scripts/dice.py` does not accept the annotation, so `ValueError: Cannot parse dice notation` came out of `average_damage` — reached while the AI weighs which enemy is worth an opportunity attack — and ran clean out through the REPL. `play.py` exited mid-fight and the encounter was lost. Nothing in the campaign was wrong; the parser was narrower than the content it was fed. Found on a from-scratch test campaign whose rogue sheet was written the way a person writes one.
- **Two defects, and fixing only the obvious one would have left a worse bug.** `roller.average` is called only from heuristics, so it now returns `0.0` for a string it cannot read instead of raising: a bad damage string can cost a bad choice, never the session. But `1d6(1d8crit)` must crit for `1d8`, **not** `2d6` — so `roll()` uses the annotated crit when there is one and doubles the dice only when there is not. Stopping the crash alone would have left every annotated crit hitting harder than its statblock says, which is the one class of error the table has no way to check. `_attack` splits the annotation into a `crit_dice` field, so the token is right at rest and not only at roll time. 8 tests in `tests/test_crit_notation.py`.
- Found by `test_playtest.py` on `smoke-sandbox`; see `docs/test-reports/TEST-REPORT-smoke-sandbox-llm-2026-09-30.md`.

### Fixed: a character sheet that exists is no longer reported as absent
- **`find_sheet` matched a filename stem against the character's full name.** A sheet at `tamsin.md` titled "Tamsin Underbough" — the shape a character creator produces, since a person has two names and a file usually gets one — matched nothing, and `end` said `no sheet in characters/, nothing written.` That sentence was false. What was lost is the fight: HP, spent hit dice and death saves stayed on the encounter file, so a character who dropped to 0 still read as untouched. A naming slip and losing a fight's results to one are not the same size of thing.
- **Filename first, still exact, then the sheet's own `# Name` heading, also exact.** The order matters and is tested as such: a loose rule would let `Kairos.md` answer for "Kairos Kestrel" and write one character's results onto another's sheet. Verified against the live campaign — `Kairos` still resolves to `Kairos.md`, and `Kairos K` still resolves to nothing. 6 tests in `tests/test_find_sheet.py`.

### Fixed: the engine's own instructions are no longer shown to the player as prose
- **On 2 turns in 11 the narration contained a literal `{"check": "Persuasion 13"}`.** `strip_think` only matched a balanced `<think>...</think>`; qwen closed a reasoning block it never opened, leaving a bare `</think>` *after* the directive. `parse()` anchors its JSON search to the end of the text, so the directive stopped being found and stayed in the narration. A model that thinks out loud, which is most of them, was writing the engine's instruction sheet into the story.
- Unpaired think tags are now removed on their own, and there is a last-resort search for the directive anywhere in the reply — keyed on one of the four directive names, so an ordinary brace in prose (`A {rough} noise.`) cannot match it.

### Fixed: `test_playtest.py` can seed from the live campaign, and its scenario is not hard-wired to Kairos
- The harness seeded its sandbox from `~/open-tabletop-gm/campaigns/`, the stale empty shell documented as B1 in the README, so it raised `No campaign` for everything but the default — the one campaign it was written for. It now resolves `GM_CAMPAIGN_ROOT` first, like every other entry point, and takes `--root` to override both.
- **The default scenario is Kairos, frog-pond and mage armor, so running it against any other campaign tested a campaign the player does not have.** `--scenario` reads player turns from a file, one per line.
- **Advantage and disadvantage prompt differently — `type the highest face` / `type the lowest face` — and `ROLL_PROMPT` only knew `type the number on the die`.** The driver saw no pending roll and typed the *next scripted line* at a prompt waiting for a die; `retreat` was consumed as a d6. That is exactly the failure the harness exists to prevent: the step after the roll tests the wrong thing and the report blames the DM for a turn it never saw. It matters more than it looks, because advantage is what a grapple induces, so most fights reach one. All three phrasings now match, plus a second independent trigger on the engine's own `still waiting on your roll`, so a future rewording degrades to a second line of defence rather than a silent one.

### Added: formations, a saved monster arrangement that crosses maps (BV5)
- **Setting a fight up is real work, and all of it was being thrown away.** Walking two kobolds into an aisle and putting a third behind the desk takes a GM a minute and a thought. It was only ever stored in `combat/encounter.json`, which the next fight overwrites and `end` retires, so reproducing it meant remembering the coordinates and retyping `--monster "Kobold@W11"` from memory. A **formation** is that arrangement and nothing else: `<campaign>/encounters/<slug>.json`, plain JSON, readable and diffable, beside `combat/encounter.json` and `tracker.json`. Four commands — `formation save|list|show|place` — and `start --formation`, which replays one onto any map.
- **It is not `spawns[]`, and the reason is the useful part.** Maps have had a `spawns` field for ever, and `maps.compile_map` has copied it into `meta` where nothing in `scripts/tactics/` reads it. A formation is not a map's residents, it is an *arrangement* — and it has to work on the island in the Detention Bog and in the restricted stacks alike. A map that restates its monsters is one map per encounter, which is exactly what BV9's table of art/creature coverage was measuring: every map with artwork had no creatures, every map with creatures was gridless and invented for one fight. **`compile_map` is untouched and has no formation field**; a new optional field changing `meta` only, with `grid.rows` byte-identical, is asserted as before.
- **Both offsets are stored, and neither is the design alone.** Each member keeps a cell offset `dx, dy` from the anchor *and* a pitch-normalized `nx, ny` as a fraction of the captured map — the trick Atlas uses with `spriteTransform`/`spriteNormalized`, and for the same reason. `--at SQUARE` pins the anchor and uses the cells, which is exact and is the only guaranteed-exact mode. With no `--at`, members land on the same *fraction* of the target map, so a formation survives a change of grid size and position. Cell offsets alone would put a 30×20 formation in the top-left corner of a 20×14 one; normalized offsets alone would stretch a tight six-square ambush across a wide room. Both failures are tested as *differences*, because a change that quietly dropped the normalized pair would still pass every same-map test.
- **The first implementation of that was wrong, and the way it was wrong is the reason it is written down.** Adding the normalized offset to a chosen centre, rather than reading it as an absolute fraction, pushed every member past the right edge of a 24×18 map; all three kobolds clamped into one column and the report said nothing. The fix was to delete the clamping rather than to describe it, and `clamped` is gone from the API entirely — `off_map` now only ever means a *pinned* replay that does not fit, which is a different problem with a different fix.
- **Two monsters on one square is a rules error, and proportional replay causes it on real data.** Two monsters a square apart on a wide map round to the same column on a narrower one. Caught by running `formation place` against the new rotunda rather than by any test, which is the argument for having a command that starts nothing. The later of the pair now moves to the nearest free square, found breadth-first so the move is the *smallest* one that fixes it, and **every move is reported with both squares** — a nudged monster is a changed distance and only the GM can say whether it is the one they wanted.
- **Three reports, three meanings, never merged.** `blocked` (in a wall — nothing moves; `state.validate` refuses the encounter and names the token), `off_map` (pinned wrong — reported with the square it wanted), `separated` (moved off a collision), and `unplaced` (collided with nowhere to go, reported rather than dropped). An earlier version treated "taken or blocked" as one condition and moved monsters out of bookcases, which quietly deleted the most useful thing the function says: that the formation does not fit this room. The three-case split is pinned by a test.
- **A formation stores no HP, AC or conditions.** Asserted key-by-key, because a formation that carried them would replay a corpse layout as a live fight. Player characters are excluded by default — the opposition is the reusable part and the party is placed with `--pc` — with an explicit `--include-pcs` for a map whose whole setup is worth keeping. The party-exclusion also has to be stated in the file: a formation that included the party would put players back where the GM had already moved them out of.
- **A formation stores the *creature*, not the instance, and the first version got this wrong in the worst possible way.** `--monster "Kobold@W11"` twice puts "Kobold" and "Kobold 2" on the board, and v1 captured those display names — which `token_from_monster` cannot resolve. So a formation saved cleanly and then failed on replay with `no SRD monster 'Kobold 2'`, for **every save with more than one of a creature**, which is most formations. It surfaced only by running a saved formation back through `start`; no test caught it, because nothing in the module resolves a creature name. Each member now stores `name` (the bare creature, which is what the SRD and the statblock fold both need) and `label` (what the token wore, which is what an Atlas nameplate should read — three tokens all reading "Kobold" is a worse preview than three reading "Kobold 1..3"). `SCHEMA_VERSION` is 2, with a `(1, 2)` migration doing the same split, so a file written by the broken version keeps working.
- **Side colours are inherited from the map that already chose them.** A campaign's map has been saying "stirges are danger red" since before this existed; a formation that invented its own would make the Atlas preview lie about which side a token is on. Matching is folded, with the same three plural probes `map_to_atlas.bestiary_note` uses, because a map spells one stirge "Stirges" and the board spells it "Stirge 2" and an exact match finds neither.
- 39 new tests in `tests/test_formations.py`, including the round-trip, both offsets as a *difference*, the two-monsters-one-square fix, the 1×1 map where a formation cannot be separated, the name/label split and its migration, and that saving one never touches the encounter file or `display/maps/`.

### Added: a formation is the creatures, a map is the room — which unblocks Atlas statblocks without artwork (BV9)
- **BV9's blocker was recorded as "the blocker is pixels, not code", and the stated fix was to import artwork.** The evidence was a disjointness: every map with artwork had no creatures to link a statblock to, every map with creatures was gridless and hand-written, so no scene could carry a `statblockPath` — the link path was proven against the real 334-note bestiary and had nothing to attach to. The roadmap's own fallback was to declare `grid: {cell_px: 100}` on the artless maps and export with `--allow-no-image`, honestly labelled "not a battle map". **This is that fallback, and it turns out to be enough for the link problem specifically.** A formation is campaign data that applies to *any* map, so `--formation` breaks the disjointness: `biblioplex-stacks` is terrain and a grid with no spawns, and three kobolds from a formation give it creatures, and all six kobolds link to a real `Bestiary/Kobold.md`. Verified end to end against the actual 334-note export — 14 links across three scenes, 0 dangling, every path read from the directory listing rather than constructed.
- **What is given up, said plainly: the background.** A formation cannot supply a picture, so the scene sits on Atlas's placeholder. It is a legible layout preview with working names and clickable statblocks, and it is not a battle map. That trade is the whole of the feature, and it is the opposite trade from the one the roadmap was making.
- **The exporter calls `tactics.formations.positions` — the engine's own function, not a second implementation of it.** A preview that placed tokens differently from the fight would be worse than no preview, and the only real guarantee against that drift is one placement function with two callers. It builds the grid through `maps.compile_map` too, so the preview's board is the fight's board rather than a second reading of the JSON. Both are asserted structurally, because a copy of `positions` inside the exporter would pass every behavioural test here and then drift.
- **`build_scene` is untouched, and its 38 tests still describe it exactly.** The mechanism is that a formation becomes ordinary `spawns` and the existing code path handles it, which is why a formation works with every existing flag at once. `build_scene` cannot tell a formation-supplied spawn from a hand-written one, and that is the point.
- **One-way stays one-way.** The map file is not modified and the formation store is not modified, even when the replay had to move a monster — asserted, because an export that consumed a formation would make a second export differ from the first. `--formation` without a campaign is refused in one sentence rather than a bare `KeyError` from deep in the loader, and a typo in `--at` writes nothing.
- 16 new tests in `tests/test_map_to_atlas_formations.py`.

### Added: three maps from the Kairos worklist, authored and verified
- **`hesper-walled-garden` (20×14), `biblioplex-stacks` (30×20) and `enrollment-ledger-rotunda` (24×18)** — items 1, 2 and 3 of the "Generate — invented places" table in `docs/guides/map-plan-strixhaven-kairos.md`, at that document's sizes. Terrain only, no artwork, via the generate route in the worklist. All three declare `grid: {cell_px: 100, offset_x: 0, offset_y: 0}` so they export to Atlas.
- **Loading a map is not the same as it being playable, and the difference is invisible in the JSON.** `compile_map` raises on a bad rectangle; it cannot see a map that is *sealed*. Both early drafts of the stacks had shelving runs that walled off a third and then two thirds of the map and compiled perfectly cleanly — a sealed room is a valid set of rectangles. The rotunda was built as a staircase of 33 wall stubs because a circle in a square grid is the only honest way to say "round" without teaching the format a shape.
- **So the property is a test, not a claim.** `tests/test_campaign_maps.py` flood-fills from the party and asserts every standable square is reachable, measures how much of the map one creature can see from the middle, and checks that no spawn is in a wall. The numbers are the argument: the garden 87% visible (open ground), the stacks **18.6%** (sight-blocking is that map's entire reason to exist, and a shelving maze you can see across is a room with shelves in it), the rotunda 64% (one round hall, meant to be open). Add a map to `WORKLIST` in that file and it is checked from then on.
- **`formation place` earned its keep on the way.** Replaying the stacks formation onto the rotunda is what surfaced the two-monsters-one-square bug above. A command that starts nothing and shows you where things land is the cheapest bug-finder in the toolset.


### Fixed: a directory with a campaign's name is no longer resolved as that campaign
- **This changes resolution for every campaign on the machine, so it is called out first.** `paths.find_campaign()` resolved a campaign by *name* alone: `campaign_dir(name)` returned `<root>/campaigns/<name>/` whenever that directory existed, and every caller then read files out of it. Nothing checked the directory was a campaign. A campaign that moves leaves an empty shell at the old path, and because the shell is at the configured root it wins over the real one: nothing ever reaches the legacy fallback, and every documented entry point (the README's "Continue a campaign", the `hermes-gm` preflight, `display/preflight.py --campaign`) lands on it. The failure is silent by construction, because a shell reads as an empty campaign rather than an error.
- **The instance that prompted this.** The default root `~/open-tabletop-gm` held a `strixhaven-kairos` with two entries, `atlas-vtt/` and `.obsidian/`, and no campaign content. The real campaign, 35 entries, was at `~/github/strixhaven-kairos/campaigns/strixhaven-kairos`, and the campaign had moved twice before that. `find_campaign("strixhaven-kairos")` returned the two-entry shell, so the linter, the display and `/gm load` all reported a campaign that was not there.
- **`_is_campaign(path)` now gates both branches.** A directory is a campaign only if it holds a `state.md`, which is the one file every consumer already depends on: the linter requires it, `campaign_system` reads it, and `context.state_digest` builds the DM prompt from it. It is applied to the configured root *and* to the legacy fallback, because a shell is just as possible at the old path as at the new one.
- **A directory that exists but is not a campaign is a miss, not a hit.** `find_campaign` returns the not-found sentinel in that case, so callers report the campaign as absent. This is what makes the failure useful: `preflight.py` exits 1 and names the campaigns that do exist, instead of exiting 0 on a folder with nothing in it. The trade is honest, too: a campaign genuinely missing its `state.md` now reads as missing. That is a broken campaign, and the linter is the tool that says so.
- **Migration is guarded by the same test, which is the part that could have done real damage.** `find_campaign(name)` with the default `migrate=True` copies the legacy tree into the configured root. Unguarded, it would have promoted the two-entry shell into the configured root permanently, turning a fixable misconfiguration into a stuck one. It is the reason the legacy branch needed validating and not just the first check.
- **Interim workaround, for anyone who hits this before upgrading:** `export GM_CAMPAIGN_ROOT=~/github/strixhaven-kairos`. That moves the trap rather than removing it, which is why the code change is the one that landed.
- 11 new tests in `tests/test_paths_campaign_resolution.py` (the name-only match, the shell losing to a real campaign, both branches, migration not copying a shell, real migration still working, and `campaign_system` / `campaign_system_version` reading through the same resolution), plus one in `tests/test_display_preflight.py` pinning the entry point that reported the bug. Two existing tests built a "campaign" as a bare empty directory and were corrected: that is the shape the fix exists to reject.

### Added: token portraits, drawn inside the shape that already said which side it was
- **A creature on the map was a coloured shape with three letters in it.** That reads at a glance and the whole display is built on it, but it is not a face, and after three sessions a table stops recognising "the octagon" and needs to be told who it is looking at. A token may now carry a `portrait`, drawn clipped to the token's existing silhouette. The set is 98 PNGs by **hearden**, a Strixhaven community artist, organised by college, and it lands the deans, the students, and a set of monsters that belong to this campaign's own fiction (`daemogoth`, `archaic`, `cogwork archivist`).
- **The portrait replaces the fill. The frame does not go away.** The frame is a notched octagon for an enemy and a circle for everyone else, and it is the only thing that says which side a creature is *without* relying on colour — that is what `drawToken` has always claimed, and it is a claim about greyscale and colour-blind players. A screen full of portraits where you have to hunt for the red one is a regression in disguise, so the shape is chosen once and stroked last, over whatever is inside it.
- **The fallback replaces the art in place rather than appending it.** The first version appended the coloured shape from the `error` handler, which runs after `drawToken` has finished, so the fill landed *on top of* the frame and hid it. Caught by rendering the panel and looking at it, not by the tests: the browser-measured check asks which shape is present, not what covers what. Pinned now.
- **Opt-in per map.** `"portraits": true` in a map's JSON. A portrait is art the GM has chosen, so a map that did not ask does not start putting faces on its monsters, and a token whose name resolves to nothing keeps drawing exactly as it always did — which is most of the SRD's 334.
- **The art is gitignored**, on the same reasoning and the same precedent as `display/maps/images/`. `scripts/tactics/token_portraits.py` *is* committed: it is the index, the index is what credits the artist, and a clone without the PNGs still knows the set exists. Free to download is not free of the artist's claim.
- **Display-only, and the BV1 constraint holds.** `compile_map` is untouched, `grid.rows` is byte-identical with the flag on or off, and the engine never reads `portrait` — the same rule map images followed. 20 new tests, including that one.

### Added: the chance is shown where a player doubts it, not before
- **RI2's display half.** The engine already computed the chance and attached it to the resolved roll (`Roll.odds`, carrying `hit_chance`'s and `save_chance`'s own answer), and the pre-action badge already showed it — but the badge is the wrong moment. A player doubts a roll *after* seeing the 3, and by then the preview has scrolled away, so the number existed nowhere at the point of doubt. Three surfaces now carry it: the roll **toast**, the **side-panel log** line (which keeps all eight entries), and a **board float** over the creature the chance was about.
- **The number is never re-derived.** `oddsText` formats whatever the system wrote and rolls nothing itself, so a system that wants to label its odds "to parry" says so in the label and the display is untouched. The direction rides in that label and is not assumed: `65% to fail the save` is `save_chance`'s chance to *fail*, and reading it as a chance to succeed inverts the only number on screen.
- **Nothing is the odds' only carrier.** The float is the one surface allowed to go missing, and for a reason the other two do not share: it must name a token that is actually drawn, and `about` may be a creature the players cannot see. The toast and the log line consult the board for nothing, so a missing float costs a number nowhere. (When an entry names an unseen creature, `sight.redact_log` empties its whole `rolls` list and all three are correctly silent — the chance is a function of that creature's AC.) The float also never reaches for the encounter, only the snapshot, so it cannot draw a token the players may not see.
- **The float stays on the board.** "40% to fail the save" is about three cells wide, and the board is an SVG `viewBox` where anything past the edge is not drawn at all — so a token in the first or last column silently lost most of its text. The first fix set a `transform` and looked right in the diff: the rise animation is a CSS `transform`, which beats the SVG attribute of the same name, so it moved nothing. `keepOnBoard` shifts the coordinates, and the tests measure rendered rectangles, which is the only thing that could have caught it.
- A roll with no chance attached (a damage die) adds no line, and says nothing rather than `0%`.
- **The chance is readable, not merely present.** The first version styled the log's odds line with `--tx-line`, which is a *border* colour: 1.35:1 against the panel in the dark theme and 1.57:1 light, where 12px text is a smudge rather than a number. Every DOM assertion passed anyway, because they read text content and contrast is not in the text. It now uses `--tx-muted`, the log's own colour, which clears WCAG AA (4.5:1) in all three themes, and two tests measure the ratio per theme so a border token cannot be reintroduced.
- 29 new tests: 14 pin the helpers and the asymmetry between the three surfaces, 10 render the panel in Chromium and assert the number is on screen, 3 measure the odds' contrast. The 8 render tests and the 2 contrast tests all fail against the previous `tactics.js` and `tactics.css`.
- **The redaction path, tested with a roll the engine actually made.** The two existing sight tests append a log entry by hand, which pins `redact_log` but leaves the interesting path open: a real `saving_throw` filling `Roll.odds` in, for a creature the players genuinely cannot see, arriving in the snapshot the display is sent. Three tests now build that encounter and assert the display receives no faces and no chance, then run the display's own `entryOdds` over the real redacted entry and assert it yields nothing. A chance surviving there would hand over the DC the entry just described, since the number is a function of it.

### Fixed: the Atlas scene import works on a file Atlas actually writes
- **`atlas_to_map.py` refused every real `.atlasmap`.** Atlas persists through zustand's `persist`, so a scene file on disk is the storage envelope `{state: <MapFile>, version: N}`, not the bare `MapFile` — Atlas's own `MapLoader.ts:36-37` unwraps `state` before reading `background`. The script read `schema` off the top level, found `None`, and raised `expected schema 'atlas-vtt', got None` on every scene Atlas has ever written. The fixture held a bare `MapFile`, a shape Atlas does not write, so 18 tests passed green against a format that cannot occur. `load_scene` now unwraps `state` and falls back to the bare shape (`migrateMapFile` produces that one, so both are real); the schema check still runs against the unwrapped object, so unwrapping is not a way in, and a non-dict `state` is refused rather than read as an empty map.
- **A metric grid was read as feet.** `grid_geometry` read `unitDistance` and assumed feet, ignoring Atlas's `unitType` (`feet | yards | meters | units`). A scene in metres with `unitDistance: 5` became a 5-foot grid: every range, reach, speed and opportunity attack out by ~1.6×, rendering correctly and playing wrong — the precise failure KC4 exists to prevent, in the one script that claims to refuse it. Non-foot units are now a hard error naming the fix; unset stays feet, which is Atlas's default and what a fresh scene carries.
- The fixture is now the envelope Atlas writes, with the bare-`MapFile` case kept as its own test so both shapes stay covered. 7 new tests; the 20 that now exercise the real format fail against the previous script.

### Added: a linter for the campaign markdown, so a missing section stops being silent
- The campaign is hand-written and model-written markdown, and the only schema in the project covers `combat/encounter.json`. Nothing checked `state.md`, `world.md`, `npcs.md` or the character sheets, so both failure modes were invisible. A section renamed from `## Active Quests` to `## Quests` costs the table a whole section of DM prompt every turn and reports nothing, because `context.state_digest` greps for the exact heading and simply finds less. And a `world.md` line left as `<what the world looks like today>` is *dropped* by `notes_digest` before the model sees it, so a half-filled campaign and a finished one are byte-for-byte indistinguishable to the thing deciding what the DM knows.
- `scripts/campaign_lint.py` reports both, with a line number and the reason. `errors` are the things that matter at the table: a missing required section (each one annotated with *who reads it*, so the fix is not a guess), a header field that lost its `**Label:**` wrapper, a `Session count` that is not a number, an arc block that does not parse, HP over max on a sheet. `warn`s are present-but-blank: unfilled placeholders, empty `**Field:**` lines, an `npcs.md` index row with no matching entry.
- **The linter and the prompt cannot disagree about what "unfilled" means**, which is the only way this is worth having. The digest's four placeholder regexes were inline and unnamed; they are now `context.is_template_line(line) -> str`, returning *why* a line is template rather than a bare bool, and both callers use it. A test asserts the round trip directly: a line the linter calls filled reaches the digest, and one it calls template does not.
- Two deliberate exemptions, both because flagging them would bury the real gaps: italic helper lines (the digest drops them on purpose, they are the GM's own instructions, meant to stay in the file) and the `<placeholder>` strings inside the arc's ```yaml fence (the shipped template is full of them by design). An arc block that does not parse is an *error*, not a crash, nothing else reads that block, so a syntax error would otherwise sit unnoticed until the GM needed it mid-session.

### Added: the advisor council's notes outlive the process that asked for them
- `/advise council` output went into `Session.saved_notes`, was handed to the next DM turn, and was then gone with the process. A council run about a plot thread between sessions, the exact case where the GM wants advice, evaporated overnight, and the notes log the test report had been asking for did not exist.
- Every consult is now also appended verbatim to `<campaign>/localdm/notes.md` under a `## <stamp>, <source>, <advisors>` header: `/advise` and `/advise council`, the background shadow advisor, and trigger-driven consults all say which, so the file reads as a record rather than a pile. `/notes [n]` in the local-DM REPL reads the tail back. Trimmed at 256 KB by dropping whole leading blocks, never a partial one, half a note read as advice is worse than no note.
- Kept verbatim and never folded, for the same reason `canon.jsonl` exists: an advisor's exact wording is the thing the GM wants to read, and a summary of it is a different claim. **It is also never read back into a DM prompt.** The notes still reach the DM through `saved_notes`, exactly as before; writing them is about the GM being able to read them afterwards. A DM briefed on its own advisor's advice stops consulting anyone. A failed consult writes nothing at all, a transport error in a file the GM reads as advice is the B2 bug in a new place, and a write failure (an unwritable campaign folder) costs the turn its advice, not the other way round.

### Added: the odds sit next to the resolved number, not only before it
- The chance of a check was computed for the pre-action badge and thrown away at the moment it resolved, which is the wrong moment: a player doubts a roll *after* seeing a 3, not before choosing to attack. `Roll` now carries `odds`, and the system that knows the number fills it in: `systems/dnd5e/tactics_rules.py` sets it from `hit_chance`/`save_chance` at the moment it rolls, calling those same functions rather than recomputing, so the badge a player chose on and the number printed beside the result cannot disagree. The `Rules` interface is unchanged: `odds` is optional, system-neutral and read by nobody but the display, and `attack()`'s result dict carries a copy for the GM.
- **The display** prints it in the same breath as the total, in three places that fail differently. The combat log line is the durable receipt: it is drawn straight from the snapshot, so it is on screen the moment the engine result lands and stays until the log scrolls. The toast carries it transiently. A float rises off the square the roll was made against, using the existing transient float layer. The layering is deliberate and is not to be tidied up: Dice So Nice, the most common animated-dice module for Foundry, drops rolls outright when several resolve in quick succession ("neither visibly roll nor show up in the chat log"), and directional area effects are the highest-roll-density moment in the game. Nothing about a resolved roll's visibility may depend on an animation completing, so the two synchronous renderings are written first and the float is decoration over them; under `prefers-reduced-motion` the float disappears and the log line does not.
- **Fog of war still holds.** The odds are about a creature: a percentage to hit plus a damage die is a way to read back an armor class. They therefore live *inside* the roll dict, which `sight.redact_log` already drops whole for an entry naming a creature the players cannot see, rather than on the log entry beside it, where they would have survived the redaction. `SYSTEM-PORTING.md` documents the mechanism for other systems, `rules.py` states it as part of the contract, and the comment on the float says what will happen to it otherwise.

### Fixed: a failed advisor is reported as a failure, not as a note
- `advisor.consult` wrote a per-advisor failure inline as `Director: (unavailable: HTTP 504 …)` so a partial council stayed visible in the transcript. That text then travelled on as though it were advice: `/advise` filed it as campaign notes and told the player *"(The advisors have been consulted. Their notes will guide the next scene.)"* while every advisor was 504ing, and the next DM call was briefed on `OmniRoute's local rate-limit` as continuity guidance — which is why prompt tokens rose after a dead consult. `Session._ask`'s `LLMError` handler was unreachable throughout, because `consult` swallowed the per-advisor error itself.
- `advisor.split_notes()` now parses advice and failure apart in one place and `Session._ask` returns `(notes, failed)`. Only real notes are filed or reach a DM prompt; `/advise` names who could not be reached rather than announcing notes that do not exist; a failed guardrail consult is no longer cached as a ruling, so a later turn can still get one. The shadow advisor's `startswith("(unavailable")` string sniff is replaced by the same parser.

### Fixed: a turn the engine answers is still a turn the player took
- `_player_turn` returned before `memory.add("player", line)` for any line the engine answered itself, so a refused attack (`cast mage armor`, `I attack the Innkeeper`) and a line typed while a roll was pending (`retreat`) vanished from `transcript.jsonl` entirely. The player demonstrably said them and the game kept no record — which is also an amnesia event for every misrouted verb, since the DM's next turn had nothing to acknowledge.

### Fixed: the pre-roll beat may not state the outcome, and the roll prompt no longer contradicts itself
- **The pre-roll beat.** Nothing stopped the DM saying "you find the hidden latch" in the beat *before* the check, which quietly adjudicates the roll the engine is about to make — so the roll then contradicts the story the player was already told. `reply.reveals_check_outcome()` adds the guardrail, scoped to `r.check` in `_player_turn` and deliberately **not** in `_dm()`: `_dm()` also serves `_check_narration`, which narrates a check the engine has already resolved, where naming the outcome is the whole job. The regex does fire on the post-roll sentence; it is the scoping that protects it, and a test pins both halves.
- **The roll prompt.** `play.py` returned the engine's `res.text` verbatim while a roll or reaction was pending, so the player was told to re-run a CLI command with `--roll` and, two lines below, to type the number instead. `combat.py` answers a pending roll by telling the *GM* to re-run the command with a flag — right in a terminal, wrong here, where this loop answers a bare number and a bare yes/no and players have no terminal at all. `_question()` strips the instruction; `_hint()` already said what to type.

### Added: an out-of-combat spell with a lasting effect is resolved on the engine
- `cast mage armor` outside a fight was refused with a message about fighting, because `declares_attack` matched the bare word `cast` — so out-of-combat spellcasting was entirely unreachable, and every 1st-level spell a silent input. A cast verb now only counts as an attack when it is aimed at something other than the caster (`I cast burning hands at the bench` still is; `I cast Mage Armor on myself` is a buff). The asymmetry that made it read as intermittent — base form `cast` matched, `casts` did not — is gone.
- A lasting-effect spell is now resolved on the engine rather than narrated: the slot is spent, the effect is written to `tracker.json`, the display sidebar picks up the new AC without a fight, and the DM is handed the real numbers as an Engine fact. Previously the prompt forbade the model from stating an AC or duration at all, which left the only option an invented one.

### Added: Party Input sends straight to the DM, with Recall
- Stage, then Ready, was three taps to say one thing. It is now one **Send** (Enter works too), and revising is an explicit **Recall** rather than a step you cannot skip. Sending again replaces your own pending action, so a player who changes their mind never leaves the GM holding both versions.
- The part that is easy to get quietly wrong when a step is deleted: `.input_queue` is one file that several phones write independently. The old code rewrote it wholesale on a single fire, so a one-tap version that still rewrote would let the second player of the round silently erase the first. `_queue_append` takes a locked read-modify-write, never truncates, and replaces only the sending character's own line.
- Honesty about what happened: Recall returns `409` once the DM has consumed the queue and the button reads "Delivered"; a failed send keeps the text and the localStorage cache so a tap re-sends without retyping; and `/player-input/stage|ready|unstage` are gone rather than aliased, so a stale client gets a 404 instead of a silent no-op.

### Fixed: display state, the dice pad, and the scene name on a campaign switch
- **`/clear` wiped only the text log and stats.** Sent actions, the queued-input files and the sidebar's turn order survived, so one campaign's state surfaced in the next and `/dnd new` needed a manual `/clear` to look right. The wipe moves into a shared `_do_clear()` that `/chunk` also calls automatically when the incoming campaign name differs from the one on file. Re-registering the *same* name deliberately does not wipe — that is a `play.py` restart re-announcing itself, and clearing there would destroy a live session's input.
- **The dice pad was unreachable on the main view.** `#dice-pending-badge` was `pointer-events: none`, so the "Waiting on: <name>" badge was decorative and a player watching the big screen had no way to roll at all. The badge now floats the pad out as a small panel; Escape or a second click closes it, and it closes itself when nothing is waiting any more. Note the trap: the pad's ancestor `#input-body` is `display: none` while the panel is collapsed, and a `position: fixed` child renders nothing inside a hidden ancestor — so the collapse has to be overridden for as long as the pad is floated, and everything else in that body hidden with it, or the character tabs land on the narration. A follow-up (#63) completes it: `_initDicePad()` was still called only inside `if (_inputMode)`, so the main view floated a pad whose Roll button had no listener — reachable, visible, and inert. Clicking Roll fired no request there; it now fires `POST /player-input/dice`, as the input-only view always did.
- **`_current_scene_name` survived a clear**, so a cleared session kept the last detected scene ("dungeon") and the title and background were wrong until enough new narration re-triggered detection. The reset rides in the clear broadcast, because a phone already holding the page learns nothing from a fresh `/stream` read.

### Fixed: the display layout test must release its listening socket
- `tests/test_display_tactics_layout.py` bound a hardcoded port and released it with `TCPServer.shutdown()`, which stops the serve loop but does **not** close the listening socket. With no `allow_reuse_address` the port stayed bound after the process exited, so the next run's bind failed with `Errno 48` and lost all 8 tests in the class at `setUpClass`. Observed as `1 failed, 919 passed` on one run and `8 errors` on the next.
- `server_close()` in `tearDownClass` is the part that does the work. Measured on the same port, one process each: `shutdown()` alone still fails the rebind, `shutdown()` + `server_close()` succeeds. `allow_reuse_address` and skipping on a bind failure are belt and braces — they make a busy port read as *skipped* rather than *failed*, but neither releases a socket that is still open. Five consecutive runs of the class go from 8 errors to 8 passed.

### Fixed
- `tests/test_dice_lens_harness.py` asserted on a single unseeded d20 (`assertNotEqual(got, 1)`), which is a coin flip that failed ~5% of runs regardless of the code under it. Both tests now roll 50 dice and assert the results are not all identical — a pinned die cannot produce a spread, so the flake is gone rather than merely rare. Measured 4 failures in 60 runs before, 40/40 clean after.

### Changed: CI action pins
- `actions/checkout` v4 → v7.0.1 and `actions/setup-python` v5 → v7.0.0, both SHA-pinned as the workflow already did. Both branches were cut by dependabot on 2026-09-16 and sat with no PR. The setup-python bump needed rebasing: applied as-is it would have reverted the checkout pin to v4, which is the failure mode branch drift invites and which a clean-looking 2-line diff would have hidden.

### Added: spell slot tracking (Phase A Task A3)
- Spell slots now have one home, one type and one set of rules. `scripts/tactics/slots.py` is the only module that knows what a slot is: what is left, what a cast costs, what a rest gives back. `spells.py`, `effects.py` (Shield, Silvery Barbs) and `actions.py` (a readied spell) all spend through it instead of each doing `extra["slots"][lv]["used"] += 1` and hoping they agree.
- **The data stays in `extra["slots"]`, not in a new `Token.spell_slots` field**, and this is a decision rather than an omission. The shape is not the engine's to change: the character sheet has a Spell Slots table, `tactics_sheet.py` parses it and writes it back, `sync.py` hands it to the display, and the sidebar draws it as pips that drain and refill. A first-class field would rename the same value in five modules and migrate every live `encounter.json` without making any of them more correct — and two names for one number is how a sheet and a sidebar start disagreeing. What it buys instead: `schemas.SPELL_SLOTS` is the type that data has to satisfy, `state.validate()` checks it on save and load so a typo is named at the file boundary, and `Token.spell_slots` is the typed, self-describing way to reach it.
- `cast` now prints what is left after it spends: `Kairos casts Magic Missile (level 1 slot). … Spell slots: 1st: 1/4, 2nd: 3/3, 3rd: 2/2`. A refused cast is still a refusal — nothing changes, not even the action, and the message names the levels that *are* left.
- `status` shows the slots of every creature that has them, in a readable form: `Kairos A1 8/8 (Spell slots: 1st: 2/4, 2nd: 2/3, 3rd: 0/2)`. The counts are **remaining, not spent** — that is how a player reads "2/4" at the table, and it is what the pips are showing them.
- `rest short|long [--token NAME] [--for-me]`. A long rest restores every slot (PHB p201). A short rest restores only what the class's own features restore: a Warlock's Pact Magic refills by itself, a Wizard's Arcane Recovery buys back up to half the caster's level in slot levels, cheapest first, once per long rest (PHB p107, p112), and everything else is the GM's ruling. Sorcery Points are reported and never converted — they are spent to *make* slots, not to get them back.
- **A sheet that lists a feature beats the SRD.** Kairos' own sheet writes him Arcane Recovery at wizard 1 rather than 2, and he gets it at 1. The engine is not here to overrule a GM's ruling on a character's actual sheet.
- New `systems/dnd5e/spell_slots.py`: the SRD's caster tables, so a caster whose sheet has an empty (or template-untouched) Spell Slots table can still cast instead of being indistinguishable from a caster who has spent everything. A table the sheet has is never overwritten, and a class the 5.1 SRD has no table for — Artificer, any homebrew caster — is left as the sheet left it. A caster's **class and level** are now read from the sheet's Identity line, which nothing read before.
- `SCHEMA_VERSION` is 3, with a `migrate_v2_to_v3` that puts spell slots in one shape. Three spellings were in the wild — the engine's `{"used", "total"}`, the display's `{"remaining", "max"}` (normalised by hand on both ends of that pipeline since 0.7.0), and a bare number — and they were only normalised at the point of use, so a level could be spent against one spelling and read back in another. Reading a fight still never writes to it; the next save upgrades the file.
- `systems/dnd5e/tactics_sheet.py` reads the Features & Traits section into `extra["features"]`, which is what "does this creature have Arcane Recovery" now answers from.
- New `tests/test_spell_slots.py` (37 tests) over A3.1–A3.7: the schema and the accessor, spending and refusing, upcasting to the right level, a reaction taking the cheapest slot, long and short rests per class, a golden `status` line, the SRD tables, a v2 file in the display's own shape still loading and still playable, and the `rest` command end to end. No unseeded dice.
- `scripts/combat.md` and `scripts/tactics.md` document the command and the per-class rules.

### Added: rest commands (Phase A Task A2, carried here)
- `scripts/tactics/rest.py` lands in this branch because A3 cannot exist without it: a spell slot that is only ever spent is a resource the party runs out of mid-campaign. A2's own PR (#35) is the origin of the module; this branch carries the module because it has to, and the spell-slot half of it is the part A3 owns. Changes from that version: it no longer defines its own duplicate `Stop` class (which `cli.main` did not catch, so a bad `rest` was a traceback), no longer imports `pathlib` at the bottom of the file, decodes its subprocess output as UTF-8 rather than with the locale encoding (the same class of bug `test_encoding_utf8.py` exists to catch, and it caught this one), and takes the encounter `cli.run` has already loaded rather than loading a second copy that `cli.run` would then overwrite.
- Short rest: spends Hit Dice to heal — only with `--for-me`, since a player's hit points are the player's to roll — resets the action economy, and recharges short-rest features. Long rest: full HP, temp HP cleared, half the Hit Dice rounded up, all spell slots, features recharged, exhaustion down one, frightened/charmed/poisoned ended, death saves reset, concentration ended.
- `rest` needs a running fight. A rest *between* two fights is narration rather than grid and is still the GM's to award; that is stated in the docs rather than quietly assumed.

### Added: every 5e condition is a rule the engine applies (Phase A Task A1)
- Conditions are one table now. `systems/dnd5e/tactics_rules.py` holds `CONDITION_EFFECTS` for all fourteen conditions and `EXHAUSTION_EFFECTS` for the six levels, and the five condition sets the attack and save paths used to keep by hand are derived from it. Three hand-maintained lists is how a condition ends up helping the creature it was meant to be hurting; the audit for this task found petrified missing from the auto-critical list and deafened, charmed and exhaustion missing entirely.
- `get_condition_modifiers(token)` merges a token's conditions and its exhaustion level into one dict whose keys are always present — `attack_roll`, `ability_check`, `save`, `attack_against`, `movement`, `action_economy`, plus `auto_crit_within_5`, `immunities`, `harmful_to`, `exhaustion` and the `sources` that produced them. Advantage and disadvantage from different conditions cancel; anything else stacks. New `Rules` methods `condition_modifiers`, `condition_notes`, `set_condition` and `clear_condition`, documented in `SYSTEM-PORTING.md`.
- Conditional conditions are measured instead of assumed. A value may be gated — `{"ranged": "dis"}` for prone, `{"source_in_sight": "dis"}` for frightened, `{"charmer": "dis"}` for charmed, `{"hearing": "auto_fail"}` for deafened — and the engine hands the rules the geometry (`AttackContext.source_in_sight` is new: whether whatever is frightening or charming one of them can be seen, measured from the effect that applied it). A gate that does not hold is silent rather than cancelling, so an invisible creature's melee swing keeps its advantage while prone.
- The rolled line now says why it was harder than the numbers suggest: `Kairos Fire Bolt -> Giant Frog 1: 8 vs AC 11, disadvantage, miss (disadvantage: Kairos is poisoned).` The same reasons were already in the result dict; they were just not in the text anyone reads.
- Exhaustion levels 1-6 (PHB appendix A): 1 disadvantage on ability checks, 2 speed halved, 3 disadvantage on attack rolls and saving throws, 4 hit point maximum halved, 5 speed 0, 6 death. The level lives in `extra.exhaustion_level` and the condition stays the plain word "exhaustion", so the display, the sheet write-back and every set membership keep working. Set it with `condition <token> add "exhaustion 3"` or `--level 3`; levels 4 and 6 are applied when they are set, and the line says so; `status` shows `exhaustion 3`.
- `condition add` and `condition remove` report what the condition is doing (`poisoned: disadvantage on attack rolls and ability checks`) instead of only that it was added. Fourteen conditions is more than anyone holds in their head, and a condition that is silently not applied is a condition that stops being applied halfway through a fight.
- `--adv` and `--dis` on `attack`, and on the new `check`: the GM's own ruling for that one roll, outranking every condition in both directions, with `the GM's ruling` in the reason list. A stunned creature can be handed a save, a poisoned archer can be given the shot they need, and neither is a thing the engine has to agree with.
- New `combat.py check <token> <skill|ability> [--dc N] [--adv|--dis] [--sense sight|hearing] [--by <token>] [--source <token>]`: a check rolled out of turn, costing no action, with the conditions on it — including a deafened creature failing only the checks that need hearing, which is the one way a condition that has no combat effect is still a condition.
- Fixed: a prone creature's own attack was given disadvantage on every attack, not only the ranged one; and a melee attack from beyond 5 ft on a prone target was given disadvantage where the rules give nothing (`test_opportunity_attack_preview_uses_the_real_distance` asserted 36% for a roll with nothing wrong with it — it is 60%).
- One house ruling, marked as such in the table: a charmed creature's attack rolls against its charmer are disadvantage, and it cannot willingly target the charmer. 2014 says the charmed creature cannot harm its charmer; the engine does not refuse the attack (a charm is a suggestion in play, and the GM's ruling is better than the table's), it makes the roll harder and says so.
- New `tests/test_condition_modifiers.py` (48 tests): one case per condition plus each exhaustion level, the merging rules, the gates, `attack()` and `saving_throw()` consuming the modifiers, the explicit ruling winning in both directions, the output text naming the condition, and the `condition`, `check` and `--adv`/`--dis` paths on the command line.

### Fixed: `--dice-request --wait` reported rolls that were never made
- `send.py --dice-request --wait` blocks the GM until every prescribed character has rolled, then prints `all rolls received` and exits 0. It decides that by polling `GET /dice-request/<id>` and reading `complete` — and that route answered `complete: true` for an id that was **never issued**, with exactly the body it returns for a request that genuinely finished. So the GM was told the table had answered, the call exited clean, and the next beat of the scene went ahead on rolls that did not happen. A GM agent hit this against a live display and reported it independently: `send.py --dice-request --wait reported "all rolls received" while returning {"complete":true,"pending":[],"results":[]}`.
- `GET /dice-request/<id>` now carries a `known` field that separates *this request finished* from *this id was never issued*. An unknown id — never created, or aged out of the 50-request keep-ring — is a 404, not a completed request. A real completion is unchanged: `known` and `complete` are both true, with the same `results`.
- `send.py` exits 2 on a 404 and says the display does not know the request, rather than printing a success. A server that still answers `200` with `known: false` is refused too, so an older display cannot reintroduce the false success.
- The trap worth recording: a 404 inside the poll loop raises `HTTPError`, so the existing catch-all turned it into an unbounded retry — the GM hung to the timeout instead of learning anything. It needs its own branch ahead of the generic one, and `import urllib.error`, without which that branch is a `NameError` at exactly the moment it matters.
- `display_bridge.py` reads the same route and already fails safe: the 404 surfaces as an exception and it returns `None` rather than a fabricated number, so it needed no change.
- New `tests/test_send_dice_wait.py` (7 tests) drives the real CLI against a stub display and asserts on the **exit code and message** rather than the JSON, because the exit code is what the GM acts on. `tests/test_dice_request_results.py` gains two: an unknown id and an aged-out id are no longer `complete`. All 9 fail against the unfixed code. 974 pass.
- `utf8_stdout()` now reconfigures **stderr** as well as stdout. Every diagnostic this script emits — `all rolls received`, the timeout line, the unknown-request refusal — goes to stderr, so on a cp1252 console the em dash in those messages raised `UnicodeEncodeError` and took the whole call down. The lines a GM reads to decide what happens next must not be the ones that crash. Caught by the Windows CI jobs, which is what the matrix is for.

### Fixed: the combat panel on a phone and on a table screen (Phase 4b)
The last three must-haves of the Phase 4b combat UX pass. Must-haves 1, 3 and 5 (move range on water, enemy-reach hatching, the display's own palette) shipped in `ux-4b-combat-clarity` and are already on `main`; these are the other three.
- **The board fits a phone, and the actions are a bar you cannot lose.** The panel used to be a fixed 28–40px-scaled SVG that ran off the side of a 390px screen, with the action buttons wrapping until End turn was under the fold and behind the STAGE button. The board is now sized to the width of the panel and the map's height follows from its own shape, so a 20-wide map draws 340px wide in a 352px box instead of 560px in 363px. The actions are a bar along the bottom of the panel: it scrolls inside itself with a 46dvh ceiling so a long spell list cannot grow it until the map is gone, Move / Attack / Cast are pinned to its top edge and End turn to its bottom, and it clears the iOS home indicator. Buttons are 40px tall on a phone (they were 36px everywhere).
- **Side is a shape, not a colour.** An enemy token is a notched octagon drawn as real SVG geometry over the same 2r box as the circle an ally gets, so side survives greyscale, a bad projector and a colour-blind player. The initiative strip — the one place the whole table is on screen at once, and which showed no side at all — now carries a 4px stripe on each chip's leading edge and a glyph (⚔ enemy, ♥ ally, ✦ neutral), and the words a screen reader reads name the side too. Colour is the third signal, not the only one.
- **A shared display is sized to be read from across the table.** The board was pinned to 28–40px squares in a fixed 560px box: 11px initials and 7px condition codes, unreadable past the second chair. Squares are now at least 40px on a table display (Frog Pond on a 1440-wide screen: 40px squares, an 800px board, up from 28px and 560px), which puts the token initials at about 14px and the condition badges at 10px. A map too big for its box scrolls rather than shrinking, and `Hide map` folds the panel to its header *and the initiative strip* instead of hiding everything, so a folded panel still answers "whose turn is it".
- `boardCell()`, `octagon()` and the side table were lifted into a marked, DOM-free block at the top of `tactics.js`, which is what lets the new tests run the real sizing numbers under node instead of asserting on the source text.
- Fixed a trap in `tactics.css`: the phone `@media` block sat *above* base rules of equal specificity, so its `.tx-body` and `.tx-board` overrides were silently losing to the ones further down the sheet. The phone block now comes last, and `tests/test_display_tactics_ui.py` fails if a base rule is ever added after it. The pinned End turn paints its backdrop with a spread shadow rather than a `background`, because a rule specific enough to pin it also outweighs `.tx-btn.tx-primary` and would have repainted the accent button in the panel's own colour.
- New `tests/test_display_tactics_ui.py` (19 tests): the cell rule at every map the display ships and at both screen classes, the octagon's geometry, and the script and the stylesheet checked against each other (a class the script sets must exist in the CSS, a side stripe must be a border and not only a colour). New `tests/test_display_tactics_layout.py` (8 tests) drives `display/evidence-panel.html` in headless Chromium at 1440x900 and 390x844 and asserts what the browser actually laid out; both test modules skip themselves if node or Playwright is missing.
- New `display/evidence-panel.html` and `scripts/phase4b_viewport_evidence.py`: the panel on Frog Pond with a snapshot, and the measurement behind the numbers above, for checking the panel at real viewport sizes without a running encounter.

### Added: encounter design — budget and rate (Phase 5)
- `combat.py budget --party auto`: the difficulty thresholds a party can actually be handed — per character and for the party as a whole, from the levels on their sheets, with the monster-count multiplier printed. `--party "Kairos,Vesper"` picks the party; a mixed-level party is measured at its average and the output says so.
- `combat.py rate --monsters "goblin x4, hobgoblin"`: what a list of monsters costs that party, with the whole calculation shown — each monster's CR and XP, the raw total, the multiplier, the per-character share, the thresholds it was measured against. `x4`, `×4` and `*4` all work; a missing count means one and `x0` is refused. A name that is not in the SRD comes back with near matches, never a traceback.
- Both take `--ruleset 2014|2024` and default to the campaign's own `**System Version:**` in `state.md`. 2014 is the DMG threshold table plus the monster-count multiplier; 2024 is the per-character XP budget by tier with **no** multiplier, and its own Moderate column — not 2014's Medium with a new name. A campaign on any other system version is told so rather than quietly rated under 2014. Below the first threshold the verdict is `TRIVIAL`, not "Easy": "Easy" is the word a GM repeats to themselves when they stop preparing for a fight.
- Neither command needs a combat running and neither rolls a die; both are read-only and write nothing.
- `end` now rates the fight that just ran with the same tables, awards the XP to the party still standing (a PC who went down gets nothing), and records it in the campaign's existing `xp-ledger.jsonl` — the one `xp.py award` writes, so `xp.py check` reconciles both. `end --no-xp` skips it. A sheet with no numeric XP field (`**XP:** 0 (milestone levelling)`) is reported as untracked instead of being written into, so the ledger never claims an award no sheet reflects. The award reads the CR and XP the tokens were built with rather than looking the monsters up again.
- The tables have one home: the 2014 thresholds, CR values and multiplier are imported from `systems/dnd5e/xp.py`; the 2024 budget lives in the new `systems/dnd5e/encounter.py`. Four methods were added to the `Rules` interface (`encounter_budget`, `rate_encounter`, `award_xp`, `record_awards`), and `xp.py` grew `award_xp`/`record_awards` helpers that resolve the campaign directory the engine already found.
- New `tests/test_phase5_encounter_design.py` (30 tests): both rulesets, the multiplier table at every band boundary (1/2/3/6/7/10/11/14/15/20 monsters), the shorthand including `x4`/`×4`/`x0` and multi-word names, unknown monsters with and without near matches, golden output for `budget` and `rate` in both rulesets, and the `end` award (ledger row, `--no-xp`, a downed PC, a party that lost one, a milestone-levelling sheet).
### Fixed: the SRD dataset was 2014 spells beside 2024 class features
- The engine has always been 2014 (`tactics_rules.py` cites the 2014 PHB and SRD 5.1), but `build_srd.py` read Foundry VTT's **2024** packs for class features and race features while reading 5e-bits' **2014** tree for spells, monsters and items. The dataset was a hybrid: `lookup.py feature sneak-attack` could answer a 2014 rules question with 2024 text, and 2024 subclass features (Overload, Harvest Oids, Fount of Moonlight) sat in a 2014 campaign's reference data.
- `foundryvtt/dnd5e` ships both editions in one repository. The build now reads the 2014 packs only (`classes`, `classfeatures`, `races`), pinned as constants (`FVTT_CLASS_PACK`, `FVTT_CLASS_FEATURES`, `FVTT_RACES`) instead of being matched inside a path loop. The editions nest their features differently, so the pack names now decide the edition in one place.
- The partition is extracted as a pure `_partition_fvtt_tree`, so `tests/test_srd_sources.py` can assert the edition against a fixture tree holding real paths from both. New tests also refuse any `classes24`/`spells24`/`origins24`/`feats24` reference in the builder. Without these the swap was invisible: the build succeeded and the dataset was well formed.
- Rebuilt dataset: 1527 records, 260 class and race features, all 12 core classes, no 2024 markers. Verified through lookup: Sneak Attack reads as 2014 (1d6 at 1st level), with Indomitable Might, Arcane Recovery, Eldritch Invocations, Metamagic and Sculpt Spells present.
- Fixed while in there: `classfeatures/grappler.yml` is a class document loose in the features pack and was indexed as a feature called "Grappler"; a depth floor excludes it. Extra Attack and Ability Score Improvement sit one folder shallower and are now tested explicitly, because a floor set too deep silently drops them. Features shared by every class (Extra Attack, the fighting styles, ASI) carry an empty class instead of the string `shared-features`, and race features `""` instead of `None`, which `lookup.py` was printing as the literal word "None" in the header.
- `systems/dnd5e/system.md` no longer documents a `2024` ruleset, or `build_srd.py --version 2024` / `lookup.py --version`. Neither flag has ever existed, so the table advertised a switch the code did not have. It now says plainly that the module is 2014 only, and what 2024 would actually require. **System Version:** does select the encounter XP budget table for `budget` / `rate` (Phase 5, tabulated for both editions because that is a table lookup, not a rules engine), but it does not select combat rules, so `system.md` now draws that line instead of calling the field a label nothing reads. `NOTICE` drops the SRD 5.2 grant, which no longer applies, and flags that the race list is wider than the SRD 5.1 subset so a redistributor checks their own obligations.

### Added: off-screen faction clocks (Phase 8)
- `scripts/world.py` is a real tool now, not a stub: factions with a goal and a 4/6/8-segment clock in `<campaign>/factions.json`, one hidden d6 per faction per day (1–3 nothing, 4–5 one segment, 6 two). `calendar.py advance <n> days|weeks` calls the tick for you; hours and rests do not. Everything it prints is GM-only and never reaches the player display.
- New subcommands: `lean` (a one-shot ±1/±2 nudge to the next tick, spent by it), `complete` (acknowledge a clock that fired — it now stays full and stops ticking until you do, instead of silently resetting to 0 mid-loop), `set-interval day|week`, and a `clear` that lists what it would drop and refuses without `--yes`. `status` shows `ACTIVE`/`HELD`/`FIRED` and pending leans; `clock` refuses a move beyond ±3 and un-fires a clock the party pre-empted.
- `--seed N` replays a tick exactly. Faction history is stamped with the in-world date from `calendar.json`, and a clock added today cannot fill today — the party always gets a tick interval of warning.
- Fixed: campaign paths came from a private `OPENTTG_CAMPAIGNS_DIR`, so clocks were written to a second, invisible copy of the campaign instead of the one `paths.py` resolves. Three days of travel rolled a single die instead of three, the party-interference modifier was re-read from history on every tick (so one `-2` depressed a faction forever), a completed clock reset itself to 0 and could fire repeatedly in one tick, roll values printed as a function's repr, an unknown faction exited 0, and a corrupt `factions.json` was never tested.
- `world.py` writes neither `state.md` nor `graph.json` — `/gm factions` in SKILL-commands.md and the `/gm save` branch now spell out the one session-end pass that reconciles a fired clock with `## Faction Moves` and the graph.
- New `tests/test_world_clocks.py` (26 tests) covering per-day rolls, seeded replay, the one-shot lean, hold/fire/complete, clamping, legacy `factions.json`, the calendar integration, and the error exits.

### Added: the encounter file has a schema
- `scripts/tactics/schemas.py` describes every value in `combat/encounter.json` with a field type (`NumberField`, `StringField`, `BooleanField`, `SetField`, `ListField`, `DictField`, `SchemaField`, `FormulaField`, `OptionalField`, `AnyField`). `Token.from_dict` coerces and validates on load and `Token.to_dict` validates on save, so a value that does not fit is refused at the file boundary with the field named, instead of surfacing as a `TypeError` twenty commands into a session.
- `SCHEMA_VERSION` is 2, with a migration chain (`migrate_v1_to_v2`) that runs on load. A v1 fight upgrades itself in memory and is written back as v2 by the next save; a file from a newer engine is refused rather than down-converted, and reading a fight never rewrites it.
- `Token.prepare_derived()` reports initiative modifier, saves, skills and max HP. It only ever fills a value the sheet or SRD did not supply: a number from a character sheet or stat block is authoritative, `max_hp` is only ever estimated (and labelled) for a token that never had one, and derived stats ignore conditions so they cannot flip value mid-fight.
- `FormulaField` evaluates an expression over a closed set of names by walking its AST. No `eval`: a formula read out of a sheet is untrusted text.

### Fixed: SRD build reads the live upstream
- `5e-bits/5e-database` is archived. `build_srd.py` and `sync_srd.py` now read `5e-bits/5e-srd-api` (`packages/5e-database`), so `sync_srd.py` can see new upstream commits again. The staleness check filters commits to that package path.

### Added: SRD attribution
- `systems/dnd5e/NOTICE` carries the CC-BY-4.0 notices for SRD 5.1 and 5.2 (and names the OGL 1.0a route for the 5e-bits data); the README links to it.

### Added: continue a campaign in one step
- `bash display/start-display.sh --campaign NAME`: starts the display for that campaign and runs `display/preflight.py`, which stops on an unknown campaign name (before touching a running display), warns when the 5e SRD data is missing or was built by an older `build_srd.py`, and says when a grid fight is waiting to resume.

### Fixed
- An action typed in the display's Party input (Stage, then Ready) never reached the GM in a normal session: it went to `.input_queue`, which only `wrapper.py` and autorun read. `check_input.py` now reads it too, once, and clears the "Queued" badge.
- `send.py --dice-request ... --wait` said "all rolls received" but never printed the roll, so the GM could not resolve the check. It now prints each roll (`Kairos rolls 1d20+1: [8] +1 = 9 ...`).
- `/gm new` left the previous campaign's story and party on the display. It now points the display at the new campaign and clears it. The display also no longer erases narration that arrives just after a clear.
- A second display started for a test or demo (`GM_DISPLAY_PORT=... python3 display/gm-display-app.py`) took over `display/.port`, so the live session's scripts talked to the test display. Only `start-display.sh` writes it now.
- `tests/test_milestone_counter.py` wrote its test player (Aldric) into the real `display/stats.json`.
- The scene name read "The Temple" at an inn: "lantern" was a temple word, and scene keywords matched inside other words ("ale" in "pale", "inn" in "dinner"). Keywords now match whole words, with or without a common ending.
- The "Waiting on" dice badge sat over the middle of the story. It now sits under the scene name, and the story moves down while it shows. With the settings column collapsed, the story no longer runs under Phone Mode and the Settings toggle.
- Phone Mode's Roll tab kept the previous roll's result under a new dice request.
- `send.py --dice-request ... --wait` said "all rolls received" when the request was cancelled. It now says it was cancelled, prints the rolls made before, and exits 2.
- `start-display.sh --campaign NAME` said "No campaign" for a campaign still in the old `~/open-tabletop-gm/campaigns/` folder, which `/gm load` finds.
- The display writes `.input_queue` to a temp file and renames it, so `check_input.py` can never take a half-written action.
- `docs/FIRST-SESSION.md`: how to start the display and a new campaign, and how to check the display without a GM.
- A spell cast from the grid (Cast menu, or `cast`) used a 5 ft range when the SRD data was built by an older `build_srd.py`: Fire Bolt could not reach anything. Stale spell records, including ones cached in an encounter, are now looked up again.
- `/combat/state` sent the fog of war as one square label per visible square, about 1800 bytes of JSON on every poll. It is now run-length encoded: `fog.runs` is a list of `[row, first column, last column]`, plus `fog.count`. The display shades the same squares from 212 bytes.
- The battle display never showed Armor Class anywhere, although `/combat/state` carries it. The turn strip now has an `AC n` chip and board tokens carry AC in their title and aria-label.
- A roll or reaction the engine waits on (`--roll` / `--react`) never reached the display: spectators saw only "Kairos's turn" with no idea a roll was awaited. A paused command now pushes a snapshot with `turn.pending` (`roll:1d20+6`, `react:kairos:silvery barbs`), and the side panel shows a waiting line. The encounter file is not saved mid-pause.
- Spell slots were visible only inside the Cast submenu. The turn strip now shows pips per level (`1st ●● ○`).
- The dice toast hid the natural roll (`Bite 1d20+3 = 19`). d20 toasts now show it (`Bite 1d20+3 [16] = 19`), with CRIT / fumble flags on natural 20 / 1.
- Shared-screen type scale: turn strip 12→13px, side info 13→14px, spell meta 11→12px, combat log 12→13px, legend 11→12px, dice toast 13→14px.

### Fixed: the display starter only kills the display on its own port
- `display/start-display.sh` used `pkill -9 -f "gm-display-app.py"`, which SIGKilled a live `:5055` test display twice mid-fight. The PID file is now per port (`app-$PORT.pid`), only that PID is signalled, and only after its command line still matches the display server. No `pkill` remains.

### Fixed: advantage prompts say to roll two dice and report the kept face
- With `advantage_next` (Silvery Barbs) the manual `--roll` prompt asked for a single number with no mention of advantage. The design is honor-system (the player's kept face is one value, the flag recorded — verified end to end, and pinned by test), so the CLI now asks for the kept face explicitly (`--roll <kept d20 face: roll 2d20, keep highest>`) and `play.py` says which face to report.

### Fixed: attack prose no longer verbs the weapon
- `"the Bite bites into Kairos"` is now `"lands the Bite on Kairos"`; `"glances off Kairos's guard"` is now `"The Bite glances off Kairos"`.

### Fixed: an explicit weapon choice is honored instead of silently substituted
- `"I strike the frog with my quarterstaff"` (a weapon Kairos does not have) used to resolve silently to Fire Bolt. The autopilot now asks which attack to use instead (`"Kairos has no quarterstaff — Fire Bolt or Dagger?"`), and `"no spells"` picks the first mundane attack (Dagger), never a cantrip.

### Added: advisors see the active fight
- An advisor consulted mid-fight invented mechanics (a 15 ft frog-tongue reach vs the actual threat 5) because consult context carried no fight state. `/advise` context now ends with a compact `## Active fight` block (round, turn, HP per token) from the bridge snapshot, and the shared advisor rules require citing numbers from context instead of inventing them.

### Added: a playable tutorial for grid combat
- `scripts/tactics/play.py`: play a grid fight yourself in the terminal. You type your turns (`move D4`, `attack 1`, `cast magic missile 1 1 2`); the game runs the enemies as the GM would. An ASCII battle map with numbered enemies, a `reach` overlay, dice you roll yourself (or Enter to roll for you), and y/n prompts for Shield, Silvery Barbs and opportunity attacks.
- `play.py tutorial`: ten lessons in the new Training Yard map (`display/maps/training-yard.json`) against two kobolds, then free play. Also `kobolds`, `frogs`, `mephit`, or any map and SRD monsters with `--map`; `--sheet` plays your own character; `--display` mirrors the map to the browser.
- `docs/TUTORIAL.md`: the player's guide. `tests/test_tactics_play.py` plays whole fights with a scripted player.

### Added: grid combat milestone 4, spells and templates
- Areas of effect on the grid (sphere, cylinder, cone, line, cube): a square is caught if its centre is inside the shape; walls block the area.
- `cast`: spell attacks, saves (one damage roll per area, a save per creature, cover on DEX saves), Magic Missile darts, healing, Mage Armor; spells the engine cannot run still spend their slot and are narrated. The bonus-action spell rule and slot levels are enforced; nothing is spent on a refused cast.
- `preview-area`: every creature a spell would catch, its chance to fail and the expected damage, allies flagged. `spells` lists what can be cast and why not.
- Concentration (a CON save per damage instance, one spell at a time, effects end with it), timed effects (Mind Sliver's penalty, Shield until the caster's next turn), repeat saves at the end of a turn.
- Reactions: Shield and Silvery Barbs, offered only when they would change the outcome; per-token `reactions ask|auto|off`. A paused command replays the same enemy dice when re-run.
- Structured monster riders: grapples with an escape DC (and restrained while grappled), save or be knocked prone, save for poison damage, conditions with a repeat save. Qualified riders stay text for the GM.
- Help, Hide (Stealth vs passive Perception, needs total cover), Escape, Ready and `trigger`.
- Enemy options include breath weapons and other area save actions, aimed and scored by save chance; recharge and per-day use are tracked.
- SRD build: spell mechanics, monster save proficiencies, skills and passive Perception.
- `demo.py --scenario mephit`: Kairos against an ice mephit.

### Fixed
- `scripts/world.py` read and wrote its files without `encoding="utf-8"`.

## [0.15.0] — 2026-09-16 — Creature defenses, narration badges, and a working SRD build

Ten fixes in one release, so an existing install updates once and receives all
of them. The SRD dataset builds again from a fresh clone. Creatures carry their
resistances and immunities into play. Narration blocks are marked with a scene
badge. XP awards are recorded and can be reconciled against a character sheet.
A dice result no longer cuts off the sentence you were reading, and the reading
column grows with the text size instead of narrowing. The test suite runs in CI
for the first time.

Several of these existed in the source already and had never reached a player.
Where that is the case the entry says so, and the commit history has the full
account.

### Fixed — the SRD dataset could not be built at all

- **Every SRD source returned 404.** `build_srd.py` fetched from
  `5e-bits/5e-database/main/src/2014/`; upstream added a language directory and
  the files are at `src/2014/en/` now. Because the generated dataset is
  gitignored, an existing checkout kept running on data built before the move
  and nothing degraded — it broke only a fresh clone, which is the case nobody
  runs. Verified against a control (the repo's README returned 200, so the
  404s were the path and not the network). The corrected path builds 1,267
  records: 319 spells, 237 equipment, 362 magic items, 15 conditions, 334
  monsters. (#42)
- **A build that fetched nothing reported success.** A failed fetch printed to
  stderr, became an empty list, and the build carried on and wrote a dataset
  containing nothing over a good one. Fetch failure and "fetched, and it was
  empty" are now different values, and the writer refuses to overwrite when
  every category came back empty. (#42)

### Added

- **Creature defenses.** Damage resistances, immunities, vulnerabilities and
  condition immunities were in the SRD all along and were dropped by the
  normaliser, so nothing could show them and the GM had no record to consult.
  They are now kept and shown in the creature block — immunities on 136 of 334
  creatures, condition immunities on 92, resistances on 70, vulnerabilities on
  15. Nothing applies them to damage automatically; the GM reads and
  adjudicates, as before. (#43)
- **An XP award ledger.** An award left no trace except a number on a sheet, so
  an award that never happened was invisible until a player noticed weeks later
  that their total had not moved. `campaigns/<name>/xp-ledger.jsonl` records
  each one, and `xp.py check --campaign <name>` reconciles it against the
  sheets and exits non-zero when a sheet holds less than the ledger recorded.
  It detects gaps; it does not fill them. (#46)

### Fixed — features that existed and never ran

- **Narration block badges.** A keyword table, a function that built the image,
  and a CSS class — and the function was never called from anywhere while the
  class had no style rule at all. Nobody had ever seen a badge. It is now wired
  and styled, and it no longer depends on English: block KIND (NPC, dice,
  tutor) is badged with no word list, so that path works in every language, and
  the semantic word list moved into the system's `ui.json` where a campaign in
  another language can override it. (#45)
- **A block arriving mid-narration no longer dumps the paragraph.** An NPC
  line, dice result or tutor note used to snap the rest of the prose to its end
  so it could land underneath — the reveal thrown away to deliver the thing
  that usually explains the sentence you were reading. Blocks are now held
  until the typewriter genuinely drains, with a re-arming check so continuing
  narration does not get cut mid-paragraph, and an 8-second valve so a stalled
  reveal can never swallow a dice roll the table is waiting on. (#41)
- **Text Size scaled the type but not the column.** `#text-content` held a
  fixed 820px measure while the control multiplies the font up to 2.0, so the
  largest setting gave roughly half the words per line — a ribbon, chosen by
  the person who found the text hard to read. The measure now scales with the
  same variable, clamped against the viewport.

  Measured across window widths, in characters per line:

  | viewport | 1.0 | 1.4 | 2.0 |
  |---|---|---|---|
  | 1440px | 82 | 62 | 44 |
  | 1920px | 82 | 82 | 67 |
  | 2560px | 82 | 82 | 82 |

  The measure is fully preserved where there is room for it. On a 1440px
  window it is not: the sidebar and settings gutters reserve 570px between
  them, so the column cannot exceed 870px no matter what the setting says, and
  the type keeps growing past the point the line can hold it. Collapsing either
  column gives the space back. Fixing that properly means the gutters shrinking
  with the scale, which is a layout change rather than a one-line one. (#44)

### Fixed — non-English installs

- **93 text-I/O call sites still took the locale encoding.** The 0.14.1 sweep
  fixed bare `open()` and added a guard; the guard measured that one property
  and `read_text`, `write_text` and every `subprocess` call that decodes child
  output were never checked, with `probe/` excluded outright. Run under a
  non-UTF-8 locale the suite went 15 failed / 12 errors before this and passes
  after. Adds `scripts/utf8io.py` for reading a file a pre-sweep install wrote
  in a legacy codepage, which refuses rather than returning replacement
  characters a write-back would make permanent. (#38)

### Fixed — XP

- **An award against a sheet with no XP field was discarded in silence.**
  `re.sub` was written back without checking whether it matched, so a
  mismatched sheet kept its old XP with no error. Worse, the pattern required
  digits before the slash while a fresh template sheet holds `**XP:** / 2700` —
  so the first award against a new character was exactly that case. Now uses
  `re.subn` and warns when the field is genuinely missing. (#40)

### Changed

- **The "not in the dataset" link is no longer a guess.** It slugified a name
  and constructed a URL that nothing checked, so a name that did not match the
  target site's convention dead-ended, and an unknown category degraded to a
  bare slug at the site root. Constructed links now point at an SRD reference
  verified per category against a control, and it is the same SRD content
  without the ads. A supplemental record still links to the page it was fetched
  from, because that is where non-SRD content lives. A category with no
  verified mapping now produces no link at all. (#47)

### Internal

- **CI exists.** There was no `.github/workflows/` directory; 9 test files and
  nothing ran them. Adds a matrix across ubuntu/macos/windows on Python 3.10
  and 3.13, plus a non-UTF-8 locale job — with `PYTHONCOERCECLOCALE=0` and an
  assertion that the locale really is non-UTF-8, because PEP 538 coerces C to
  C.UTF-8 on Linux and would otherwise make that job pass without testing
  anything. Actions pinned by SHA. (#39)
- 146 tests, up from 105, all passing under both a UTF-8 and an ASCII locale.
  Every new guard was break-tested and fails for its own reason.


## [0.14.1] — 2026-08-23 — Non-English display fixes

Two bugs reported from a Russian-language install, both of which only appear outside an English, UTF-8 environment and neither of which fails loudly enough to notice from the developer's side.

### Fixed

- **Campaign text no longer corrupted on a non-English Windows install.** An in-world date was rendering as `18 РЎРµСЂРїР°РЅСЊ, 412 РѕС‚ РџР°РґРµРЅРёСЏ РЎРІРѕРґРѕРІ` instead of `18 Серпань, 412 от Падения Сводов` — UTF-8 bytes decoded as cp1251, the Windows ANSI code page for Russian. Python's `open()` falls back to the locale encoding when none is given, so every bare `open()` was a corruption site: 67 of them across 15 files. All now pin `encoding="utf-8"`, `start-display.sh` exports `PYTHONUTF8=1` so the *default* is UTF-8 rather than only the call sites that exist today, and `scripts/paths.py` reconfigures the standard streams so printing non-ASCII to a cp1251 or GBK console does not raise. The same bug reaches Chinese users as GBK/cp936. (#36)
- **Display icons no longer 404.** This fork carried `display/templates/` without `display/icons/` or the Flask route that serves it, so all ~20 icon references in the UI — class badges, dice and block badges, the corner logo, the app icons — returned 404 and rendered as blank boxes. The 39-icon set and the `/icons/` and `/favicon.ico` routes are now in place. (#37)

### Internal

- `tests/test_encoding_utf8.py` and `tests/test_display_icons.py` are the detectors that were missing for both bugs. The encoding guard works two ways, since neither is sufficient alone: Python's own `-X warn_default_encoding` catches `read_text`/`write_text` as well as `open()` but only on code that runs, and an AST walk covers the paths no test executes. The icon guard reads names out of the template the way the browser does, including the three tables the JS assembles names from at runtime — the ones a search for a literal path never finds. Both were break-tested in each direction.


## [0.14.0] — 2026-07-24 — GM-side discipline sync from claude-dnd-skill v2.4.0

Ports the GM-discipline and quality-of-life work from claude-dnd-skill **v2.4.0**, generalized to OTGM's system-agnostic core where the upstream version was D&D-flavored. What's genuinely 5e-only stays in `systems/dnd5e/`; mechanical rendering is driven by the system's UI manifest, not a hardcoded list.

### GM discipline (core — SKILL.md)

- **New Standard 13 — Never Play the Player's Side.** A hard constraint separating the GM's authority from the player's: never speak a PC's dialogue, narrate their private thoughts, or decide their actions; adjudicate each *declared* action on its own terms and let it resolve rather than skipping it, swapping it, or narrating past it; and treat the party as exactly the named player characters, never inventing a companion or putting words in a real player's mouth. Faster/weaker models drift into acting for the player without an explicit rule — this is that rule. (Numbered 13 in OTGM, which does not carry the upstream "Open Each Scene With a Bang" standard.)
- **Table Dials — optional per-campaign tuning.** Three neutral-by-default settings in `state.md → ## Session Flags`: `difficulty` (stakes/lethality only — Standard 7 still governs rolls), `spotlight` (how much the GM drives vs. follows), and `pacing` (pressure vs. room for character scenes). An unset dial changes nothing; a set dial is honored every turn like `## GM Style Notes`.
- **Narration hygiene.** Standard 4 now states plainly that narration is prose for the table, never a document — no markdown headings or bulleted lists inside the fiction, opening scenes included.
- **Bold-play reward backstop (core hook + 5e binding).** Standard 12's generalized reward hook now says: don't rely on remembering — where the system module defines a hard, table-visible trigger, honor it every time. The concrete 5e binding lives in `systems/dnd5e/system.md → ## Bold Play Reward`: a natural 20 on any d20 test (or a nat-20 death-save stabilize) awards Inspiration on the spot unless the character already holds it.

### Continuity & memory (core)

- **Pinned Facts — the memory the GM always keeps.** A new `## Pinned Facts` section in `state.md`, read at every `/gm load` alongside `## GM Style Notes` and kept hot all session. It holds the small, stable set of soft facts a table never wants forgotten (a promise made, a dead sibling's name, a house rule, an in-joke). New command **`/gm pin`** adds/lists/removes them; unlike Live State Flags they are never rewritten wholesale at save.
- **Keep the world clock honest.** SKILL-scripts.md now states the clock is continuity, not decoration: narrate consistently with the last pushed time, advance it deliberately by how much an action costs, and reconcile a drift with a fresh `--world-time` push — including *backward* to the correct time, which undoes nothing since timed effects run on their own tracker durations.
- **Firmer arc-advance discipline + stale-pointer nudge.** `/gm arc advance` now states advancing the pointer is not optional bookkeeping (clearing the current beat's outstanding items, or the party moving into the next beat/chapter's situation, *is* an advance). `/gm load` now sanity-checks a stale pointer (current beat looks finished but never advanced) and offers to move it instead of opening another scene in a done beat.

### Rules lookup & character creation

- **Rules lookup no longer dead-ends on a typo.** `systems/dnd5e/lookup.py` gains a `suggest()` near-miss pass: when an exact/substring lookup misses, it fuzzy-matches the query against real dataset names and offers the closest ones (`poisonned` → Poisoned, `fireballl` → Fireball). The CLI prints a `Did you mean: …?` line; the display's lookup modal renders the misses as tappable chips that re-run the lookup. Suggestions honor the category when given and search all categories otherwise. Zero model calls — reads only the bundled dataset.
- **Describe-it character creation (core flow, system-deferred legality).** `/gm character new` now opens with a build-path choice: the deliberate `Step by step` flow, or a new `Describe it` path where the player says who the character is in a sentence or two and the GM assembles a legal, level-appropriate sheet from it. The prose→concept flow and the single confirm-or-adjust pass are core; the sheet build and legality validation defer to the active system module (`systems/<system>/system.md`) and its lookup. Both paths converge into the same name-check, calc, and write steps.

### Relationship graph & display

- **Typed party disposition in the relationship graph.** `gm_graph.py` gains `set-disposition --to <npc-or-faction> --level <allied|friendly|neutral|suspicious|hostile>` — the party's stance toward an NPC (a `disposition` edge) or faction (a `standing` edge) on the same five-point scale the display's faction panel uses. Edges run from a shared, auto-created `party` node; setting a new stance closes the prior one at that session (arc stays queryable) and only the current stance surfaces in `scene-context`, rendered inline (`The Party --[disposition:suspicious]--> Aldric`). The `/gm save` sweep proposes these alongside NPC↔NPC edges. Covered by tests.
- **Display: "New ↓" pill when you've scrolled up.** Fresh narration arriving while you're scrolled up now surfaces a small tap-to-jump pill at the bottom of the reading column, instead of landing silently below the fold. It retires itself the moment you catch up.
- **Display: tappable conditions (manifest-driven).** Condition pills are now tappable — tap Poisoned, Prone, etc. to pull the rule text in the lookup modal. The tap is wired by a `srd_lookup` / `lookup_category` flag on the system's `tag_list` widget in `ui.json`, not a hardcoded condition list, so a system without a rules dataset simply omits the flag. A name with a suffix like "Exhaustion (2)" resolves to the base condition.
- **Display: settings column no longer overlaps the narration, and folds away.** The reading column now reserves guaranteed right clearance (`max(340px, 18vw)`), and a new `Hide ▶ / ◀ Settings` toggle collapses the settings column (mirroring the left sidebar toggle) so the narration reclaims the space. The choice persists per browser.

### Cleared remaining Claude-coupling leftovers

- **`wrapper.py` wraps any agent**, not just Claude — set `GM_AGENT_CMD` (default `claude`) to wrap `opencode`, `gemini`, etc. The PTY wrapper is a legacy/optional path anyway (the canonical setup runs the agent directly + `send.py`).
- **The phone `/character` route resolves via `paths.py`** (honors `GM_CAMPAIGN_ROOT`) instead of `DND_CAMPAIGN_ROOT` + a hardcoded `~/.claude/dnd` root; the legacy `~/.claude/dnd/characters/` path is kept as a final fallback for older installs.
- Cosmetic: "the DM (Claude)" → "the GM agent" in a couple of script docstrings.

## [0.13.0] — 2026-06-27 — System-agnostic character UI + model-agnostic GM hint

### System-agnostic character UI (systems can define their own sidebar + sheet)

- **The character sidebar and sheet are now driven by a per-system UI manifest** instead of a hardcoded D&D layout. A system ships `systems/<name>/ui.json` describing its sidebar widgets, sheet combat strip, and attribute grid; the display renders from it. A new system becomes a ~40-line JSON file rather than new front-end code. See `systems/UI-MANIFEST.md` for the widget catalog and a Shadowrun 5e example, and `systems/dnd5e/ui.json` for the reference manifest.
- **Backward compatible.** The renderer carries a built-in default manifest that reproduces the original D&D 5e display exactly, so campaigns with no `ui.json` (or no system declared) look identical to before. The attribute grid now also supports raw ratings (`show_modifier: false`) for dice-pool systems, not just D&D's score+modifier.
- A campaign selects its system module with a `**System Module:** <name>` line in `state.md` (distinct from the human-readable `**System:**` label); absent ⇒ `dnd5e`. New `paths.campaign_system()` resolves it. Switching systems takes effect on the next display load.

### Model-agnostic GM hint (thanks @eviloverclaude)

- **The ◈ GM Help hint no longer depends on Claude.** `dm_help.py` previously shelled out to a hardcoded `claude -p --model claude-sonnet-4-6`, so the feature silently produced nothing for anyone running OTGM through opencode, gemini, mistral, or any non-Claude tooling. It now resolves a backend portably: set `OTGM_HINT_CMD` to your own model command (prompt on stdin, hint on stdout), or let OTGM auto-detect a known CLI on `PATH` (`claude`, `opencode`, `gemini`, `llm`). `OTGM_HINT_MODEL` pins a model for the auto-detected backend; `OTGM_HINT_TIMEOUT` bounds the call. Claude still works out of the box but is no longer a dependency, and with no backend available the feature no-ops instead of breaking play.
- **Fixed `push_stats.py` reading the display token from a hardcoded `~/.claude/skills/dnd/display/.token`** instead of its own display directory like every sibling script. This broke stat pushes for any install outside the Claude skill path.

### Display input fixes (synced from claude-dnd-skill)

- **The PARTY INPUT box no longer covers the bottom of the narration.** The fixed input panel grows when it expands (or the editorial drawer opens, or the mobile keyboard raises the viewport); a `ResizeObserver` now keeps `#text-scroll`'s bottom padding synced to the panel's live on-screen footprint so the last lines of narration always clear it. No-op in phone input-only mode.
- **A failed player-input submit now says so.** If the browser→server POST fails after retries, the Stage button shows "Send failed — tap to retry" (the typed text and its `localStorage` cache are preserved, so a tap re-sends) instead of silently resetting to "Stage" — which previously read as "submitted but not acknowledged."

### Sync from claude-dnd-skill v2.1.x — backend + GM-side discipline

Ports the system-agnostic portions of the v2.1.0–v2.1.4 upstream lineup. Backend infrastructure and GM-side docs land here; the deeper phone UX bits (on-screen dice drawer for no-phones games, per-PC Rolls toggle in phone Settings, status-strip rewrite for one-tap send) are deferred to a follow-up PR to be adapted properly against OTGM's tab-based phone UX.

- **`roll_mode` session flag** — campaigns now declare in `state.md → ## Session Flags` whether players roll their own PCs (default — GM waits for the result) or the GM rolls everything openly. `/gm new` asks at session 0; `/gm load` prompts once on legacy campaigns missing the field. SKILL.md → Dice convention rewritten with the new semantics, including the per-character override path via the phone Settings → Rolls toggle (surfaced to the GM as a `[[<Char> roll mode: …]]` directive prepended by `check_input.py`).
- **Narration length directive** — display companion now exposes a Narration slider (250–2500 words). Setting it POSTs to `/narration-pref`; `check_input.py` prepends a `[[Narration length for this turn: aim for ~N words…]]` directive to the queued action so the GM honors the table's pacing this turn. SKILL.md documents how to read and obey the directive.
- **TCC-safe autorun** — `display/autorun-wait.sh` replaced by `display/autorun_wait.py`. macOS TCC blocks shell-level file creation under `~/Documents`, which broke autorun mode for anyone running OTGM out of that path. Pure-python rewrite handles session invalidation, countdown broadcast, queue poll, and `/queue/consumed` POST with identical semantics. Old `.sh` deleted.
- **Phone-aware dice routing infrastructure** — `gm-display-app.py` now tracks which character each SSE client is bound to (`_client_chars`) via the new `?character=<name>` stream query param, and exposes `_phone_present()` so `dice_request` payloads include `onscreen_targets` (characters with no live phone). The follow-up UI PR will use this to auto-open the on-screen dice drawer for those characters; the field is benign without UI support.
- **`GM_REQUIRE_APPROVAL` env var** — device approval gate now defaults to **off** (auto-trust any reachable device). The approve/deny friction made every casual home-LAN game feel like a security checkpoint. Set `GM_REQUIRE_APPROVAL=1` to restore the gate on untrusted networks.
- **Reading-text-size stepper** — display companion gains a Text Size control in audio-controls (A− / A+ / click % to reset). Multiplies `--text-scale` on `#text-content`, persisted to `localStorage["gm-text-scale"]`. Anti-FOUC read in the top-of-document inline script. Font-size, not page zoom — keeps layout intact at scale.
- **Display README** — component table updated for `gm-display-app.py` + `autorun_wait.py`, Player input panel section rewritten to document the `GM_REQUIRE_APPROVAL` default and the phone Settings (Text Size + Narration; Rolls toggle pending in the follow-up PR).
- **License formalized as AGPL-3.0-or-later.** Added canonical `LICENSE` file with `Copyright (c) 2026 Neural Initiative LLC` and a `CONTRIBUTING.md` documenting the contribution licensing handshake. The README now includes a proper License section. Self-hosting and modification remain explicitly welcome; AGPL protects against closed-source SaaS forks.

## [0.12.0] — 2026-05-31 — Phone companion + theme picker (sync from claude-dnd-skill v1.10.0..v1.12.1)

Four upstream PRs land in one bundled sync: phone dice companion (#38), mode switcher + dice-pending badge XSS hardening (#39, v1.11.0), light/dark/auto theme picker (#40, v1.12.0), and audio-controls legibility fixes (#41, v1.12.1). All adapted to open-tabletop-gm conventions — GM terminology, relative-path scripts, `gm_*` localStorage keys (the per-browser TTS / phone / theme preferences), `GM_TTS_KEY` env var, key file at `~/.config/open-tabletop-gm/tts.key`. The `dnd-token` meta tag and the `dnd_device_id` localStorage key stay as-is in this sync — those are fossils preserved across all otgm syncs to avoid invalidating existing approved-device bindings on operator machines.

### Phone dice companion (upstream PR #38, contributed by Ethros)

Adds a phone-side player companion to the existing Flask + SSE display:

- `?view=input&char=<Name>` URL bindings (one phone per character).
- Three-tab phone layout: **Move** (action input) / **Roll** (server-side dice) / **Sheet** (read-only character sheet rendered from the campaign's markdown).
- `POST /player-input/dice` rolls server-side with `secrets.randbelow`; slot-machine reveal locks on the authoritative value.
- `POST /dice-request` lets the GM (or LLM tooling) prescribe a roll to one or more named characters — their phones pre-fill (die, modifier, adv/dis, label, optional DC), lock all controls except Roll, pulse the Roll button. Pad stays locked after a prescribed roll resolves to prevent unsolicited follow-ups.
- `send.py --dice-request --wait` blocks the GM until every prescribed character rolls (polls `GET /dice-request/<id>`). Exits 2 on timeout with a clean `DELETE` of the request.
- Main display "Waiting on…" badge driven by a new `dice_pending` SSE event, replayed on `/stream` connect.
- New `scripts/dice_player.py` wrapper takes `dice.py`-style syntax (`d20+5 --player piper --label "Stealth check"`) so the GM has a familiar CLI that routes through `/dice-request`.
- New `GET /character/<name>` endpoint serves the character markdown for the Sheet tab. Token-gated; both `<name>` and the resolved campaign value pass through an allowlist + length cap before `os.path.join`.

Token-gated on every new endpoint. `secrets.randbelow` for all dice. Strict input validation: spec regex `\d{1,2}d\d{1,3}` plus range checks, modifier clamped ±100, character/label strings stripped of shell metacharacters and length-capped. No external resources loaded, no new dependencies.

### Mode switcher + XSS hardening (upstream PR #39 / v1.11.0)

- **Phone-mode switcher.** Base URL now carries a small "📱 Phone Mode" button on the right rail (above the audio-controls cluster). Click drops a character dropdown populated from the campaign's current player roster (read from the existing `/stream` `stats` payload — no new server endpoint). Pick a name and the browser navigates to `?view=input&char=<Name>` automatically.
- **Full-display toggle.** Symmetric "👁 Full Display" button bottom-left in input mode strips the `?view` / `?char` query params and returns to the base view.
- **Defense-in-depth XSS fix on the dice-pending badge.** The "Waiting on…" badge rendered the server's `dice_pending` snapshot via `innerHTML` to support inline `<span class="dpb-label">` styling. Server-side validation strips `` ` $ \ `` from labels + character names but leaves `< > &` intact. A shared `_escHtml()` helper now wraps both `e.label` and every `e.pending[]` member name before they reach the template literal. Server side of `POST /dice-request` is unchanged — the right escape boundary is the innerHTML sink.

### Light / dark / auto theme picker (upstream PR #40 / v1.12.0)

Three-state theme picker. Default behavior unchanged — anyone who doesn't touch the picker continues to see the same dark, atmospheric display.

- **Dark** *(default)* — the existing ornate look.
- **Light** — parchment scroll on dark scenery. Body, vignette, and particle backdrop stay dark for contrast; `#text-content` becomes a cream "page" with deep-ink narration. Block-identity colors preserved at darker shades (GM-tab purple, NPC bronze, player blue, tutor moss, tutor-warning amber).
- **Auto** — follows the operating system's `prefers-color-scheme`. Switches automatically and tracks change live.

Picker lives as a fourth row in `#audio-controls` (top-right cluster) alongside Sound Effects / Type Speed / Auto Narrate. Click to cycle through Dark → Light → Auto → Dark. Per-browser persistence in `localStorage["gm-theme"]`. A small inline `<script>` in `<head>` reads that value and sets `data-theme` on `<html>` *before* the stylesheet parses — prevents the dark→light or light→dark flash on every load. Auto mode leaves the attribute off and lets `@media (prefers-color-scheme: light)` resolve.

Vellum palette carried over from the Neural Initiative implementation: parchment radial gradient `rgba(255, 245, 220, 0.96)` → `rgba(238, 224, 188, 0.82)`, deep-ink narration `rgb(40, 28, 14)` with warm-light shadow, bronze accents `rgb(70, 50, 18)` / `rgba(140, 95, 20, *)`. Action buttons flip to dark plate + cream text. Modal panels get the same parchment treatment with deep-ink text; modal backdrop stays dark to dim the scenery behind it.

Covers all narration block types, input panel + char tabs + staged queue, sheet + SRD modals + content tables, world clock, composing indicator, all v0.11.0 TTS chrome, the new phone-mode + full-mode buttons, and the dice-pending badge.

### Audio-controls legibility fixes (upstream PR #41 / v1.12.1)

Four small fixes surfaced by table-side testing immediately after the theme picker landed:

- `.theme-label` had no CSS rule and was rendering at browser-default `<span>` size (~16px) while every other label in `#audio-controls` was 7.5px Cinzel. Folded into the combined `.speed-label` / `.narrate-label` / `.theme-label` declaration so all three share font, sizing, and the new pill styling.
- The three click-to-cycle labels (Type Speed, Auto Narrate, Theme) now look like pill buttons — subtle dark inset background, bronze 1px border, 4px corner radius. Active-state ("On", "Light", "Auto") gets a stronger pill background.
- Default `#audio-controls` opacity bumped from 0.38 → 0.78. The cluster used to fade to barely-visible at rest; combined with `rgba(*,0.75)` text alpha that gave ~0.28 effective alpha against the dark backdrop. Individual label defaults bumped from `rgba(180,140,60,0.75)` to `rgba(220,185,100,0.85)`.
- Per-row hover affordance — each `.audio-row` now has its own hover state (subtle background pill, brighter label color). The row under the cursor reads as the active click target.
- `.dice-result` text was `#c8a040` with a faint glow — called out as "barely visible." Bumped to `#f0d27a` with a stronger amber glow plus a deep-ink drop shadow, wrapped in a faint dark plate. Light-mode dice-result picks up a matching deep-ink-on-parchment treatment.

## [0.11.0] — 2026-05-28 — Narrator TTS + i18n expansion (sync from claude-dnd-skill v1.10.0)

Two additive features ported from claude-dnd-skill v1.10.0, adapted to open-tabletop-gm conventions (GM terminology, relative paths, `GM_TTS_KEY` / `GM_SFX_LANGUAGES` env vars, key file at `~/.config/open-tabletop-gm/tts.key`).

### Narrator TTS via Gemini Flash TTS (optional)

Per-block speaker buttons on every `.dm-block` and `.npc-block` in the display companion, paired with a 9-voice dropdown (4 male: Charon, Enceladus, Fenrir, Umbriel; 5 female: Aoede, Gacrux, Kore, Vindemiatrix, Zephyr). Click to hear the block read aloud. Synthesis happens server-side via Google's Gemini Flash TTS through your own AI Studio API key — full setup walkthrough at `docs/SKILL-tts.md`, about five minutes with a free Google account.

A per-browser **Auto Narrate** toggle in the top-right audio controls (saved to `localStorage`) auto-plays each new narration block on that browser only. Turn it on for the casting TV, leave it off on player phones.

Voice selection is per-campaign — persisted to `state.md → ## Session Flags → tts_voice: <name>`. Switching voices mid-session updates the active marker across every visible block's dropdown simultaneously.

**Off by default.** With no key configured, the speaker buttons don't render and the display behaves exactly as it did before. Three layered fail-silent gates: no key → 503 and inert button; invalid voice → silent fallback to default; upstream error → button shows a brief diagnostic label, then resets, with text-only narration continuing.

Browser-side playback uses AudioContext with manual Int16 → Float32 PCM conversion rather than HTMLAudioElement, because iOS WebKit's per-call gesture gating on `<audio>` is hard to work around reliably. AudioContext only needs `ctx.resume()` once per session inside a user gesture. The 2000-character input cap matches Gemini Flash TTS's effective response limit; longer GM blocks are naturally chunked at block flush.

Gemini Flash TTS auto-detects the input language from the text — all 24 supported locales work transparently without any explicit language code on the request. A Spanish-language campaign just narrates in Spanish.

### i18n expansion to all 24 Gemini-supported locales

The two-language SFX foundation (English + Chinese) introduced upstream is extended to all 24 locales Gemini Flash TTS supports: `ar`, `bn`, `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `mr`, `nl`, `pl`, `pt`, `ro`, `ru`, `ta`, `te`, `th`, `tr`, `uk`, `vi`, `zh`. Same dict-of-dicts language-pack structure — each language contributes trigger phrases per SFX category, Latin scripts use word-boundary regex, unspaced scripts (CJK, Thai, Arabic) use literal substring matching.

The `_PRINTABLE` character allowlist and `_CHAR_NAME_RE` regex in `display/gm-display-app.py` and `display/wrapper.py` widen to accept letters from every script in scope: Latin Extended A/B, Greek, Cyrillic, Hebrew, Arabic, Devanagari, Bengali, Tamil, Telugu, Thai, Vietnamese diacritics, Hiragana, Katakana, Hangul, and the existing CJK ranges. Player and NPC names in any supported script are now first-class.

Default behavior is unchanged. The active language list stays `["en"]` (English-only) until explicitly overridden via the new `GM_SFX_LANGUAGES` environment variable (e.g. `export GM_SFX_LANGUAGES=en,zh,es`) or per-campaign via `state.md → ## Session Flags → sfx_languages: en,zh`.

Translation quality across the new packs is best-effort starter content. Community PRs to refine any pack are welcomed — the structure is designed for additive, language-by-language extension with zero code changes.

### README — Other ways to play

The README gains a short "Other ways to play" section between the Status block and Quick Start. It names the two sibling projects sharing this framework's design DNA: claude-dnd-skill (the Claude Code-specific upstream) and neuralinitiative.ai (a hosted browser version for users who'd rather skip a local install). Same maintainer, same design DNA, different surfaces.

## [0.10.0] — 2026-05-08 — System-versioning infrastructure (sync from claude-dnd-skill v1.8.0)

Many tabletop systems ship more than one set of rules over their lifetime. A campaign should declare the edition it's playing once and have the GM honour it from then on, without re-explaining at the start of every session which book the table is using. This release lays the framework-level groundwork for that — system-agnostic, deliberately small, deliberately opaque. The *infrastructure* lives in core; the *content* of any specific edition stays in the system module.

### What changed

**Per-campaign `**System Version:**` field on state.md**

A campaign records its chosen edition on the header line. The value is an opaque string from the framework's perspective — core never parses or validates it. The system module owns what counts as a valid value and what the default is.

- **`paths.campaign_system_version(name)`** for any script that needs to read the field. CLI passthrough: `paths.py campaign-system-version <campaign> [default]`.
- **`paths.system_data_path(system, version, filename)`** for any module that wants to ship version-keyed data files. Resolves to `systems/<system>/data/<filename>`; the system module composes file names however it prefers.

**`scripts/migrate_system_version.py`**

Backwards-compat migrator for legacy campaigns. `--check` (strictly non-mutating) and `--yes` (idempotent stamp + timestamped backup) modes. Used by `/gm load` against legacy campaigns. Edge cases handled cleanly: missing campaign exits 2; non-standard headers exit 0 "not-applicable" (so `/gm load` doesn't pester GMs of campaigns with hand-rolled metadata blocks).

**`/gm new` step 2 (optional version prompt)**

When the system module advertises a `## System Versions` section in its `system.md`, `/gm new` prompts for a version at creation time. Skipped silently otherwise.

**`/gm load` step 3 (migration check)**

Runs the migrator's `--check` and surfaces a one-time migration prompt for legacy campaigns. Already-stamped campaigns proceed silently. Subsequent steps renumbered.

**Display companion `#system-version-badge`**

Sidebar badge populated automatically from the campaign's recorded value. Empty hides. The server reads `paths.campaign_system_version` on `/stats --set-campaign`; `push_stats.py --system-version` exposes an explicit override.

**`systems/dnd5e/system.md` worked example**

Documents how a system module declares its versions via the `## System Versions` section. Other system modules can follow the same pattern when they need multi-edition support.

### Out of scope (deferred to follow-up PRs scoped to system modules)

- Per-version build and lookup routing scripts. These belong in `systems/<system>/scripts/`, not core.
- Edition-specific mechanics (e.g. weapon mastery in 5e 2024). Those are system content.
- The dnd5e module's per-edition datasets. Will land as a separate PR scoped to `systems/dnd5e/`.

### Backwards compatibility

Legacy campaigns predate the field. The migrator backs `state.md` up before any write, stamps the chosen version, and is idempotent. The migration default is **system-defined**, not core-defined — a system module configures whether legacy campaigns silently inherit "the older edition" or are forced to make an explicit choice. Core has no opinion. Character files don't need their own migration; they inherit the campaign's version at runtime.

### Companion development upstream

The parent project, [`claude-dnd-skill`](https://github.com/Bobby-Gray/claude-dnd-skill), shipped v1.8.0 today using this pattern. The dnd5e module there now carries data for both of its published editions, with provenance preserved per record (CC-BY-4.0). otgm users running the dnd5e module will get those data files in a follow-up PR scoped to `systems/dnd5e/`.

---

## [0.9.1] — 2026-05-01 — Display robustness + arc pre-emption (sync from claude-dnd-skill v1.7.5)

Three reliability bugs land hard fixes here, with regression tests so they don't come back. Synced from claude-dnd-skill v1.7.5 — same root causes affected both repos.

### What changed

**send.py — body-bundling restored, integrity checks added**

Bug: when `--stat-*` flags were combined with a heredoc body, `send.py` dispatched the stat update but silently dropped the narration. Root cause was in the stdin-read decision: `_build_stats_payload(args)` was treated as a "body-less" signal, so reading stdin was skipped even when a heredoc was attached.

- **Stdin decision rewritten.** Three categories distinguished cleanly: content flags require a body; truly body-less flags (`--milestone-award`, `--milestone-spend`) skip stdin; stat flags and `--set-campaign` are body-OPTIONAL — stdin read when piped (heredoc), skipped when an interactive TTY (avoids blocking).
- **Pre-flight `_validate_payload(...)`.** Chunk payloads must have text, an award, or a campaign tag; multiple content tags rejected; stats payloads must carry a list. Validation failures abort `sys.exit(2)` with stderr diagnostic.
- **HTTP-level receipt verification.** `_post(...)` now logs every send to `_SEND_LOG` and inspects response status. Non-2xx surfaces body excerpt to stderr.
- **Post-flight self-check.** Tallies failures from `_SEND_LOG`; display-offline yields one quiet stderr line; other partial failures yield `PARTIAL FAILURE` summary + `sys.exit(3)`.
- **Optional `--verify` round-trip** against the new `/health` endpoint. Use during dev/debug.

**gm-display-app.py — non-destructive tail persistence + atomic writes**

Bug: `session_tail.json` got silently wiped to `[]` between sessions, so `/gm load` had no last-scene replay. Root cause: `_load_tail()` cleared the buffer then re-appended campaign-filtered entries; if every entry filtered out, the buffer ended up zeroed and the next `_persist_tail()` wrote `[]` over the file.

- **`_load_tail` is non-destructive.** Builds a candidate buffer first; only swaps it in if at least one entry survived filtering. Empty/all-filtered/corrupt → buffer left alone.
- **`_persist_tail` skips on empty.** Refuses to overwrite an existing non-empty file with an empty buffer. Stderr warning when it does.
- **Atomic writes.** Tempfile + `os.replace(...)` — readers can never see partial state.
- **Legacy fallback path removed.** Tails only land in the campaign-specific file. If `CAMP_FILE` is missing/empty, persist holds the buffer in memory and skips disk — no shared file that bleeds across campaigns.
- **`/health` endpoint added.** Returns `alive`, `tail_buffer` count, `tail_file_size`, `text_log` count, `campaign`, `clients`. No auth required (no PII).

**`/gm save` tail backstop + `verify_tail.sh` + `write_canonical_tail.py`**

Belt-and-suspenders for the worst case. `verify_tail.sh <campaign>` checks the on-disk tail is healthy (>50 bytes, parses as a non-empty JSON list, entries have recognizable shape). When unhealthy, the GM writes a canonical replacement directly to disk via `write_canonical_tail.py` from session context — bypasses the display entirely so the file is good even after server crashes. Atomic write, campaign-stamped, capped at 30 entries. Both helpers respect `GM_CAMPAIGN_ROOT`.

**Beat 2b structural fix — pre-emption is a revision trigger**

Bug (campaign-level): when players act faster than the world, a beat's `world_pressure` event plays out fully without the `what_changes` consequence landing. Beats go stale.

Root cause: arc beats were generated with `what_changes` written event-shaped (something specific happens) when it should be consequence-shaped (something fundamentally different is true).

- **SKILL.md rule 8 added.** Pre-emption auto-triggers `/gm arc revise` at `/gm save`. Three landing-path templates: **cost** (party paid for moving fast), **secondary consequence** (world responds to being pre-empted), **deferred** (rewrite `world_pressure` toward same consequence on a longer horizon).
- **`/gm new` step 14 strengthened.** Arc-generation prompt explicitly demands `what_changes` be consequence-shaped, with worked event-vs-consequence example.
- **`/gm save` arc-check rewritten.** Performs explicit pre-emption check on each outstanding beat; auto-triggers `/gm arc revise` when needed.
- **`/gm arc revise` enhanced.** Surfaces three landing-path templates as a structured choice; before/after diff shown for review.

### Tests

- New `tests/test_display_robustness.py` (20 tests) covers: stdin-read decision across all flag combinations including the bundled-stat regression, payload validation, tail load (empty/filtered-out/matching/corrupt/missing CAMP_FILE), tail persist (skip-on-empty/atomic/no-camp-no-write), set-campaign body-optional path.

## [0.9.0] — 2026-05-01

The milestone feature is now visually complete. The award block alone wasn't enough for stack-based reward systems where the count is the whole point — Bennies, Fate Points, Hero Points all rely on knowing how many you have at any moment. This release adds the sidebar counter that v0.8.1 promised was coming.

### What's new

- **Milestone counter in the player sidebar.** Each character card now shows a row per active milestone label (`INSPIRATION 1`, `BENNIE 3`, `HERO POINT 2`) with a gold count pill. Empty labels are not rendered, and a label drops out of the sidebar entirely when its count hits zero — the card stays clean rather than accumulating zero-count entries.
- **Server-side mutation ops `_milestone_inc` and `_milestone_dec`** — the same pattern as the existing `_conditions_add` / `_slot_use` family. Increments are floor-clamped at 0 (a spend before any award has no effect; the label simply doesn't exist on the player).
- **`milestone_caps` per-player override** — for binary reward systems like D&D 5e Inspiration, set `milestone_caps: {"Inspiration": 1}` on the player and the count will never exceed 1 regardless of how many awards arrive. System modules can set this at character creation.
- **`send.py` now POSTs to both `/chunk` and `/stats`** for milestone events. The chunk renders the gold-glow feed block (already shipped in v0.8.1); the stats POST drives the sidebar counter (new).

### Test suite (now 63 tests)

`tests/test_milestone_counter.py` (8 new tests):
- Increment from zero creates the label
- Repeated increments accumulate
- Decrement-to-zero removes the label entirely
- Decrement below zero is floor-clamped (no negative counts)
- Multiple labels coexist on the same player
- `milestone_caps` per-label cap is respected
- Decrement without a prior increment is a no-op
- Other stat mutations (conditions, slots) don't clobber milestones

### Demo verification

In-process simulation: 3 award + 1 spend on Aldric → `{Bennie: 2, Hero Point: 1}`. Mira with `milestone_caps: {Inspiration: 1}` correctly capped at 1 despite two award calls.

### What stays deferred

- Phase 3 hybrid extractor mode — still out of scope for this LLM-agnostic fork.

---

## [0.8.1] — 2026-05-01

Two follow-ups from the v0.8.0 deferred list. The first replaces the upstream's D&D-specific `--inspiration-reason` with a system-agnostic equivalent. The second ports forward the future-tense planning verbs that just shipped in claude-dnd-skill v1.7.3.

### What's new

- **Generic milestone-event support** in the display companion. New `send.py` flags:
  - `--milestone-award NAME [--reason "..."] [--label "Inspiration"]`
  - `--milestone-spend NAME [--label "Inspiration"]`

  `--label` is the system-specific name for what was earned: `"Inspiration"` (D&D 5e), `"Bennie"` (Savage Worlds), `"Hero Point"` (Pathfinder 2e), `"Fate Point"`, etc. Default is `"Milestone"`.

  The award fires a gold-glow `.milestone-block` in the feed showing the character name, label, and reason. Spend events are processed but don't render a feed block (future work: sidebar counter for stack-based systems like Bennies). System modules can map their reward mechanic to this generic event without touching display code.

  The award block is also persisted in the session tail and replayed on browser reconnect.

- **Six new future-tense verbs in the seed**: `plans_to`, `intends_to`, `scheduled_to`, `aims_to`, `expected_to`, `targets`. All `lifetime: dispositional`, medium confidence. The deterministic extractor now picks up GM session-prep prose like *"Vedra plans to file the nomination Friday"* — previously silently dropped.

- **`V` wildcard in pattern templates** — represents a variable verb phrase (1–4 lowercase tokens) between a fixed modal phrase and an entity. One template `"X plans to V Y"` matches `"plans to file"`, `"plans to meet"`, `"plans to ambush before dawn"` against the same regex. Implemented with `(?-i:...)` so the wildcard never accidentally consumes a capitalized entity prefix.

### Test suite (55 tests, up from 48)

- Seven new `FutureTenseVerbTests` ported from upstream — V-wildcard capture, capital-letter exclusion, all six new patterns, end-to-end extraction.

### Demo verification

```
$ python3 display/send.py --milestone-award "Aldric" --label "Inspiration" \
    --reason "took the harder path through the Stairs"
→ gold-glow block in feed: "Aldric  INSPIRATION" / "took the harder path..."

$ /gm graph extract  (against synthetic 1-session log)
Captain Renna Voss --[plans_to]--> Mira Solveig
  "Captain Renna Voss plans to ambush Mira Solveig at the docks."
```

### What stays deferred

- Sidebar counter for stack-based milestone systems (Bennies, Fate Points). Fires correctly, just doesn't accumulate visually yet.
- Phase 3 hybrid mode — still out of scope for this LLM-agnostic fork.

---

## [0.8.0] — 2026-05-01

This sync ports forward the deterministic extractor + Phase 2.5 graph features that landed in claude-dnd-skill v1.7.1 and v1.7.2 today. The deterministic extractor is the centerpiece — it's exactly the LLM-free path the v0.7.0 release said was deferred, and it's why this fork exists.

Existing campaigns and `graph.json` files keep working unchanged. Everything new is opt-in.

### What's new

- **`/gm graph extract`** — pattern-matches the campaign's session logs against `data/graph/verb_table_seed.yaml`. Zero LLM calls. Estimated recall ~50%, precision ~95% on clean SVO and SVO-with-prep relationships. Output format matches the upstream Haiku extractor exactly so proposals are interchangeable.
- **`/gm graph extract --last-session-only`** — narrow extraction to the most recent `## Session N` block (skip the archive). Useful for end-of-session sweeps.
- **`/gm graph extract-apply --review`** — interactive proposal-by-proposal walkthrough with `y / n / q` prompts. Shows the verbatim source anchor and confidence for each proposal. Mutually exclusive with `--pick`.
- **`/gm graph close-edge --anchor "..."`** — record the verbatim phrase that justifies the closure as a new optional `closed_anchor` field on the edge.
- **`/gm graph supersede-edge`** — mark an edge as superseded (hard retcon). Use when canon explicitly contradicts a prior extraction. Optional `--by <correct-edge-id>` links the replacement; `--reason "..."` records why. Distinct from `close-edge`: closing ends a real relationship; superseding says the original was wrong.
- **Category-node edges**. State-verbs flagged `category_object_ok: true` in the verb seed (`possessed_by`, `worships`, `cleric_of`, `cursed_by`, `fears`, `flagged_offlimits`) now match patterns where the object is a categorical noun phrase ("a ghost", "the silver veil"). `extract-apply` auto-creates a node with `category_node: true`, `type: category`, `id: cat_*`. `scene-context` renders these with an `(unnamed)` marker so the GM remembers canon hasn't named them yet.

### Schema additions

- **`Edge.superseded_by`** — `<edge-id>` or `true`. `_edge_active_at()` excludes any edge with this set, so superseded edges never surface in `scene-context` but stay in the graph for audit trail.
- **`Edge.supersede_reason`** — optional human explanation of the retcon.
- **`Edge.closed_anchor`** — optional verbatim closure phrase.
- **`Node.category_node: true`** — flag on auto-created category nodes.
- **`verb_table_seed.yaml`** v0.5 ports forward with `lifetime: event | state | dispositional` annotated on every inclusion + borderline entry (119 total).

### Test suite (new)

`python3 -m unittest discover tests` → **48 tests in ~2s**, all green.

- `tests/test_verb_table.py` (12) — seed sanity, every entry has `lifetime`, lifetime values valid.
- `tests/test_deterministic_extract.py` (25) — entity recognizer, alias index (first-word / surname / middle-subsequence, stop-word rejection, ambiguity skipping), pattern regex, sentence splitter, session-number resolution, end-to-end synthetic campaign, dedup, last-session-only.
- `tests/test_gm_graph.py` (11) — actual `gm_graph.py` CLI: `add-node`, `add-edge`, `close-edge` with and without `--anchor`, `scene-context` filtering by `--at-session` (closed and superseded edges hidden), uninitialized-graph notice, `extract` writes JSON, `extract-apply --pick`, `supersede-edge` marks correctly, category-node creation from a possession scene.

### Demo verification

A synthetic 2-session "Winterhold" campaign extracted cleanly:

```
Aldric Brandt --[met]--> Mira Solveig             s1  (high)
Renna Voss --[attacked]--> Aldric Brandt          s2  (high)
wraith --[possessed_by]--> Aldric Brandt          s2  (low)  [X is category]
Mira Solveig --[swore_oath_to]--> Aldric Brandt   s1  (high)
```

`extract-apply` created 4 edges + 1 category node (`cat_wraith`). `supersede-edge --id e1` correctly hid the met-edge from later `scene-context` queries.

### What's still deferred

- **Hybrid mode** (Phase 3) — pattern-first then LLM-fallback on unmatched sentences. The LLM-agnostic constraint of this fork makes it less compelling here than upstream, but worth revisiting if a deterministic-only model can be plugged in via the same interface.
- **Future-tense planning verbs** (`plans_to`, `intends_to`, `scheduled_to`) — corpus mismatch (Reddit narrative is past-tense). Needs a separate corpus pass on DM session-prep documents.
- **`--inspiration-reason`** — D&D-specific Inspiration mechanic; needs design as a generic milestone event before porting.

---

## [0.7.0] — 2026-05-01

This release ports forward the campaign relationship graph that just shipped in claude-dnd-skill v1.7.0 — adapted for the LLM-agnostic constraints of this project — alongside the version-tracking infrastructure that's been overdue and a couple of important bug fixes.

The graph in this fork is **manual + query-only**. The Haiku-backed `extract` / `extract-apply` subcommands from the upstream version are not ported (they assume Claude API access). The high-value parts — `init`, `add-edge`, `close-edge`, and the `scene-context` query that auto-pulls at `/gm load` — work exactly the same way and don't require any LLM. When a deterministic verb-table extractor is built (it's designed in the upstream `docs/research/graph/phase-2-3-plan.md`), it will land here too.

### What's new

- **Campaign relationship graph.** `scripts/gm_graph.py` ships with subcommands `init`, `add-node`, `add-edge`, `close-edge`, `list`, `show`, `subgraph`, and `scene-context`. Local-only, time-stamped (`since_session` / `until_session`), with verbatim source-anchors on every edge. Stored at `<campaign-root>/<name>/graph.json`.
- **Auto-pull at `/gm load`.** Scene-context runs as part of the load flow, before the recap, so the GM has the active subgraph in scope before they speak. If the graph isn't initialized yet, the load flow offers an auto-init with a backup-first prompt — see below.
- **Backwards-compatible auto-init.** Existing campaigns don't have a graph. When `/gm load` notices `graph.json` is missing, it offers:

  > *"This campaign doesn't have a relationship graph yet. I can initialize one now — it improves long-session continuity recall when full NPC files fall out of context. As a safety precaution, I'll back up the campaign first to `<campaign-root>/<name>.backup-YYYYMMDD-HHMMSS/`. Proceed? [y/n]"*

  `y` runs a `cp -R` snapshot before anything touches the campaign, then proposes seed nodes and edges from the existing markdown for GM approval. `n` continues without the graph for that session and doesn't re-prompt. No silent extraction, no auto-write.
- **Sweep at `/gm save`.** The save flow scans the session for relationship shifts that weren't recorded live and presents them to the GM as a numbered list (`y / pick / skip`) before writing.
- **`/gm graph` command suite** documented end-to-end in `SKILL-commands.md`.

### Versioning is now tracked

- New `VERSION` file at the repo root (semver, `0.7.0`).
- New `CHANGELOG.md` (this file) with the full retroactive history.
- `python3 scripts/update_skill.py --check` (and `/gm update --check`) now shows local vs. remote version side by side, so it's obvious at a glance whether you've fallen behind.

### Bug fixes

- **`send.py` no longer hangs on chained-bash invocations.** Body-less calls (e.g. `--set-campaign` alone, or any `--stat-*` flag without text) were waiting on stdin that never came. Body-less detection now skips `sys.stdin.read()` entirely when the call has no content flag and only carries state-update flags.
- **Spell slots no longer 500 on long rest.** `display/gm-display-app.py` now accepts both `{used, max}` and legacy `{remaining, max}` slot schemas via a new `_normalize_slot()` helper. Affects systems that use spell slots and send them through the display.

### What's deferred (not in this release)

- **Phase 1 Haiku extractor** (`extract`, `extract-apply` subcommands) — Claude API-specific; doesn't fit OTGM's LLM-agnostic constraint. Ports forward when the deterministic Phase 2 extractor is implemented.
- **Verb-table seed and corpus tooling** — research artifacts from upstream; useful when Phase 2 lands but not user-facing in this release.
- **`/gm graph extract` documentation** — added back when an LLM-agnostic extractor exists.

---

## [0.6.0] — 2026-04-28

Two new commands that close the longest-standing usability gap: figuring out which copy of the skill you're running and where your campaign data lives.

### What's new

- **`/gm update`** — pull skill changes from `origin/main`. Refuses on a dirty tree, fast-forward only, so it never silently merges divergent history.
- **`/gm path`** — view or relocate campaign storage via the `GM_CAMPAIGN_ROOT` environment variable. Useful if you keep your campaigns in iCloud, on a network drive, or anywhere other than the default location. Existing campaigns aren't auto-migrated; the path resolver handles legacy fallback + copy-on-access.
- **`scripts/paths.py`** — central path resolution module. All campaign reads/writes route through `find_campaign()`, which honours `GM_CAMPAIGN_ROOT`, falls back to the legacy default, and copies on access if the campaign was found in the legacy location. Decouples the skill from any one install location.

---

## [0.5.0] — 2026-04-23

Display companion polish + arc-aware GM hints. The `app.py` rename to `gm-display-app.py` was overdue — having a generic-named file in the project root made it harder to find when grepping.

### What's new

- **`display/app.py` renamed to `display/gm-display-app.py`** — distinct, greppable name. `start-display.sh` updated to launch by the new name and `pkill` by it cleanly on restart.
- **Arc-aware GM hints.** The DM Help button (◈) now reads `## Campaign Arc` from `state.md` and tailors hints to the current beat — telegraphing what's in scope without spoiling the beat itself.
- **Reliability improvements** — display force-restart at `/gm load` (no more stale processes lingering), per-campaign log routing, `set-campaign` flow tightening, `check_input.py` for queued player input retrieval.

---

## [0.4.0] — 2026-04-20

The narrative-arc release. Every campaign now has a committed three-act narrative shape, and the GM is aware of it during play.

### What's new

- **Dynamic arc system.** Auto-generated at campaign creation from world threat + factions + setting. Six beats (1a/1b setup, 2a/2b confrontation, 3a/3b resolution) defined by *consequence* (`what_changes`) not by event. The arc commits to a thematic resolution. The shape bends; it doesn't break.
- **`/gm arc advance <beat>`** — mark a beat complete at session end.
- **`/gm arc revise`** — when a player choice significantly redirects the story, revise outstanding beats to fit the new direction without retconning what already happened.
- **`/gm arc new`** — generate a new arc from the consequences of a completed one. Same world, new story question.
- **Arc-aware GM steering.** The skill reads `## Campaign Arc` at every session load. World pressure for the next beat lands as a visible event before the beat itself. No beats delivered cold.
- **Campaign import** — `/gm import` accepts PDF, markdown, DOCX, or plain text. Extracts structure type, acts, chapters, key beats, telegraph scenes, NPCs, factions, and quest hooks. Builds all campaign files automatically.
- **Live State Flags** in `state.md` — compaction-resistant key-value block holding cover, faction stances, and NPC dispositions. Read first on any recap or status claim before falling back to fuller files.
- **`state.md` template** updated to include the arc + Live State Flags structure by default.

---

## [0.3.0] — 2026-04-18

Routing architecture + model evaluation. This release made open-tabletop-gm actually portable across LLMs, not just nominally.

### What's new

- **Model routing policy** — explicit Script / Tier-1 / Tier-2 / Tier-3 tiers per task class. Mechanics offload to scripts; narration uses the strongest available model; lookup uses the cheapest.
- **Lazy script loading** — `SKILL-scripts.md` and `SKILL-commands.md` load once at session start instead of being inlined in the system prompt. Smaller context footprint per turn.
- **`probe/` directory** — narrative-quality probe with 5-judge ensemble. 37-model sweep across OpenRouter providers; results in `probe/results/`. `--runs N` for averaged scores.
- **`SYSTEM-PORTING.md`** — first draft of the guide for porting non-D&D systems. Establishes the `systems/<system>/` module convention.
- **LLM guide expansion** — model-routing examples, OpenCode and LM Studio integration notes, narration vs. mechanics split documented.

---

## [0.2.0] — 2026-04-18

The first sync from claude-dnd-skill into this project. Display fixes + GM discipline mechanisms (`## DM Style Notes` → `## GM Style Notes`) ported across, with terminology adapted and Claude-specific paths replaced with relative ones.

### What's new

- **GM discipline mechanisms** — calibration block in `state.md → ## GM Style Notes`, read at every load, accumulates table-specific patterns across sessions. Compounds the skill's "read this specific player" standard rather than resetting each session.
- **NPC full-entry split convention** — `npcs.md` index + `npcs-full.md` per-NPC entries with personality axes, relationships, hidden goals. GM reads the full entry proactively before voicing dialogue.
- **Display fixes** — faction validation, device approval persistence, deadlock fix, sheet modal `console.warn` + `/gm load` hint.
- **Autorun mid-wait queue check** — when a GM message interrupts the autorun wait, check `.input_queue` once before processing the message.

---

## [0.1.0] — 2026-04-16

Initial release. The shape of the project was already there at first commit.

- Persistent campaigns at `<campaign-root>/<name>/` with `state.md`, `world.md`, `npcs.md`, `session-log.md`, `characters/`.
- `/gm` command suite: `new`, `load`, `save`, `end`, `list`, `recap`, `world`, `quests`, `character new/sheet/import/level-up`, `roll`, `combat start`, `rest`.
- Twelve applied GM behavioral standards in `SKILL.md` enforced as hard constraints in every session.
- Helper scripts: `dice.py`, `combat.py`, `character.py`, `tracker.py`, `calendar.py`, `lookup.py`, `xp.py`, `ability-scores.py`, `campaign_search.py`.
- Cinematic display companion (Flask SSE): typewriter narration, scene-reactive backgrounds, dynamic sky canvas, live party sidebar, LAN mode with TLS, player input form, autorun mode.
- Bundled SRD dataset (D&D 5e for the default `systems/dnd5e/` module); `systems/<system>/` convention for porting other rulesets.

---

## Versioning policy

- **PATCH** (0.7.x) — bug fixes, doc updates, no behavior change.
- **MINOR** (0.x.0) — new commands, new scripts, new opt-in features. Existing workflows continue to work without modification.
- **MAJOR** (x.0.0) — breaking change to campaign data format, command rename/removal, or workflow that requires migration.

Tag releases with `git tag v<version>` and update both `VERSION` and `CHANGELOG.md` in the same commit. Tags follow `vX.Y.Z` format.
