# Change brief: 171-context-floor

**Kind:** state
**Code repo branch:** `fix/171-context-floor`
**Issue:** `qclayssen/dnd-gm#171` (outer tracks the work; the code is engine-side)
**Spec:** `docs/specs/SPEC-dm-agent.md` D4.2
**Status:** implemented, verified, awaiting push

## 1. Implement

> **CORRECTION, added after merge.** The severity claim originally written here was
> wrong. It said a filled-in campaign at the **12000 default** hits the empty-the-list
> loops, quoting `SPEC-dm-agent.md:66-69`. Those spec lines predate **#264** ("stop
> charging the static prompt against the dynamic budget", landed `4c9127b`), which
> already removed the default-budget path. Measured on `origin/main` after #260, with a
> 4797-char digest, 8 turns and 8 canon records:
>
> | `--budget` | turns kept (old) | turns kept (with the floor) |
> |---|---|---|
> | 12000 (default) | 8 | 8 |
> | 9000 | 8 | 8 |
> | 400 | **0** | **2** |
>
> So the defect is real and reachable, but through an operator **lowering `--budget`**,
> not through the ordinary case. `--budget` is operator-settable, so this is a real
> session, not a theoretical one — the original brief said as much about reachability and
> was right about that — but calling it "the ordinary case" was an overstatement, and it
> was caused by quoting a spec section that had been superseded by a merge this change did
> not account for. The code is unaffected; only the record was wrong.

`build_messages` trimmed with `while can and spent() > budget` and
`while lines and spent() > budget`. Both loops empty their list, so whenever the budget
bites at all the DM loses the entire conversation and the entire canon — and the prompt
still assembles, still parses, and still gets a reply, from a summary of nothing.

- [x] `scripts/localdm/context.py:build_messages` — new `min_turns=2` and `min_canon=3`
  keyword arguments. The two loops stop at the floor instead of emptying. Canon is still
  dropped first: the current scene beats history.
- [x] `scripts/localdm/context.py:build_messages` — floors are clamped with `max(0, …)`.
  Unclamped, `while len(lines) > -5` would run off the end of the list.
- [x] `scripts/localdm/context.py:build_messages` — `report` gains `canon`, `canon_dropped`,
      `turns_dropped`, `min_turns`, `min_canon`, `over_budget`, `over_by`.
- [x] When the floor does not fit, the function does **not** drop below it and does **not**
      raise. It assembles and reports `over_budget`/`over_by`, so an operator seeing
      `dynamic` above `budget` can tell the floor held from the loop stopping early.
- [x] Docstring records that SPEC D4.2's digest-degrade ladder is deliberately **not**
      implemented here, because it contradicts a standing decision (the digest is never
      trimmed, since `dm.md` refers to `## Campaign` by name). Flagged rather than
      silently resolved.

## 2. Tests: what, and how

- [x] `pytest tests/test_localdm_context.py` -- 31 passed (22 before, +9).
  before fix: **FAILS 9 of 9** against the reverted `context.py` — 8 new cases
  (`KeyError: 'turns_dropped'`) plus the one existing case this changes. Nothing is
  decoration.
- [x] `pytest tests/ -q -k "localdm or context or canon or memory"` -- **498 passed,
      2 skipped**.
- [x] full suite `TACTICS_NO_DISPLAY=1 pytest tests/ -q` -- **3976 passed, 12 failed,
      23 errors, 61 skipped**.

### The full-suite failures are not ours, and are measured rather than asserted

The 12 failures and 23 errors are **identical with and without this change** (measured by
stashing the diff and re-running the whole suite):

| | passed | failed | errors |
|---|---|---|---|
| clean `origin/main` | 3968 | 12 | 23 |
| with this change | 3976 | 12 | 23 |

+8 passed is exactly the new tests. Cause: `systems/dnd5e/data/dnd5e_srd.json` is
**gitignored generated output**, so a fresh clone starts without it. The failures are in
`test_export_bestiary.py` and `test_srd_fixture_isolation.py`, and their own messages say
`dnd5e_srd.json is absent`. Not caused by #171 and not fixed here.

## 3. Advisors required

`change_brief.py advisors state` -> `engineer`, `verifier`, `steward`, `architect`

| `engineer` | consulted | does this already exist; which function actually changes |
| `verifier` | consulted | does each test do anything on the pre-fix code |
| `steward` | consulted | repo/process hazards; two repos, a worktree and a gitlink |
| `architect` | consulted | is the abstraction earned; state authority boundary |

## 4. Advisors consulted

| Advisor | Verdict | Note |
| --- | --- | --- |
| engineer | concurs, with a scope cut | The floor is the feature; the digest-degrade ladder that precedes it in D4.2 is **not** implemented, because `build_messages`' own docstring records a standing decision that the digest is never trimmed (`dm.md` refers to it by name) and D4.2 asks to trim it to 1000/1800/2000. Those two cannot both hold. The floor is the part that fixes the reported defect; the conflict is recorded in the docstring for a separate change rather than resolved here. |
| verifier | concurs, and caught three bad tests of mine | All 9 verified to fail against the reverted source. Three of my own new tests were wrong first and were corrected rather than weakened: one asserted `over_by == dynamic - budget`, which are two different measurements (assembled parts vs the joined string — they differ by the separators); one used a digest so large that both floors were hit at once, so it could not observe the canon-before-turns ordering it claimed to test; and one asserted a resumed session would still be over budget when it in fact fits. Numbers for the ordering case were then measured rather than guessed (budget 1800 → `canon_dropped=3, turns_dropped=0`). |
| steward | concurs | Engine repo, worktree off engine `origin/main` at `8d8157b`. Outer `scripts/localdm/play.py` is **claimed by another run** (`codex-worker-301`), so the `_dm.call` logging half of D4 item 1 is deliberately not in this PR; `context.py` was claimed and worked alone. |
| architect | no change wanted, with one note | Considered raising the 12000 default instead. Rejected: `SPEC-dm-agent.md:293-295` lists "any appetite for changing the 12000-char default" as an **open question for phase 1**, so changing it now would answer a question this evidence was gathered to inform. Also did not add a `report` schema version; the dict is already additive and `update()`-based. |

## 5. Gates before merge

### Before PR

- [x] `pytest tests/` run in the worktree; full-suite failures proven identical to baseline
- [x] own diff read and reviewed

### Before merge

- [x] PR is OPEN, base `main`, and head is the reviewed SHA

Verified after enqueue, before the gate: engine PR #260 reports `state=OPEN`,
`base=main`, `head=2c0afdd1bb185b8342ef37110aff0eaa926ca52a`, byte-identical to local
reviewed HEAD.

CI green on this PR: `pytest` pass, `python 3.10 floor (engine glob)` pass, `eslint
(advisory)` pass. Note that the engine's CI **provisions the SRD dataset**, which is why
its `pytest` is green where the local full-suite run had 12 failures and 23 errors — that
part of the brief's evidence is about the local clone, not about this PR.

## 6. Follow-ups

- [ ] **D4 item 1's second half is not done**: `_dm.call` should log one row per DM call to
  `localdm/context.jsonl`, and `usage.jsonl`'s real `prompt_tokens` gives the measurable
  trigger. That is in `scripts/localdm/play.py`, which another run holds a claim on.
- [ ] **D4 item 2's degrade ladder conflicts with a standing decision.** SPEC asks for
  notes 2500→1000, sheet 3000→1800, state digest 3000→2000 before dropping anything;
  `build_messages`' docstring says the digest is never trimmed because `dm.md` refers to
  `## Campaign` by name and a truncated digest breaks the prompt. One of the two has to
  give, and it is a product decision, not an implementation detail.
- [ ] The 12000-char default is unchanged, deliberately: `SPEC-dm-agent.md:293-295` lists it
  as an open question for phase 1, to be answered with real numbers.
- [ ] `context.py:167` claims `is_template_line` strips `world.py`'s GM-only markers. It
  does not — those markers reach the DM prompt today. Found while reading this code for
  #171; belongs with the documented-falsehood sweep, not here.