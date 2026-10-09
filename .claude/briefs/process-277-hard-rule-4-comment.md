# Change brief: correct CLAUDE.md hard rule 4, which quotes a comment that no longer exists

**Kind:** process
**Code repo branch:** `process/277-hard-rule-4-comment`
**Status:** ready

## 1. Implement

One line per thing to build or change. Name the file and the function, not the
feeling. If it turns out to already exist, that is a finding, not a waste.

- [ ] `CLAUDE.md` hard rule 4, the Windows half — the block starting "The Windows half of this rule is deliberately not checked" is **replaced**. It currently says `2d6f582` "recorded the reason in the workflow itself" and then quotes that reason. **It is not in the workflow.** `git grep -i 'not a gap to fix helpfully' origin/main -- .github/` returns nothing. The comment lived inside `strategy.matrix:` and died with the matrix in `6e78d4d chore(ci): remove the tests workflow`, which removed the workflow entirely; the file was rebuilt afterwards and now reads "One job, one OS, one Python. This is a pre-push signal, not a matrix."
- [ ] `CLAUDE.md` hard rule 4 — attribute the surviving reasoning to `2d6f582`'s **own commit message**, which is where it now lives, and state in the file that the workflow comment is gone. A reader who trusts the quote will `grep` for it, fail, and conclude the rule was never decided.
- [ ] `CLAUDE.md` hard rule 4 — append the 2026-10-06 supersession: the **locale** half was reinstated deliberately as PR **#275** (`ci/non-utf8-locale-floor`), and on its first run it caught `systems/dnd5e/build_srd.py:1362` printing `── ` box-drawing characters and dying with `UnicodeEncodeError: 'ascii' codec can't encode characters in position 0-1`. **`windows-latest` is still not reinstated** and remains a separate decision. Without this the rule reads as wholly unchecked when half of it is now enforced and has already caught a real defect.

**Out of scope, and why.** Do **not** fix `build_srd.py`. It is already fixed on the #275 branch; this brief is a records correction in the same area, and merging an unrelated engine change behind a docs PR is how a review stops being reviewable. Do **not** reinstate `windows-latest`.

### The replacement text, verbatim

Apply this to `CLAUDE.md` hard rule 4, replacing the current Windows-half block. It was drafted against `origin/main` `0060e3c` and **re-verify the three quoted facts before committing** — if any has moved, the text moves with it, and that re-check is the point of the change.

> - **The Windows half of this rule was deliberately not checked, and that was a
>   recorded decision rather than an oversight.** `2d6f582` (#103) dropped the
>   ubuntu and windows matrix legs and the non-UTF-8 locale job. **The comment
>   recording the reason did not survive**: it lived inside `strategy.matrix:` and
>   died with the matrix in `6e78d4d chore(ci): remove the tests workflow`. The
>   reasoning now lives only in `2d6f582`'s own commit message — *"a UTF-8 default
>   is all a macOS runner ever hands you, so a cp936 or cp1251 console can now
>   regress uncaught. That was accepted knowingly"* — and here. **Do not restore it
>   as a drive-by fix; it needs its own decision and its own brief.**
>   **Superseded 2026-10-06:** the locale half was reinstated on purpose as
>   `open-tabletop-gm#275` (`ci/non-utf8-locale-floor`), and on its first run it
>   caught `systems/dnd5e/build_srd.py:1362` printing `── ` box-drawing characters
>   and dying with `UnicodeEncodeError` under a non-UTF-8 stdout. **`windows-latest`
>   itself is still not reinstated** — that remains a separate decision.

**Note the tense change**, which is not cosmetic. The current text says the half "is deliberately
not checked"; the replacement says it "**was** deliberately not checked" and then records what
changed on 2026-10-06. A rule that has been partly reinstated must not keep reading as wholly
unenforced, or the next reader discounts the whole of it.

## 2. Tests: what, and how

Each entry needs a command that can be run by someone else, and a line saying
what the test does on the pre-fix code. A test that passes on the broken version
is decoration (`agents/verifier.md`).

- [ ] `git grep -i 'not a gap to fix helpfully' origin/main -- .github/` — **returns nothing.** This is the falsifier for the current text, run against the base commit. It is the whole reason the change exists, and it is a command rather than a test because the defect is a false statement in prose.
- [ ] `git -C . show origin/main:.github/workflows/tests.yml | grep -n 'One job, one OS, one Python'` — prints line 29. Establishes what replaced the matrix, so the new text cites a file state that exists rather than describing the removal in the abstract.
- [ ] `git show 2d6f582 --format=%B -s` — prints the commit message the new text now attributes the reasoning to. If the quote is not in this output, the new text is wrong in the same way the old text was.
- [ ] full suite `PYTHONPATH=. pytest` — regression only, no new case. **State whether `systems/dnd5e/data/dnd5e_srd.json` was present.** It is gitignored generated output; absent, a full run reports ~12 failed / 23 errors on a pristine `origin/main`, and that ambiguity is what produced the "the suite is broken" audits. Provision with `python3 scripts/provision_srd.py` first.

**There is no `before fix: FAILS` to claim here, and inventing one would be worse than saying so.** A documentation change has no behavioural assertion. The three commands above are the substitute: each is falsifiable against the base, and two of the three fail on the current text.

## 3. Advisors required

`change_brief.py advisors process` -> `engineer`, `verifier`, `steward`, `architect`

These are required, not suggestions. They are also NOT in `/advise`: they are
unregistered briefs read from `agents/`, consulted directly during the cycle.

| `engineer` | consulted | does this already exist; which function actually changes |
| `verifier` | consulted | does each test do anything on the pre-fix code |
| `steward` | consulted | repo/process hazards; two repos, a worktree and a gitlink |
| `architect` | consulted | is the abstraction earned; state authority boundary |

## 4. Advisors consulted

Fill as you go, with the actual verdict. An advisor that was asked and had
nothing to add is a real result, say so rather than leaving the box open.

| Advisor | Verdict | Note |
| --- | --- | --- |
| `steward` | **found it** | Read `open-tabletop-gm/CLAUDE.md:28-35` and found it attributes a quote to a file that does not contain it. Verdict: *"A commit that cites a comment in order to justify overriding it should not cite a comment that no longer exists."* Independently confirmed the workflow comment is gone. Also found the same false citation duplicated in the outer repo's `docs/guides/ci.md:105-113` — **that half is already fixed and committed** (outer `19ebb33`), so this brief is the engine half of one finding. |
| `verifier` | **scoped the real gap** | The interesting part of T5.6 was never the runner. `tests/test_encoding_utf8.py` holds two **platform-independent** detectors — a static AST sweep for bare `open()`, and a live `-X warn_default_encoding` detector — and the file passes `7 passed, zero skips` on macOS today. By mutation: an `io.open()` on an *executed* path **passes** the static guard but **is caught** by the armed live detector. Verdict: *"If a check's result is invariant to the runner, it did not need a second runner; it needed to be armed."* So the rule's "not checked" half was partly wrong before #275: the encoding class was reachable, and `windows-latest` uniquely covers `os.name` path semantics, CRLF and drive letters — a different gap, which is why it stays a separate decision. |
| `engineer` | **scoped the supersession** | Confirmed #275's locale job is live and red for a real reason: steps 5 (*"Assert the locale really is non-UTF-8"*) and 6 (*"Prove the assertion can fail"*) both **passed**, then step 8 failed on `build_srd.py:1362`. An AST sweep of `origin/main` found **31 `print()` calls carrying non-ASCII text across 15 files** — `display/send.py`, `display/tts.py`, `display/setup_tls.py`, `scripts/calendar.py:192`, `probe/*`, 3 in `build_srd.py` — each a crash on a cp1251/cp936 console. Verdict: keep #275 and this correction in **separate** commits; do not bundle the `build_srd.py` fix here. |
| `architect` | nothing to add | No abstraction, no state authority, no coupling at issue. This is a false statement in prose, and the only architectural question — whether the rule's two halves should be one rule or two — is answered by the fact that they have different coverage and different reinstate costs. Recorded as no finding rather than left open. |

## 5. Gates before merge

### Before PR

- [ ] `PYTHONPATH=. pytest` green in the worktree, not the primary checkout
- [ ] collected-test count matches clean `origin/main` (no silent deletions)
- [ ] own diff read and reviewed

### Before merge

- [ ] PR is OPEN, base `main`, and head is the reviewed SHA

After enqueue and verifying that PR-state assertion, run
`merge_queue.py gate <PR> --sha HEAD` immediately before merging. It independently
checks queue position, PR state, base, head repository, conflicts, reviewed SHA,
and this brief. After merge, `merge_queue.py landed <PR>` must print `landed:`
before the cycle reports success. Record command outcomes after they run; neither
is a prerequisite checkbox.

## 6. Follow-ups

Anything deliberately not done, and where it is tracked. This section is how a
brief stops being a lie: an unchecked box with no follow-up is a silent drop.

- [ ] **`build_srd.py`'s other 30 non-ASCII `print()` calls.** The #275 locale job fixes `build_srd.py:1362` and goes green, but the same crash class remains in `display/send.py`, `display/tts.py`, `display/setup_tls.py`, `scripts/calendar.py:192` and `probe/*`. The reinstated job will catch them one at a time. **File an engine issue when #275 merges**, or the queue will serialise them as a series of red builds.
- [ ] **`windows-latest` is still undecided** and stays that way — CRLF, `os.name` path semantics (`campaign_lint.py:454`), drive letters. Nothing tests those now. A separate decision with a separate brief, which is what this rule has always demanded.
- [ ] **Outer `AGENTS.md` hard rule 4** stated the requirement unqualified while this file qualified it — the outer file is the one a reader hits first. Corrected and committed in the outer repo (`fb0e239`). This brief does not need to touch it.
