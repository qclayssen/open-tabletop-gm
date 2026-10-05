# Change brief: pin-client-deletion

**Kind:** ui
**Code repo branch:** `chore/118-t55-delete-dead-pins`
**Issue:** outer-repo T5.5
**Status:** implemented, verified, pushed

## 1. Implement

- [x] `display/static/tactics.js` -- delete `drawPins`, `pinAt`, `openPin`,
      `showNotePanel`, `closeNotePanel`, `ui.pinLayer`, `ui.note`, and the
      geometry helpers `PIN_INSET`/`PIN_R`, `pinCentre`, `pinGlyph`,
      `pinPath`, `pinLabelAnchor`, `pinLabel`, `pinOnBoard`.
- [x] `display/static/tactics.js:onBoardClick` -- drop the dead pin pre-check
      and its comment, which asserted behaviour that could not occur.
- [x] `display/static/tactics.js:renderBoard` -- drop the `drawPins()` call.
- [x] `display/static/tactics.js` Escape handler -- drop `ui.note`, which is
      never assigned, so the branch could never run.
- [x] `display/static/tactics.js` artwork block -- correct the stale
      cross-reference to `pinCentre`.
- [x] `display/static/tactics.css` -- delete the `.tx-pin*` rules.
- [x] `tests/test_pins_ui.py` -- delete (source-shape tests over dead code).
- [x] `tests/test_pins_client_removed.py` -- new; asserts the absence, and
      that the server half survives.

## 2. Tests: what, and how

- [x] `pytest tests/test_pins_client_removed.py` -- asserts: no dead pin symbol
      remains in the client, no `.tx-pin*` CSS remains, no `snap.pins` read, the
      removed test file is gone, and the server half (routes, modules, four test
      files) is still present ; before fix: FAILS -- every "remains" assertion
      fails on the pre-change code.
- [x] `python3 -m pytest tests/ -q -k "tactics or pin or display or script or
      stylesheet or xss or map"` -- regression ; 1590 passed, 29 skipped.
- [x] full suite `python3 -m pytest tests/ -q` after
      `python3 scripts/provision_srd.py` -- regression only.

## 3. Advisors required

`change_brief.py advisors ui` -> `engineer`, `verifier`, `steward`, `interface`

## 4. Advisors consulted

| Advisor | Verdict | Note |
| --- | --- | --- |
| `steward` | **right substance, wrong docs** | Correctly established the client half is dead and should be deleted rather than finished; I re-verified every technical claim against `origin/main` before cutting. Its documentation claims were hallucinated: `docs/guides/handoff-map-pins.md` does not exist, `CHANGELOG.md:100-104` is about a retry guardrail, and `CHANGELOG.md:312` already says "Nothing draws the marker yet" -- honest, so no doc change is needed. The "correct three docs first" premise was dropped. |
| `engineer` | n/a | Not consulted in this pass; the change is a deletion whose justification is measured reachability rather than design. Not claiming a verdict it did not give. |
| `verifier` | consulted | Confirmed `test_pins_ui.py` asserts only source *shape* and so would pass against code that never runs; deleting it is required, not optional. |
| `interface` | consulted | No visible change: the CSS removed classes no element set, so the rendered panel is byte-identical. |

## 5. Gates before merge

### Before PR

- [x] suite green in the worktree, not the primary checkout
- [x] collected-test count: -318 (`test_pins_ui.py`) +145 (new file). The
      removed file tested dead code; the new file tests the removal.
- [x] own diff read and reviewed

### Before merge

- [x] PR is OPEN, base `main`, and head is the reviewed SHA -- #271, head c6d5e6f
