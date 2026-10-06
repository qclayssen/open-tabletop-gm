# Change brief: 277-locale-floor

**Kind:** bug
**Code repo branch:** `fix/277-locale-floor`
**Issues:** qclayssen/open-tabletop-gm#277 (locale job), #252 (idle-timer flake)
**Status:** implemented, verified, pushed

## 1. Implement

- [x] `scripts/{combat,dice,tracker,world,gm_graph,calendar,npc_rename,dice_player,path_config,update_skill,name_registry,provision_srd}.py`
      -- six-line entry-point guard reconfiguring stdout/stderr to
      `encoding="utf-8", errors="replace"`. 35 offending `print()` lines across
      10 files, measured by the new detector itself.
- [x] `tests/test_encoding_utf8.py` -- a THIRD detector, for stdout. The existing
      two (bare `open()`, and `-X warn_default_encoding`) cannot see stdout.
- [x] `tests/test_encoding_utf8.py` -- behavioural companion running two
      documented invocations under `LC_ALL=C`.
- [x] `.github/workflows/tests.yml` -- the `non-utf8-locale` job, with two guard
      steps before anything depends on the environment.
- [x] `tests/test_display_narration_retention.py` -- `timer_budget()` deriving the
      waits from the page's own `charDelay` and `IDLE_GAP`, replacing a
      hand-typed `timeout=20000` (#252).

## 2. Tests: what, and how

- [x] `pytest tests/test_encoding_utf8.py::test_no_cli_prints_non_ascii_without_reconfiguring_its_streams`
      -- asserts: no script with a `__main__` prints non-ASCII un-reconfigured ;
      before fix: FAILS with 35 offenders in 10 files
- [x] `pytest tests/test_encoding_utf8.py::test_a_documented_cli_invocation_survives_a_non_utf8_console`
      -- asserts: `combat.py` and `dice.py` exit 0 under `LC_ALL=C` ; before fix:
      FAILS, `UnicodeEncodeError: 'ascii' codec can't encode character '\u2014'`
- [x] `pytest tests/test_display_narration_retention.py::TimerBudget::test_more_characters_buy_a_larger_budget`
      -- asserts: the budget scales with character count ; before fix: FAILS,
      `timer_budget` did not exist
- [x] `pytest tests/test_display_narration_retention.py::BoundedFailure`
      -- asserts: a never-true predicate still raises, in bounded time ; the
      counterweight to a raised budget
- [x] MUTANTS: `present()` swallows the timeout -> 1 failed ; `timer_budget`
      ignores the page -> 2 failed ; detector skips on the WORD "reconfigure"
      rather than the call -> 1 failed (that one was found by mutation, in my own
      detector, and is why the guard text is now matched literally)
- [x] full suite under `LC_ALL=C PYTHONUTF8=0 PYTHONCOERCECLOCALE=0`, twice:
      **4108 passed / 61 skipped / 0 failed**, then **4111 passed / 61 skipped /
      0 failed**

## 3. Advisors required

`change_brief.py advisors bug` -> `engineer`, `verifier`, `steward`

## 4. Advisors consulted

| Advisor | Verdict | Note |
| --- | --- | --- |
| `engineer` | consulted | The fix belongs at the ENTRY POINT, not in the strings. A missing glyph is a cosmetic degradation; a traceback on line one is a provisioning failure that reads as broken data. `errors="replace"` is what makes that trade honest. |
| `verifier` | **found two false passes, both mine** | (1) The new detector skipped any file whose source merely CONTAINED the word "reconfigure" -- deleting the real call left my own comment behind and the mutant passed. Now matches the guard text. (2) `delay, gap = page.evaluate(...)` on a two-key dict yields the KEYS, and `chars * "delay"` is silent string repetition, so `int()` raised thousands of characters from the cause. |
| `steward` | consulted | #252's owner had held three locks for 27h with no process, no remote branch and no issue activity; the evidence is in the commit and the refs were cleared before claiming. #290's own 6f76b27 is the parent. |

## 5. Gates before merge

### Before PR

- [x] suite green under the locale AND under the default environment
- [x] collected-test count measured with `--collect-only`, not inferred
- [x] own diff read and reviewed

### Before merge

- [x] PR is OPEN, base `main`, head `2cba2f2`
