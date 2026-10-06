# Change brief: 286-named-specialist

**Kind:** prompt
**Code repo branch:** `prompt/286-named-specialist`
**Status:** draft

Fill every section. `scripts/change_brief.py check /Users/quentinclayssen/github/dnd-gm/.claude/briefs/286-named-specialist.md` is the gate and it is
not optional: it is what stops a skipped step from being reported as a done one.

## 1. Implement

One line per thing to build or change. Name the file and the function, not the
feeling. If it turns out to already exist, that is a finding, not a waste.

- [x] `scripts/localdm/advisor.py:NAMEABLE` -- NEW. Five of nine advisors the DM
      may name. `arbiter` is guardrail-only; `interface`/`referee`/`mascot-handler`
      fire on what the table is doing, not what the DM wants.
- [x] `scripts/localdm/reply.py:split_escalate` -- NEW. Splits the specialist prefix
      off `escalate`. Extraction is over `ADVISORS`, NOT `NAMEABLE`, so a
      real-but-unnameable name reaches `_help` and can be refused OUT LOUD.
      Accepts the object shape, as `_cast_field` already does for `cast`.
- [x] `scripts/localdm/reply.py:DMReply.escalate_to` -- NEW field, 7th positional.
- [x] `scripts/localdm/reply.py:parse` -- applies `split_escalate` once so every
      downstream caller sees a plain question. Absent/blank escalate stays `None`;
      `_CUT_JSON`/`_LAST_OBJECT` untouched.
- [x] `scripts/localdm/play.py:_help(question, to=None)` -- the name LEADS with
      `pick()` behind it as a cross-check, capped at `HELP_ADVISORS`. Unknown name
      -> status line, fall back to `pick()`. Repetition key is the question ALONE.
      `MAX_ASKS_PER_SESSION = 12` ceiling.
- [x] `scripts/localdm/play.py` call site -- `_help(r.escalate, r.escalate_to)`.
- [x] `scripts/localdm/prompts/dm.md` -- one sentence listing the five; +40 tokens.

## 2. Tests: what, and how

Each entry needs a command that can be run by someone else, and a line saying
what the test does on the pre-fix code. A test that passes on the broken version
is decoration (`agents/verifier.md`).

- [x] `pytest tests/test_localdm_play.py::test_a_name_pick_would_not_have_chosen_is_still_asked`
      -- asserts: an advisor `pick()` cannot choose IS consulted, so the test
      cannot pass without the feature ; before fix: FAILS, no advisor consulted
- [x] `pytest tests/test_localdm_play.py::test_the_same_question_to_a_different_specialist_is_still_repetition`
      -- asserts: naming another specialist does not bypass the bound ; before fix:
      FAILS -- the second ask goes out
- [x] `pytest tests/test_localdm_play.py::test_a_real_but_unnameable_name_falls_back_to_routing_out_loud`
      -- asserts: the ask happens by topic AND the refusal is announced ; before fix: FAILS
- [x] `pytest tests/test_localdm_play.py::test_the_session_ceiling_stops_a_model_that_asks_every_turn`
      -- asserts: advisor calls stay within the ceiling ; before fix: FAILS
- [x] `pytest tests/test_localdm_play.py::test_the_question_is_still_capped_and_unprefixed_before_the_advisor_sees_it`
      -- asserts: the advisor receives the BARE question, capped ; before fix: FAILS
- [x] `pytest tests/test_localdm_reply.py::test_a_real_but_unnameable_advisor_is_extracted_so_the_refusal_is_audible`
      -- asserts: the name survives to the layer that refuses it ; before fix: FAILS
- [x] `pytest tests/test_localdm_reply.py::test_an_absent_or_blank_escalate_stays_none_not_empty_string`
      -- asserts: `None`, not `""` ; before fix: FAILS on 3 counts
- [x] `pytest tests/test_localdm_advisor.py::test_nameable_is_the_five_the_dm_prompt_offers`
      -- asserts: prompt and code offer the same five ; before fix: FAILS
- [x] SIX MUTANTS, each verified to fail: name not honoured 2 | repetition key has
      the name 1 | ceiling removed 1 | refusal silent 4 | cost cap removed 2 |
      prefix never parsed 18 | restored 239 passed
- [x] full suite green in the worktree: **4142 passed, 61 skipped, 0 failed**

## 3. Advisors required

`change_brief.py advisors prompt` -> `engineer`, `verifier`, `steward`, `security`, `arbiter`

These are required, not suggestions. They are also NOT in `/advise`: they are
unregistered briefs read from `agents/`, consulted directly during the cycle.

| `engineer` | consulted | does this already exist; which function actually changes |
| `verifier` | consulted | does each test do anything on the pre-fix code |
| `steward` | consulted | repo/process hazards; two repos, a worktree and a gitlink |
| `security` | consulted | reachability of player text into the DM context |
| `arbiter` | consulted | dice and rules adjudication, and the prompt guard |

## 4. Advisors consulted

Fill as you go, with the actual verdict. An advisor that was asked and had
nothing to add is a real result, say so rather than leaving the box open.

| Advisor | Verdict | Note |
| --- | --- | --- |
| `engineer` | consulted | The load-bearing choice is a PREFIX on the existing key, not a fifth JSON key: at 9B an extra key is mangled or omitted, and every key is prompt weight on the always-loaded prompt. `_CUT_JSON`/`_LAST_OBJECT` therefore stay untouched, which is also what keeps a cut-off reply behaving exactly as it does today. |
| `verifier` | **found four defects, two of them in my own tests** | (1) Absent `escalate` coerced to `""` broke six pre-existing reply tests -- it is typed `str | None` and callers test `is None`. (2) The regex required a character after the colon, so `"historian:"` never matched and the empty-remainder branch below it was UNREACHABLE; the model would have asked an empty question. (3) I asserted a prefix survives a TRUNCATED JSON line -- it does not, on pristine main either, so that test was about a behaviour that never existed. (4) `assert _asked_advisors(c)[0] == ...` FLAKED 25% of the time in ISOLATION: `_ask` fans advisors out in parallel, so `roles()` is completion order and the assertion was on thread scheduling. Rewritten to assert an advisor `pick()` cannot choose IS consulted -- deterministic, load-bearing, and impossible to pass without the feature. Eight consecutive clean runs after. |
| `security` | consulted | The parse sits on untrusted model output, so two things matter. The regex length bound plus `ADVISORS` membership is what stops "the tavern: something happened" being read as a name, and a name with no remainder is dropped rather than routed. And the *question* stays capped at 400 and prefixed away before the advisor sees it -- the name is routing, not content, so the advisor gets the bare text. Pinned by a flood test that measures the segment actually delivered. |
| `arbiter` | not consulted | No rules question. This routes a question to a lore/narrative advisor and decides nothing about dice, damage or the action economy; `arbiter` remains guardrail-only and is pinned as UNNAMEABLE. Saying so rather than claiming a verdict it did not give. |
| `steward` | consulted | Four source files and three test files claimed before any edit. Released my own stale `opencode-t52` claim on `play.py` first -- same agent, two owner ids, which reads as a conflict to the tool and as nothing to a human. Also caught and fixed a branch named after engine #253, which is an unrelated merged issue. |

## 5. Gates before merge

### Before PR

- [x] full suite green in the worktree: **4142 passed, 61 skipped, 0 failed**
- [x] own diff read and reviewed
- [x] collected-test count, measured with `--collect-only` in a detached worktree of
      each: clean `origin/main` = 4169, this branch = 4203. **+34, 0 removed.** Measured
      rather than inferred -- "N added tests" and "+N net" are different claims, and
      this branch also edits existing test files.
- [x] brief named for the branch slug from the start (`286-named-specialist.md`),
      which is what the merge-queue gate derives the brief path from -- the third
      time this session

### Before merge

- [x] PR is OPEN, base `main`, head `d09c319`; tracking issue #286; PR **#287** (replaces #285, auto-closed when the mis-named branch was deleted)

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

<!-- files this kind usually lands in: scripts/localdm/prompts/, play.py -->
