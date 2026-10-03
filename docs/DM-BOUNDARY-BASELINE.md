# DM boundary baseline

**Issue:** #127 (`test(dm): measure current check policy and puppeting rates`)
**Measured at:** `open-tabletop-gm` `478fc7e` (`origin/main`)
**Instrument:** `tests/dm_boundary_lens.py` · **Pins:** `tests/test_dm_boundary_lens.py`
**Reproduce:** `python3 -m pytest tests/test_dm_boundary_lens.py -v`

---

## What "puppeting" means here

**The DM deciding something the engine owns.** Not railroading (the world moving) and
not only dialogue in the PC's mouth: the adjective is *engine-owned*. The repo's own
rule is "the engine owns the rules, the GM narrates outcomes". A violation is any beat
where the model resolved a question `scripts/tactics/` answers instead.

Engine-owned: distance, cover, damage, a save, a slot, a check's DC, whether a fight
is running, whether a lasting mechanical spell effect applies.
The model's: what the guard looks like, what an NPC says, whether the attempt might
work at all.

## Pin

| Field | Value |
|---|---|
| Commit | `478fc7e` (`origin/main`, after #202) |
| Model | none. Every case is a scripted fixture reply through `FakeClient`. |
| Config | `GM_CHECK_POLICY` unset, so `checks.decide(mode="on")`; `GM_SHADOW` off; no display |
| Campaign | built from `tests/fixtures/Kairos_Level1.md`, the player-facing sheet. No `answer-key.md`, no DM-sealed folder, no `campaigns/`. |
| Suite | 2917 passed, 98 skipped at this commit (`python3 -m pytest tests/ -q -n 4`), after `python3 systems/dnd5e/build_srd.py --no-fvtt` (a fresh worktree has no dataset) |

## The baseline

```
M1  detection, one row per detector   denominator: planted cases for its boundary
    reply.reveals_check_outcome            2/4
      missed: pre-resolved-fragment, pre-resolved-knowledge
    reply.speaks_for_player                1/2
      missed: third-person-emotion
      false positive on: echo-of-own-words, sensation-not-emotion
    puppet_lens_detector                   2/2
    reply.states_an_unbacked_cast_result   2/2
    reply.unbacked_numbers                 3/3
    reply.grants_injection                 3/3
    reply.fakes_system_log                 1/1

    detectors                       7
    caught / planted                14/17 (82%)
    blind (caught nothing)          none
    false positives                 reply.speaks_for_player
    unmeasured                      none

M1  controls                        denominator: 14 clean cases, swept by every detector
    total false positives           2 over 98 detector/case pairs

M2  check policy  denominator: check requests reaching Session._ability_check
    6 kinds over 8 requests: auto-easy 1, auto-passive 1, no-stakes 1, not-on-sheet 1,
    retry-refused 1, rolled 3; rolled 3/8, retry refused 1

M3  turn asks     denominator: DM turns in a three-beat session
    2/2 out-of-fight turns carried '## Your task' with the uncertainty question; the
    combat beat asks for a tactics command and refuses a check in the open (1/1)

M4  routing       denominator: player lines the engine can claim
    4/6 answerable lines claimed by the engine with no model call; unclaimed:
    "how many first-level slots do I have left"; "can I cast magic missile"
```

## Definitions, so the numbers can be re-derived

**M1 detection.** Denominator: the planted cases **for that detector's own boundary**.
17 planted crossings across 7 detectors. A detector with no planted case is reported as
`NOT MEASURED`, never as a rate: a row with zero denominator would divide to a perfect
or a zero score depending on how it was written, and both would be invented.

**M1 controls.** Denominator: 14 clean cases swept by **every** detector, so a control
written as a near miss for one instrument is also checked against the others. 98
detector/case pairs, 2 false positives.

**M2 check policy.** Denominator: check **requests** that reached
`Session._ability_check`, not turns. One turn asks for one check, so the reportable
number is what the engine did with each request. Driven through the real method, so the
sheet lookup, `checks.decide` and the retry ledger are all in the path and "no model
call" is true by construction.

**M3 turn asks.** Denominator: DM turns. The out-of-fight ask and the combat ask are
counted separately because they are different asks; averaging them would produce a
number that describes neither.

**M4 routing.** Denominator: the six `ROUTABLE` player lines in the test. Whether the
engine claimed one is decided by `c.dm_calls() == []`.

## Prior-finding comparison

| Prior finding | Date | Prior state | Measured now |
|---|---|---|---|
| The die is decorative: `transcript.jsonl` had **zero** `check` fields across a full run | 2026-09-28 | 0/4 arbiter trials, 0/6 prompt variants | **Structural precondition restored.** 2/2 out-of-fight turns carry `## Your task` with the uncertainty question (PR #81, commit `c0bd9f4`). The model-side rate itself is still unmeasured. |
| A fabricated skill became a real roll (Swim at +0) | 2026-09-28 | rolled | **Closed.** 1/1 of M2's off-sheet requests is refused by name and nothing is rolled. |
| A failed check that changed the world at no cost | 2026-09-29 | flagged | **Closed** by `reply.is_costless_failure` (commit `9d2eab4`, #170). Out of M1's scope; not re-measured here. |
| "The Marcus bug" | 2026-09-29 | flagged | **Closed** by `reply.NameLedger` (commit `33dd99c`, PR #114). Not an engine-owned boundary, so not in M1. |
| The DM contradicted the engine's own AC (13 against a computed 15) | 2026-09-30 | uncaught | **Caught now.** `reply.unbacked_numbers` fires on a claim the backing does not contain (commit `1bfc590`, PR #192). M1 catches 3/3 planted number crossings. |
| A slot spent answering a status question | 2026-09-30 | reproduced | **Partly closed, partly live.** The reported line ("what is my AC right now and how many first-level slots do I have left") is now claimed by the explore router and never reaches the model (commit `6aba0f0`, PR #150). Two unclaimed lines remain; see finding 4. |

## Findings

Four, in the order a reader should care about.

### 1. The pre-roll guardrail misses two of four shapes it exists for

`reply.reveals_check_outcome` catches 2 of 4 planted cases. It misses:

- **A result stated as a fragment.** "You lean over the ledger and run a finger down
  the column. A name, inked." No outcome verb, so the verb list cannot see it.
- **A result stated as knowledge.** "You know there is no name here." `know` is not in
  `_CHECK_OUTCOME`.

Both are the beat the guardrail exists for: the check has not been rolled. This is not
a corpus artefact: `puppet_lens_detector.pre_resolved_findings` reads both as
`pre-resolved`, which is why `test_the_two_coverage_gaps_are_pinned_as_gaps` asserts
both halves.

**Not fixed here.** #127 is a measurement, and moving `reply.py` would move the
baseline this issue exists to establish. Follow-up: extend `_CHECK_OUTCOME` with the
fragment and knowledge shapes, keeping the existing negative controls clean.

### 2. The agency guardrail reads no player line, so it fires on a faithful echo

`reply.speaks_for_player` fires on 2 of 14 controls:

- `'I tell the archivist, "I only want the catalogue."'` → `'"Only the catalogue," you
  say.'` The DM echoing the player's own words.
- "You feel the cold of the leather under your palm." A physical sensation with no
  emotion in it, which is the DM doing its job.

**Cost: one wasted model call each, not a shipped defect.** `Session._dm` adopts a
guardrail rewrite only when the rewrite comes back clean, so a false positive keeps the
first draft and costs one call. Two of fourteen is still a measurable tax on a
per-turn path.

**Not fixed here.** Widening it needs the player line, which `Session._dm` has and the
module-level function does not. That is an interface change, not a regex change, and it
deserves its own issue.

### 3. The agency guardrail reads second person only

`reply.speaks_for_player` misses "Kairos feels a chill of recognition". The puppet lens
catches it (2/2). The lens lives in `tests/puppet_lens_detector.py` for the outer-repo
harnesses and is **not** consulted by the loop, so in play this shape is unchecked.

**Not fixed here**, for the same reason as #2.

### 4. Two answerable lines still reach the model, and neither can spend a slot

M4: 4 of 6. Unclaimed:

- "how many first-level slots do I have left" -- `fightq.SELF_TOPICS` has no slot topic.
- "can I cast magic missile" -- no topic matches.

These two still reach the model. What changed is what happens next, and this section
was the stale record of it: it previously said `Session._player_turn` honours `r.cast`
with **no check that the player's line was a cast at all**, so a scripted DM reply
carrying `"cast": "Mage Armor"` on that line spent a level 1 slot and told the player
their AC changed while asking about slots.

**#251 has since landed the check that closes it.** `Session._player_turn` now tests
`fightq.is_questionish(line)` and refuses a cast the player's line phrased as a
question, spending nothing and saying so (`CAST_ON_A_QUESTION`). Pinned in
`tests/test_spell_command_boundaries.py::test_a_question_shaped_line_never_spends_a_slot`
(the class, four inputs) and in
`tests/test_dm_boundary_lens.py::test_an_unclaimed_status_question_never_spends_a_slot`
(this line). The second of those used to assert the opposite; the two specifications
were merged twenty-four minutes apart without either seeing the other, and #251 is the
one that ships.

**Still open, deliberately.** The guard's cost is that a player who asks
conversationally gets nothing -- the exact complaint #127 was filed about. The refusal
tells them to re-ask in the imperative, but nothing offers to cast it for them. The
confirm affordance (decline AND offer, so the cost is only paid if the player declines
to confirm) is filed as its own issue and is not built here.

## Detector limits

Stated so the numbers are not read as more than they are.

1. **The corpus is hand-written.** Every number above is a rate over the **detectors**.
   None of it says how often a 9B model crosses a line.
2. **The model-side rates are not measured here and are not estimated.** Whether a 9B
   model requests a check on an uncertain turn, and how often it puppets in live play,
   both need an endpoint. The instruments that have one already exist and are
   unchanged: `dnd-gm/test_dice_lens.py` and `dnd-gm/test_puppet_lens.py`. Until one of
   them is run against a named model, "the die is decorative" should not be restated in
   either direction.
3. **One detector, two rows.** `player-voice` is measured twice because
   `speaks_for_player` and the puppet lens do not agree. Collapsing them would average
   two instruments into a number that describes neither.
4. **The three combat-facing boundaries are gates, not detectors.** `mid-fight-check`,
   `mid-fight-cast` and `check-decision` are decided by control flow, so they have no
   text shape and are measured by M2 and M4 instead of scored by M1.
5. **Recall is lower-bounded, precision is not.** The detectors are written to miss
   rather than shout, so 14/17 is a floor on the crossings caught. The false-positive
   column is the one that makes the floor meaningful.
6. **M2's denominator is eight requests.** Enough to show every verdict kind the loop can
   produce, not enough for a rate. The distribution is the finding, not a percentage.

## Mutation proofs

Each reverts a shipped guard, keeps the tests, and shows red.

| Mutant | Reverted | Result |
|---|---|---|
| A | `play._player_turn`: the `in_fight` branch's `CHECK_MID_FIGHT` / `CAST_MID_FIGHT` refusals replaced with `refused = []` | 3 failed (both mid-fight tests + the combined combat-ask test) |
| B | PR #81: `self._dm(player=line, engine=engine, notes=notes, task=PLAYER_TURN)` → `task` dropped | 1 failed: no `## Your task` in the sent message |
| C | `checks.decide`: the retry refusal gated on `False and ...` | 2 failed here, 2 more in `tests/test_localdm_checks.py` |

Mutant A first landed on the `out-of-fight` copies of the two refusals and changed
nothing, because for a player's turn in a fight the `in_fight` branch returns first.
The live guard is the `in_fight` one; the mutation was corrected before the proof above.

## What this is not

Not a claim that the engine owns the rules correctly today. It is a claim about the
**DM-facing boundary only**, with the denominators stated, the gaps named, and the
model-side question left explicitly open rather than answered from a fixture.