# Change brief: player-view

**Kind:** ui
**Code repo branch:** `ui/player-view`
**Status:** draft

Fill every section. `scripts/change_brief.py check /Users/quentinclayssen/github/dnd-gm/.claude/briefs/player-view.md` is the gate and it is
not optional: it is what stops a skipped step from being reported as a done one.

## 1. Implement

One line per thing to build or change. Name the file and the function, not the
feeling. If it turns out to already exist, that is a finding, not a waste.

- [x] `display/static/player-view.js` (NEW) -- the entire permitted-surface
      policy: `PLAYER_SURFACES` / `OPERATOR_SURFACES` / `OPERATOR_ATTRS`,
      `applyTo`, `sweep`, `activate`, `deactivate`. Exports
      `window.GMPlayerView`.
- [x] `display/static/player-view.js:activate` -- adds `body.player-view` and
      installs a `MutationObserver` over `{childList, subtree}`; load-bearing
      because `tactics.js` is `defer`red and builds `#tx-panel` afterwards.
- [x] `display/static/player-view.js:boot` -- inert unless `?view=player`.
- [x] `display/templates/index.html` -- load `player-view.js` with `defer`,
      declared BEFORE `tactics.js` so the observer is armed first.
- [x] `display/static/display.css` -- `body.player-view` rules: hide the
      operator panels, the scroll/speed/theme controls and the tactical
      command controls. Layout + hiding only; it decides nothing.

## 2. Tests: what, and how

Each entry needs a command that can be run by someone else, and a line saying
what the test does on the pre-fix code. A test that passes on the broken version
is decoration (`agents/verifier.md`).

- [x] `pytest tests/test_player_view.py::test_no_surface_is_unclassified` --
      asserts: every surface id in index.html and every `tx-*` id tactics.js
      builds is classified by one policy set ; before fix: FAILS with 25
      unclassified surfaces on first run.
- [x] `pytest tests/test_player_view.py::test_every_live_dom_surface_is_classified_by_the_policy`
      -- asserts: the same over the RENDERED DOM ; before fix: FAILS, catching
      `#phone-mode-btn` / `#phone-mode-menu`, which are runtime-injected and
      exist in no source file.
- [x] `pytest tests/test_player_view.py::test_tx_info_is_not_suppressed` --
      asserts: `tx-info` stays player-facing ; before fix: FAILS, and hiding it
      would cost a player the death-save prompt.
- [x] `pytest tests/test_player_view.py::test_a_mutation_observer_guards_late_built_dom`
      -- asserts: the observer covers `childList`+`subtree` ; before fix: FAILS,
      and without it the whole tactical toolbar shows on the player screen.
- [x] `pytest tests/test_player_view_browser.py` (7 tests, real chromium) --
      asserts: both presentations render, no operator surface visible, ordinary
      display untouched, `deactivate()` restores. Read the policy from
      `window.GMPlayerView` at runtime so the two lists cannot drift.
- [x] MUTATION, verified not assumed: deleting `world-clock` from
      OPERATOR_SURFACES -> 2 failures. Deleting `phone-mode-btn` -> 1 failure
      (browser DOM test only). Both re-run and confirmed.
- [x] full suite `python3 -m pytest tests/ -q` after
      `python3 scripts/provision_srd.py` -- regression only. Baseline note: on
      pristine `origin/main` (a89fe0a) WITHOUT provisioning the suite shows 7
      failed / 21 errors in test_monster_defenses, test_rules_coverage,
      test_srd_provisioning, test_export_bestiary, test_srd_fixture_isolation --
      all SRD-fixture, none mine. Provisioned run is the comparison.

## 3. Advisors required

`change_brief.py advisors ui` -> `engineer`, `verifier`, `steward`, `interface`

These are required, not suggestions. They are also NOT in `/advise`: they are
unregistered briefs read from `agents/`, consulted directly during the cycle.

| `engineer` | consulted | does this already exist; which function actually changes |
| `verifier` | consulted | does each test do anything on the pre-fix code |
| `steward` | consulted | repo/process hazards; two repos, a worktree and a gitlink |
| `interface` | consulted | what the player actually sees |

## 4. Advisors consulted

Fill as you go, with the actual verdict. An advisor that was asked and had
nothing to add is a real result, say so rather than leaving the box open.

| Advisor | Verdict | Note |
| --- | --- | --- |
| `engineer` | NEW FILE, deliberate | `?view=input` exists (display.js) but `?view=player` did not. Putting the policy in a new module rather than display.js is the decision: a 4,400-line file holding the rule is not one policy. Side benefit: `display.js` is under a live claim by `codex-worker-252-20261005-a`, and this change needs no edit to it. |
| `verifier` | **found a false pass, twice** | The browser test originally iterated `window.GMPlayerView.OPERATOR_SURFACES`. Deleting an entry from that set removes it from the loop, so the test could not detect the policy losing an entry -- it passed against a policy with `world-clock` deleted. It also passed a *visibility* check, because display.css hid those elements anyway: redundant CSS masked a broken policy. Fixed by iterating the live DOM and asserting on the policy's decision (`data-pv`). Both mutations now fail. |
| `steward` | claims checked, one left alone | `display.js` is claimed by `codex-worker-252-20261005-a`. Liveness was ambiguous (lock ref has no heartbeat; live Codex processes exist; #252 has activity from the previous day), so the claim was **not** stolen -- the cost of waiting is small and I have destroyed a live run's work once already. Avoided entirely by not touching display.js. `.kiro/.../tasks.md` is concurrently edited by another run and was edited in place, never staged from the primary checkout. |
| `interface` | n/a this cycle | The Interface Designer is a *play* council advisor and was not consulted; the brief requires the dev council's `interface`. Not consulted in this pass rather than claiming a verdict it did not give. |

## 5. Gates before merge

### Before PR

- [x] full suite green in the worktree, not the primary checkout -- provisioned run
- [x] collected-test count matches clean `origin/main` (no silent deletions):
      base 7305ec5 = 4109 collected, this branch = 4133. +23 new (16 static +
      7 browser) plus +1 inherited from #268, which this branch rebased onto.
      Verified with `--collect-only` in a detached worktree of each, not assumed.
- [x] own diff read and reviewed

### Before merge

- [x] PR is OPEN, base `main`, and head is the reviewed SHA -- #272, head 5200181

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

<!-- files this kind usually lands in: display/ -->
