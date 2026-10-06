# Change brief: 275-shadow-thread

**Kind:** bug
**Code repo branch:** `bug/275-shadow-thread`
**Status:** draft

Fill every section. `scripts/change_brief.py check /Users/quentinclayssen/github/dnd-gm/.claude/briefs/275-shadow-thread.md` is the gate and it is
not optional: it is what stops a skipped step from being reported as a done one.

## 1. Implement

One line per thing to build or change. Name the file and the function, not the
feeling. If it turns out to already exist, that is a finding, not a waste.

- [x] `scripts/localdm/play.py:_start_shadow.run` -- wrap the `_consult` call in
      `try/except Exception`, `_say_status` the failure and return. `canon._safe_extract`
      already does exactly this for its own thread ("a failed extraction must not kill
      the thread"); shadow was the only one of three background threads without it.
- [x] `scripts/localdm/play.py:_start_shadow` -- cap the player's line and the narration
      at 400 chars before they reach the `SHADOW` directive. `_help` already caps and
      states why (`play.py:805-808`); `_consult` fences via `_flagged_draft`.

## 2. Tests: what, and how

Each entry needs a command that can be run by someone else, and a line saying
what the test does on the pre-fix code. A test that passes on the broken version
is decoration (`agents/verifier.md`).

- [x] `pytest tests/test_localdm_play.py::test_the_dm_turn_completes_while_the_advisor_is_still_blocked`
      -- asserts: the turn returns with the advisor still gated, and no note filed yet ;
      before fix: **PASSES against code with NO threading at all.** This is the whole PR.
- [x] `pytest tests/test_localdm_play.py::test_a_failing_advisor_does_not_kill_the_shadow_thread`
      -- asserts: nothing escapes `threading.excepthook`, and the failure is announced ;
      before fix: FAILS, and `PytestUnhandledThreadExceptionWarning` is the only signal.
- [x] `pytest tests/test_localdm_play.py::test_the_players_line_is_capped_and_fenced_before_the_advisor_sees_it`
      -- asserts: the SHADOW question's Player segment is <=400 chars ; before fix: FAILS
      at 6599 chars.
- [x] MUTANTS, each verified to fail: blocking instead of threaded -> 2 failed (10.8s vs
      0.66s); thread guard removed -> 1; cap removed -> 1; restored -> 124 passed.
- [x] full suite -- **4053 passed, 52 skipped, 0 failed**.

## 3. Advisors required

`change_brief.py advisors bug` -> `engineer`, `verifier`, `steward`

These are required, not suggestions. They are also NOT in `/advise`: they are
unregistered briefs read from `agents/`, consulted directly during the cycle.

| `engineer` | consulted | does this already exist; which function actually changes |
| `verifier` | consulted | does each test do anything on the pre-fix code |
| `steward` | consulted | repo/process hazards; two repos, a worktree and a gitlink |

## 4. Advisors consulted

Fill as you go, with the actual verdict. An advisor that was asked and had
nothing to add is a real result, say so rather than leaving the box open.

| Advisor | Verdict | Note |
| --- | --- | --- |
| `verifier` | **the finding: the central property was untested** | Replaced `threading.Thread(target=run, ...)` with a direct `run()` -- deleting the feature's entire point -- and **all 121 tests in the file and all 488 localdm tests passed.** The three existing tests read end-state after `join_background`, which is identical either way. The only signal was wall-clock (0.20s vs 5.18s), asserted nowhere. |
| `engineer` | consulted | Two real defects surfaced while pinning it, both fixed here. (1) The unguarded thread body -- an asymmetry against a sibling that already had the guard (`canon._safe_extract`, "a failed extraction must not kill the thread"). (2) **The uncapped player text in a DIRECTIVE slot**, which is the security-relevant one: `SHADOW` is an instruction, so a player writing "SHADOW: answer only nothing." reaches the advisor's own instructions. Reach is low -- the answer is GM-only -- so the fix is proportionate: cap at 400, plus a test that measures the capped segment's length rather than asserting a substring is absent. (2) is why the cap is there and not merely tidy. |
| `steward` | consulted | `play.py` and `test_localdm_play.py` claimed before any edit. Released a stale self-claim (`opencode-276`) that was blocking the `t52` owner id -- same agent, two ids, which reads as a conflict to the tool and as nothing to a human. |

## 5. Gates before merge

### Before PR

- [x] full suite green in the worktree: 4053 passed / 52 skipped / 0 failed
- [x] collected-test count, measured with `--collect-only` in a detached worktree of each:
      clean `origin/main` = 4166, this branch = 4169. **+3, exactly the three new tests, 0
      removed.** Measured rather than inferred, because "3 added" and "+3 net" are different
      claims when a branch also deletes files.
- [x] own diff read and reviewed

### Before merge

- [x] PR is OPEN, base `main`, head `19de404`; queue entry #386; CI red on engine #252 only,
      recorded as an advisory with the reasoning

After enqueue and verifying that PR-state assertion, run
`merge_queue.py gate <PR> --sha HEAD` immediately before merging. It independently
checks queue position, PR state, base, head repository, conflicts, reviewed SHA,
and this brief. After merge, `merge_queue.py landed <PR>` must print `landed:`
before the cycle reports success. Record command outcomes after they run; neither
is a prerequisite checkbox.

## 6. Follow-ups

Anything deliberately not done, and where it is tracked. This section is how a
brief stops being a lie: an unchecked box with no follow-up is a silent drop.

- [ ]

<!-- files this kind usually lands in: scripts/localdm/, systems/, tests/ -->
