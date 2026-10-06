# Change brief: gm-graph-context

**Kind:** rules
**Code repo branch:** `rules/gm-graph-context`
**Status:** draft

Fill every section. `scripts/change_brief.py check /Users/quentinclayssen/github/dnd-gm/.claude/briefs/gm-graph-context.md` is the gate and it is
not optional: it is what stops a skipped step from being reported as a done one.

## 1. Implement

One line per thing to build or change. Name the file and the function, not the
feeling. If it turns out to already exist, that is a finding, not a waste.

- [x] `scripts/gm_graph.py:render_subgraph` -- split out of `_emit_subgraph`,
      returning markdown text instead of printing. The CLI wrappers now
      `print(render_subgraph(...))`; their bytes are unchanged (diffed).
- [x] `scripts/gm_graph.py:_overlap_terms` -- content words from the scene
      text, tokens <=3 dropped. Documented limit for short node names.
- [x] `scripts/gm_graph.py:scene_nodes` -- NEW. Hybrid rank: adjacency
      (decaying by hop, via `_expand` in rings) + lexical overlap, so a node
      named in the scene outranks one merely nearby. Returns
      `{present, nodes, names}`; `present: False` for absent/empty/corrupt
      graphs and never raises.
- [x] `scripts/localdm/play.py:Session._scene_notes` -- NEW. Calls
      `scene_nodes`, renders the selection, scrubs it. Returns "" on every
      failure path (ImportError, any exception, no graph, empty selection).
- [x] `scripts/localdm/play.py:Session._scene_present` -- NEW. On-scene names
      from the bridge snapshot, read from the source the digest already parses.
- [x] `scripts/localdm/play.py:Session._digest` -- lore section becomes
      `self._scene_notes() or context.notes_digest(self.camp_dir)`.
- [x] `scripts/gm_graph.py:cmd_subgraph`/`cmd_scene_context` -- use the
      returning renderer.

## 2. Tests: what, and how

Each entry needs a command that can be run by someone else, and a line saying
what the test does on the pre-fix code. A test that passes on the broken version
is decoration (`agents/verifier.md`).

- [x] `pytest tests/test_play_graph_wiring.py::test_without_a_graph_the_digest_is_exactly_what_it_was`
      -- asserts: byte-identical digest with no `graph.json` ; before fix: passes
      (that is the contract -- it must pass before AND after)
- [x] `pytest tests/test_play_graph_wiring.py::test_the_wiring_is_what_makes_the_graph_reach_the_digest`
      -- asserts: the graph's own text reaches the digest ; before fix: **FAILS**
      -- this is the mutation test. It did not fail at first; see section 4.
- [x] `pytest tests/test_play_graph_wiring.py::test_a_scene_where_the_far_node_is_the_live_threat_does_not_shrink`
      -- asserts: a named-but-unconnected threat survives ; before fix: FAILS,
      the threat is culled and `dm.md`'s "signalled before it lands" is broken
- [x] `pytest tests/test_play_graph_wiring.py::test_the_scene_notes_are_scrubbed_like_every_other_digest_section`
      -- asserts: graph lore is scrubbed, and `notes` is non-empty first ;
      before fix: **PASSED against unscrubbed code** because the payload ranked
      out and `notes` was `""`. Fixed; the `assert notes` is now the point.
- [x] `pytest tests/test_gm_graph_scene_relevance.py::test_a_far_unmentioned_node_may_still_be_dropped`
      -- asserts: the cull can drop something ; before fix: FAILS, the selection
      keeps everything and the survival test proves nothing
- [x] `pytest tests/test_gm_graph_scene_relevance.py::test_render_subgraph_matches_what_the_cli_printed`
      -- asserts: the CLI still prints what `render_subgraph` returns ; before
      fix: FAILS (the function did not exist)
- [x] MUTATIONS, each verified to fail: remove wiring -> 1 failure; remove the
      lexical half -> 5; remove the adjacency half -> 1
- [x] token saving measured with tiktoken: 664 -> 36 tokens (94%) on a 12-NPC
      campaign, against a digest already at its 2500-char cap
- [x] full suite `python3 -m pytest tests/ -q` -- 4069 passed, 52 skipped, 0 failed

## 3. Advisors required

`change_brief.py advisors rules` -> `engineer`, `verifier`, `steward`, `designer`, `arbiter`, `architect`

These are required, not suggestions. They are also NOT in `/advise`: they are
unregistered briefs read from `agents/`, consulted directly during the cycle.

| `engineer` | consulted | does this already exist; which function actually changes |
| `verifier` | consulted | does each test do anything on the pre-fix code |
| `steward` | consulted | repo/process hazards; two repos, a worktree and a gitlink |
| `designer` | consulted | SRD rules correctness |
| `arbiter` | consulted | dice and rules adjudication, and the prompt guard |
| `architect` | consulted | is the abstraction earned; state authority boundary |

## 4. Advisors consulted

Fill as you go, with the actual verdict. An advisor that was asked and had
nothing to add is a real result, say so rather than leaving the box open.

| Advisor | Verdict | Note |
| --- | --- | --- |
| `engineer` | consulted | `scene-context` existed and was already wired into `/gm load`; `localdm/` had zero references. The gap was wiring, exactly as #276 says, not design. The one real decision was splitting `render_subgraph` out of the printing `_emit_subgraph` rather than capturing stdout in the hot turn path. |
| `verifier` | **found a false pass twice** | 1. Removing the wiring entirely did not fail the test file -- `notes_digest` is itself trimmed, so the shrink assertions held against code with no graph involvement. Now pinned on the graph's own text. 2. The scrub test passed against UNSCRUBBED code, because the payload node ranked out and `notes` was `""`. Now guarded by `assert notes` first. |
| `designer` | consulted | The scene block is labelled "off-screen pressure included" on purpose: the DM is told the cull is relevance-based, so a missing NPC reads as "not relevant yet" rather than "the system lost it". `dm.md` requires danger be foreshadowed; a cull that silently dropped the threat would read to the model as the threat not existing. |
| `arbiter` | not consulted | No rules question here -- it culls lore from the prompt and decides nothing about dice, damage or the action economy. Saying so rather than claiming a verdict it did not give. |
| `architect` | not consulted | Not reached in this pass. |
| `steward` | consulted | `play.py` and `gm_graph.py` claimed before any edit; preflight clear. Left `display.js` alone -- it is under a live claim from `codex-worker-252-20261005-a`, whose liveness was ambiguous, and I have destroyed a live run's work once already. |

## 5. Gates before merge

### Before PR

- [x] full suite green in the worktree: 4069 passed / 52 skipped / 0 failed
- [x] collected-test count, measured with `--collect-only` in a detached
      worktree of each: clean `origin/main` (6e04257) = 4102, this branch
      (4acfef7) = 4121. **+19 exactly**, and 0 removed. Measured because #276
      removes nothing, and the gate is there to catch the case where it does.
- [x] own diff read and reviewed

### Before merge

- [x] PR is OPEN, base `main`, and head is the reviewed SHA -- #276, head 4acfef7

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

<!-- files this kind usually lands in: systems/, scripts/localdm/ -->
