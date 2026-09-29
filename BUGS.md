# Bug log — display & session tooling

**Scope:** `open-tabletop-gm/display/`, plus session-state hygiene.
**Opened:** 2026-09-29, from an engineering review of the GM-watcher work.
**Status key:** `FIXED` · `OPEN` · `FALSE` (reviewer claim, disproved) · `KNOWN-OK`
**Log committed:** 2026-09-29, with `display/drain_queue.py`, `display/gm-watch.sh`,
`tests/test_drain_queue.py` and `tests/test_gm_watch_sh.py`.

---

## Severity 1 — data loss

### B1 — `drain_queue.py` deleted actions it never read — **FIXED**
`drain_queue.py` read `.input_queue` then `unlink`ed it. The Flask app writes that file concurrently via write-tmp + `os.replace`, so a player pressing Send between the drain's `read()` and its `unlink()` had that action **deleted without ever being delivered**.

The docstring actively asserted the opposite ("we own the read" — false; the app owns writes concurrently, and documents this exact hazard at `gm-display-app.py:370`).

**Fix applied:** claim-then-read, copying the primitive already in `check_input.py:112-122` — `os.replace(QUEUE_FILE, QUEUE_FILE + ".taken")` *before* reading, then read the claimed file, then unlink it. An action written after the claim lands in a fresh file picked up on the next poll. False docstring paragraph deleted.

**Follow-up (2026-09-29, this commit):** the claim introduced its own, narrower
data-loss window. Once `os.replace` has run the actions exist *only* in `.taken`, so
anything throwing after that point — a read error, a decode failure — left them in a
file no later poll reads. `drain_queue.py` now tracks whether the claim succeeded and,
on a failure after it, moves `.taken` back to `.input_queue` and reports nothing this
run (delivering *and* restoring would narrate the same action twice; the next poll picks
it up). If the restore itself fails it says so loudly and names the stranded file.
Covered by `tests/test_drain_queue.py` (10 tests; the three recovery tests fail against
the unfixed version).

### B2 — `gm-watch.sh` logged success without checking — **FIXED**

**Follow-up (2026-09-29, this commit):** the restore-on-failure path wrote
`printf ... > "$QUEUE"`, which truncates. The drain had already consumed the action by
then, so a player who sent a *second* action while the GM turn was running had it
destroyed by the restore meant to save the first — the same class of loss as B1, in the
other file. Both restore sites now append. Covered by `tests/test_gm_watch_sh.py`.
`opencode run`'s exit status was never inspected; `"GM turn complete"` was logged unconditionally. Dead session, missing binary, or GM crash meant the action was already consumed and the log claimed success.

**Evidence this actually happened:** `.gm-watch.log` shows an action drained at `10:37:49`, the watcher dying 4s later, and restarting — the action was consumed, never narrated, and no error recorded.

**Fix applied:** capture `RC=$?`; branch on 0 / 124 / 137 / other; log loudly; **restore the raw action to `.input_queue` on any failure** so it retries next poll.

### B3 — no timeout on `opencode run` — **FIXED**
An observed turn ran ~4 minutes with no bound. A hang would wedge the serial loop permanently; every later action would pile up unseen while the display still showed "Sent" — the exact failure these files exist to prevent.

**Fix applied:** `timeout --signal=TERM --kill-after=30 900`, with exit 124/137 handled as a distinct case.

### B5 — drainer deleted `.input_trigger` — **FIXED**
`drain_queue.py` unlinked both `QUEUE_FILE` and `TRIGGER_FILE`. The trigger is not a drain artefact — it is the signal `wrapper.py` polls at 50ms (`wrapper.py:14,56`) to know it may inject. A single drain cycle would swallow a legitimate promote-to-now signal.

**Fix applied:** only `QUEUE_FILE` is touched.

### B6 — four unsynchronised consumers of `.input_queue` — **OPEN (accepted)**
| Consumer | Primitive | Safe? |
|---|---|---|
| `check_input.py:112` | `os.replace` claim-then-read | yes |
| `wrapper.py:206` | read + `unlink` | **no** (pre-existing) |
| `autorun_wait.py:33` | reads `QFILE` | needs review |
| `drain_queue.py` | `os.replace` claim-then-read | yes (fixed) |

`_queue_lock` (`gm-display-app.py:317`) serialises the *writer* only. `wrapper.py:206-228` has the same read-then-unlink defect as B1 and predates this work. `drain_queue.py` originally copied the buggy pattern rather than the correct one sitting beside it.

**Note:** only one consumer is active in this deployment (the watcher), so this is latent, not firing.

---

## Severity 2 — display UI

### B7 — dice pad still unreachable in the main view — **FIXED** (open-tabletop-gm#63)

> Both halves of this were real and both shipped separately. The CSS half (the pad's
> ancestor `#input-body` is `display: none` while the panel is collapsed) was fixed in
> #57 by overriding the collapse while the pad is floated. The half this entry reports
> as "requires a click on the Party Input bar" was fixed in **#63**: `_initDicePad()`
> was still called only inside `if (_inputMode)`, so the main view floated a pad whose
> Roll button had no listener — reachable, visible, inert. Confirmed in a real browser
> (clicking Roll fired no request) and now fires `POST /player-input/dice`.
> The "decision needed" below is therefore settled by #63: no auto-expand, the badge
> floats the pad without opening the panel.

The CSS fix made `#dice-pad` *eligible* to show, but it lives inside `#input-panel`, which ships as `class="collapsed"` (`index.html:3105`), and `#input-panel.collapsed #input-body { display: none; }` (`index.html:1577`). The only load-time removal of `collapsed` is inside the `if (_inputMode)` block.

**Consequence:** in the main view the pad is wired to a container that is still hidden. Requires a click on the Party Input bar.

**Decision needed:** is auto-expanding the panel on a TV display acceptable, or is click-to-expand correct and the docs should just say so? Also `ROADMAP-ideas.md` N2 notes `#dice-pending-badge` is `pointer-events: none`, so the badge cannot be used to open it either.

### B8 — main-view pad inherits the last player's name — **OPEN (cosmetic)**
`_initDicePad` reads `?char=` / `?character=` to bind a character. `_inputMode` requires those params to be *absent* in the main view, so it always falls through to `localStorage['gm_player_name']`. The pad on the DM's screen is labelled with whatever the last phone typed, so an accidental roll is attributed to a player.

### B8a — `_initDicePad` double-init safety — **KNOWN-OK**
No second call site exists; the `dpInit` guard is defensive but not load-bearing. The click handler sets `rollBtn.disabled = true` before any `await`, so a double-tap cannot produce two rolls.

---

## Severity 3 — process state

### B9 — watcher and server are orphaned (PPID 1) — **OPEN**
Neither has a controlling terminal, so the header's "Stop with Ctrl-C" is false. Only the pidfiles are usable.

**Correct shutdown order** — consumer first, then server:
```bash
kill "$(cat open-tabletop-gm/display/.gm-watch.pid)"   # stop the consumer FIRST
kill "$(cat open-tabletop-gm/display/app-5001.pid)"   # then the server
rm -f open-tabletop-gm/display/{.gm-watch.pid,app-5001.pid}
```
Stopping the server first re-creates the B5 hazard for anything still polling.

### B10 — `trap` cannot kill the in-flight `opencode run` — **PARTLY FIXED**

> The turn is now bounded on every platform: `timeout`/`gtimeout` when coreutils is
> present, and a pure-bash watchdog (TERM, then KILL after 30s) when it is not — which
> is every macOS box without brew. Calling coreutils `timeout` unconditionally made
> every turn exit 127 -> FAILED -> restore -> retry, forever. Exercised against a real
> hang: terminated in 2s and reported as RC=124, which the handler treats as a timeout.
> Still open: the watcher holds the turn in the foreground, so Ctrl-C fires `trap` while
> `opencode run` is still a child; that child is not signalled by the trap.
bash defers trap execution until the foreground child returns. `kill` on the watcher ends the loop; the in-flight turn re-parents to init and keeps writing. A ~4-minute window per turn where shutdown leaks an orphan.

### B11 — pidfile double-start guard is TOCTOU — **OPEN**
`kill -0` + write is not atomic; two simultaneous starts both pass. `kill -0` also succeeds on a *recycled* PID, so a stale pidfile after reboot blocks startup with a false "already running". Use `mkdir` or `flock` as the atomic primitive.

### B12 — runtime artefacts not gitignored — **FIXED**
`.gm-watch.log`, `.gm-watch.pid`, `app-5001.pid` are unignored. A `git add -A` would commit a ~91KB log **containing the full player session** — narration, HP, spell slots, the player's own dialogue. Only `.input_queue` is currently covered.

### B13 — both repos on detached HEAD, behind origin — **OPEN**
`open-tabletop-gm` is 17 behind `origin/main`; `dnd-gm` is 4 behind. The new scripts and the `index.html` change sit on a detached commit. Reconcile before committing — PR #55 rewrote both files a rebase would touch.

### B14 — the GM subagent writes into the git working tree — **OPEN (containment)**
The live GM runs with `--auto` and full tool access, and has been observed editing `display/gm-watch.sh` and `display/drain_queue.py` in place — changing "staged" to "sent" wording in both, consistently. It also created and removed a `campaigns/` directory inside the repo mid-session.

**Risk:** the GM can `git add` campaign spoilers into a public repo. It has no reason to be able to.

---

## Severity 4 — display server

### B15 — `send.py --dice-request --wait` false success — **OPEN (reported by GM)**
`send.py --dice-request --wait` reports *"all rolls received"* while returning `{"complete":true,"pending":[],"results":[]}` — zero dice actually entered — plus `Connection refused` on every poll. The GM correctly fell back to `dice.py` and showed real math rather than inventing a number.

---

## Severity 5 — stale test fixture

### B16 — `tests/fixtures/Kairos_Level1.md` describes a different character — **OPEN**
Used by six test modules (`test_tactics_cli.py:21`, `test_phase5_encounter_design.py:41`, `test_localdm_play.py:386`, `test_localdm_bridge.py:12`, `test_spell_slots.py:31`, `test_localdm_autopilot.py:108`). Divergence is larger than skill proficiencies:

| | fixture | live sheet |
|---|---|---|
| Point buy | INT 15, WIS 12, DEX 14 | INT 17, WIS 10, DEX 13 |
| Background | Quandrix Student | Sage |
| Insight | +3, ✓Kenku Recall | +0, not prof |
| Nature | +5, ✓Quandrix | +4, not prof |
| Religion | +3, not prof | +6, ✓Kenku Recall |
| Passive Perception | 11 | 12 |

Encoded in assertions: `test_tactics_cli.py:393` and `tactics_fixtures.py:122` both pin `passive_perception == 11`. `test_tactics_spells.py:329` is a boundary test that ties at exactly 12, so fixing the fixture will require re-deriving it.

**Consequence:** the tactics suite green-lights a different character from the one being played.

---

## FALSE — reviewer claims disproved

### B17 — "passive Perception / Investigation arithmetic is wrong" — **FALSE**
Claim: `Kairos.md:69` parentheticals "sum to 2 and 6, not 12 and 16," so the headline numbers are wrong.

5e passive score is `10 + skill modifier`. Perception modifier = WIS(+0) + prof(+2) = +2 → passive **12**. Investigation modifier = INT(+4) + prof(+2) = +6 → passive **16**. Both correct; the parenthetical describes the *modifier*, not the whole passive.

**One real defect did exist and is fixed:** `Kairos.md:89` carried a stale "passive Investigation 14 to 19" from the intermediate reconciliation state. Corrected to `16 to 21`.

---

## Severity 1 — the Atlas import never worked on a real scene file

### B21 — `atlas_to_map.py` read `schema` off the top level and refused every real `.atlasmap` — **FIXED**
Atlas persists through zustand's `persist` middleware, so the file on disk is the storage **envelope** `{state: <MapFile>, version: N}`, not the bare `MapFile` (`MapPersistence.ts` `setItem`; Atlas's own `MapLoader.ts:36-37` unwraps `state` before reading `background`). `load_scene` did `scene.get("schema")` on the envelope, found `None`, and raised `expected schema 'atlas-vtt', got None` on every scene Atlas has ever written.

The test fixture held a **bare `MapFile`** — a shape Atlas does not write — so the whole suite passed green against a format that cannot occur. Nothing exercised the real file.

**Evidence:** a hand-built envelope matching the real shape (`MapPersistence.ts` `MapFile` + persist envelope) reproduces the failure exactly; it passes after the fix and converts end to end (20x14 cells, image copied, `compile_map` accepts it).

**Fix applied:** `load_scene` unwraps `state` when it is a dict and falls back to the bare shape — `migrateMapFile` produces the unwrapped one, so both are real and both load. The schema check still runs against the unwrapped object, so unwrapping is not a way in (`test_an_envelope_holding_a_foreign_schema_is_refused`); a non-dict `state` is refused rather than silently read as an empty map. The fixture is now the envelope, with the bare-MapFile case kept as its own test. 7 new tests.

### B22 — `atlas_to_map.py` ignored `unitType` and read a metric grid as feet — **FIXED**
`grid_geometry` read `unitDistance` and assumed feet. Atlas offers `feet | yards | meters | units` (`MapPersistence.ts` `GridState`). A scene set to metres with `unitDistance: 5` converted to a 5-foot grid: a map where every range, reach, speed and opportunity-attack distance is out by ~1.6×, rendering correctly and playing wrong. Exactly the failure KC4 exists to prevent, in the one script that claims to refuse it.

**Fix applied:** `grid_geometry` refuses a non-`feet` `unitType`, naming the fix. Unset stays feet — that is Atlas's own default and what a fresh scene carries. 4 new tests.

---

## OPEN, low severity

### B18 — backup accumulation — **OPEN**
Three full campaign snapshots, two `*.injection-backup.json` pairs, and `state.md.bak-20260929`. None in git, none auto-cleaned. The `0922` snapshot is the only pre-session restore point — keep it; the 09-25 and 09-26 ones are candidates for deletion.

### B19 — `state.md` `first_sound` is a definition in a status field — **OPEN**
Was `first_sound: pending (1.0; canon comfort sound)`; a GM subagent replaced it with a full definition asserting the player canonised it in character. Verify against the transcript before treating as canon. **The player did say "good night ?" in the session, so this one is corroborated.**

### B20 — file permissions — **OPEN (cosmetic)**
`SCENE-0B-REBUILD.md` is mode 644; the rest of the campaign directory is 600. Same for `graph.json`, `session_tail.json`, `text_log.json`, `tracker.json` and the backup JSONs.
