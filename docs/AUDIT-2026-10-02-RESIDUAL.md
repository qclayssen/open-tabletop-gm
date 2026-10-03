# AUDIT-2026-10-02 residual findings: reconciliation

**Issue:** #252 (`test(audit): reconcile remaining AoE and prompt-boundary findings`)
**Source audit:** `docs/audits/AUDIT-2026-10-02.md` (outer repo), MEDIUM, Test coverage + Security
**Reconciled at:** `open-tabletop-gm` `4c9127b` (`origin/main`)
**Fixtures:** `tests/test_aoe_invariants.py` (138) · `tests/test_prompt_boundary_disclosure.py` (18)

Every finding below is one of: **verified** (reproduced on this build), **not
reproducing** (the source claim does not hold), **already-shipped** (fixed since the
audit ran), or **gap** (confirmed, with no fix here and a named owner). Nothing in this
document asserts a defect that was not reproduced.

---

## Per-finding disposition

### F1 · AoE geometry has only hand-worked examples, no invariant tests
**Audit claim:** `grid.py:374-464`. "The invariants *do* hold today; this is the missing net."
**Disposition: verified (a coverage gap), now netted.**

The audit is right about the gap and right that nothing is broken. `grid.area` had
hand-worked examples only (`tests/test_tactics_grid.py`), which pin the answers somebody
already computed rather than the properties those answers came from.

`tests/test_aoe_invariants.py` generates over 12x12 boards (three terrains: open, a wall
column, a pit), every shape in `grid.AREA_SHAPES`, sizes 5/10/15/20/30 ft, and aims in
all eight octants at three distances. 15 geometry properties and 9 resource properties.

| Property | What it rules out |
|---|---|
| in-bounds, never a wall | an off-map or walled square in the caught set |
| unique, sorted, stable | a roll-receipt diff that changes run to run |
| the caster is never inside his own cone or line | an `along <= 0` boundary slip |
| along in (0, cells], across within the width | a shape that reaches or widens too far |
| a sphere contains the square it is aimed at | the boundary rule dropping its own centre |
| spheres nest by radius | a non-monotone boundary at one radius only |
| a cube whose block fits is exactly n x n | a cube that is a rectangle, or clipped |
| an unknown shape is refused | a new shape falling through to the cube branch |
| one action, one recharge, whatever it caught | a per-target charge or a free second use |
| one damage roll for a whole area | an area handled per target |
| cover 0 at the origin square | a creature granting itself cover from its own blast |
| a refused cast spends nothing | validation reordered after the spend |

**Sampling, stated.** A full sweep of the board is 514,800 `area` calls and takes 66 s
measured, which is a suite nobody runs twice. The shape x size x board sweep therefore
uses 16 casters on a lattice and 8 aims (one per octant) at three distances, covering
every direction a shape can point and every distance a size reaches. The per-shape
property tests widen onto every (caster, target) pair, capped at 400. This is a
subsample of pairs, not an exhaustive proof, and the test file says so.

**One dead guard found, and reported rather than tested around.**
`_cover_from` (`spells.py:80-84`) has an explicit "origin square gets no cover" check.
Removing it leaves all 138 tests green, because `grid.cover` already returns 0 when
attacker and target are the same square. The check is therefore defensive, not
load-bearing, on the current `grid.cover`. The test pins the observable number and the
docstring says the guard is not what holds it.

### F2 · "Pinned Facts are secrets" has no script-side enforcement
**Audit claim:** `context.py:44`, `dm.md:93`, `play.py:542`. "None of the four
post-draft guardrails checks whether the narration *discloses* one."
**Disposition: gap, verified. Not fixed here.**

**Verified.** A narration that hands the player both pinned facts in plain prose passes
every post-draft check the loop runs:

| Detector | Result on the disclosure |
|---|---|
| `reply.speaks_for_player` | False |
| `reply.grants_injection` | False |
| `reply.reveals_check_outcome` | False |
| `reply.states_an_unbacked_cast_result` | False |
| `reply.is_costless_failure` | False |
| `reply.unbacked_numbers` | False |

And through the real loop: the disclosure is shown to the player, exactly one DM call is
made (no rewrite), and nothing is announced on the operator's status stream. The rule is
a prompt clause in `dm.md` and nothing reads the digest to enforce it.

`tests/test_prompt_boundary_disclosure.py` asserts the absence three ways: the detector
table, the loop path, and a source check that `reply.py` still does no file I/O at all
and never names `Pinned Facts` or `DIGEST_SECTIONS`. A mitigation that adds a disclosure
check fails all three, which is the point.

**Not fixed here.** A disclosure check needs the digest, so it needs I/O in the path
that decides caught from narrated, and `reply.py`'s no-I/O property is load-bearing for
the display and the guardrail tests that import it standalone. That is an interface
decision, not a regex.

### F3 · Player text reaches the prompt with only a heading as delimiter
**Audit claim:** `context.py:391`.
**Disposition: gap, verified. Characterised, not fixed.**

`build_messages` wraps the player's line in `## Player now` and does nothing else to it.
A player who types their own `## Your task` heading therefore puts a second one in the
user message. Verified: `prompt.count("## Your task") == 2` for the fixture, with the
real heading present once.

**Why nothing goes wrong today**, which is worth stating so the finding is not
overstated: nothing downstream parses the digest. `dm.md` refers to `## Campaign` by
name in six places and the digest is rendered by the GM's own state file, so a forged
heading is text in a section rather than a redefinition of one. The exposure is that a
mitigation would have nothing to trip over.

**The mitigation is a fence, not a filter**, and `reply.scrub_injection` already
implements the shape for a different reason: the transcript is scrubbed on load, sentence
by sentence, anchored on a system word, with the bias documented as "a missed exotic
phrasing is the safe direction". A player-line fence can reuse that reasoning.

### F4 · The model-authored escalate question is concatenated into an advisor instruction
**Audit claim:** `play.py:645,659`.
**Disposition: already-shipped, mitigated. Verified with fixtures.**

`_help` (`play.py:726`) treats the DM's own question as untrusted: whitespace-collapsed
and capped at 400 characters, and never concatenated into the system message. Pinned:

- a 607-character question arrives capped, with its content intact
- it reaches the advisor in the USER message, not the system one
- an empty or whitespace-only question never reaches an advisor at all
- an identical question is asked once (`HELP_ADVISORS` = 2 advisors per ask, so the
  assertion is on the shape: a repeat adds nothing, a different question doubles it)

The advisor's own output comes back under `## Advisor notes (GM only, never read aloud)`
and `_notes_out` returns nothing unless `--show-gm-notes`, so the default session shows
a player no note body. `advisor.split_notes` keeps a transport error out of the saved
notes, which is the 2026-09-29 finding and is pinned too.

### F5 · A flagged draft reaches the advisor as untrusted evidence
**Not in the audit's MEDIUM list; added because F4's mitigation is only as good as this.**
**Disposition: already-shipped. Verified with fixtures.**

`_flagged_draft` quotes the flagged draft between `<<<FLAGGED DRAFT` and
`FLAGGED DRAFT>>>`, collapses a run of three or more angle brackets to one so the draft
cannot close its own fence, and never puts the draft in the question. Pinned: one open
and one close marker, the close after the open, and nine `<` in the input read back as
one. `GUARD_QUESTIONS["injection"]` is checked not to quote any of the payload strings a
player types, because the question is model-written.

### F6 · "58 of 319 spells lack mechanical coverage"
**Disposition: not reproducing. Three separate claims, none as stated.**

Reproduced against `systems/dnd5e/data/dnd5e_srd.json` at `4c9127b`, driving every
record through `systems/dnd5e/tactics_spells.py::resolve` with the production `_srd`:

| Quantity | Value |
|---|---|
| SRD spell records | **319** (the denominator is right) |
| records with parsed mechanics | 319 |
| resolve raises | 0 |
| `mode == "reaction"` | 1 |
| `mode == "narrate"` | 265 at caster level 1, 266 at levels 5 and 17 |
| mechanical (`mode` not in `narrate`/`reaction`) | **54** at caster level 1, **53** at levels 5 and 17 |

So:

- **"58" matches nothing.** 265 spells lack mechanical coverage at level 1 and 266 at
  level 5+, and 54 (level 1) or 53 (level 5+) have it. The roadmap figure is stale and
  its unit is unstated.
- **The coverage figure is a function of the caster**, which is the real defect in the
  number rather than the number. `BUILTIN["eldritch blast"]` sets
  `narrate_from_level: 5` because the SRD record holds one beam and the spell deals more
  above 4th, so Eldritch Blast is mechanical at level 1 and not at 5. "54 of 319" and
  "53 of 319" are both true and the difference is one spell.
- **194 of the 319 carry the `effect` flag**, which `resolve` treats as blocking. It is
  not a parsing obstacle: `build_srd` writes it when a spell has no attack, no save, no
  damage and no heal, which is a fact about the spell. Reading `effect` as "blocked"
  buries 194 rows of "this is a utility spell with nothing to roll" under a parsing
  failure.

**The instrument is not on `main`.** `scripts/rules_coverage.py` exists only on the
unmerged branch `state/rules-coverage-report` (PR #207). Its own docstring already says
that "hiding the decision is how the roadmap's 58 became unfalsifiable", and it fixes the
condition set at `caster_level: 5`. So the corrected headline, on the numbers above, is:

> At caster level 5, **53 of 319** SRD spells resolve to a mode the engine can run.
> **266** resolve to `narrate` and are GM-resolved, and **1** is a reaction the engine
> offers on its trigger. Coverage is a function of the caster and must be quoted with
> one.

No file in this change asserts any of these numbers. They belong to the coverage report
(PR #207), and this document is the pointer.

### F7 · Three condition clauses the coverage audit named
**Disposition: not reproducing. The clauses are wrong and the engine agrees with the SRD.**

Validated against `systems/dnd5e/data/dnd5e_srd.json` at `4c9127b`, the repository's own
dataset, and against `systems/dnd5e/tactics_rules.py::CONDITION_EFFECTS`:

| Clause as named | SRD text in this dataset | `CONDITION_EFFECTS` |
|---|---|---|
| poisoned restricts actions | "A poisoned creature has disadvantage on attack rolls and ability checks." Nothing about actions. | `attack_roll: dis`, `ability_check: dis`. No `action_economy`. **Correct.** |
| incapacitated forbids object interaction | "An incapacitated creature can't take actions or reactions." Nothing about objects. | `action_economy: none`. **Correct.** |
| invisible means blinded | Invisible: "For the purpose of hiding, the creature is heavily obscured... Attack rolls against the creature have disadvantage, and the creature's attack rolls have advantage." Blinded: "Attack rolls against the creature have **advantage**." | `attack_roll: adv`, `attack_against: dis`. **Correct, and the opposite of blinded.** |

The invisible/blinded confusion is the dangerous one, and worth spelling out: treating
invisible as blinded would invert the creature's attack rolls, making an invisible
creature's attacks disadvantage instead of advantage. `CONDITION_EFFECTS` already has it
right, so a correction here would be the bug.

**What is genuinely not modelled** is invisible's *hiding* clause, which is a `hide`
rule rather than a combat one, and `grid` has no heavily-obscured state. That is a
coverage gap in a different module and a different issue.

`CONDITION_EFFECTS` already marks its one house ruling (`charmed`) as such in the
module's own comment, which is the practice the audit's clause list did not follow.

---

## What this change does not cover

- The MEDIUM engine findings in the same audit (upcasting refused, Silvery Barbs rolling
  the victim's save, `reaction_used` absent from the receipt fingerprint, `scenes.load`
  returning `None` for a corrupt file, `rest short` accepted mid-combat). They are
  engine behaviour questions, not residual coverage or disclosure findings, and they
  are not this issue's scope.
- The MEDIUM test-coverage findings other than AoE (`reveal_hidden`, the scene schema
  gate, the two portrait tests that assert a machine-wide invariant, the SRD skips). The
  first two were fixed by commit `732ba20` (PR #191); the rest are separate.
- Anything in the audit's CRITICAL, HIGH or LOW sections.

## Not verified

- No live endpoint was run. Nothing here is a claim about model behaviour.
- No sealed campaign material was read. Every fixture is written in the test file or
  copied from `tests/fixtures/Kairos_Level1.md`.
- The coverage numbers in F6 were produced by driving `resolve` directly, not by
  `scripts/rules_coverage.py`, because that script is not on `main`. The two may differ
  in their `_narrate_reason` classification; the mechanical/reference split does not
  depend on it.