# Milestone 6: local DM with a smarter advisor

## Goal

Run the table on a small local model, so a session costs almost no cloud tokens,
and call a smarter cloud model only when the story needs it.

Two things make that hard today:

- **Harness overhead.** Running the GM inside Claude Code or OpenCode sends the
  harness system prompt and tool schemas on every turn, whichever model answers.
- **Tool-call depth.** `docs/LLM-GUIDE.md` shows models of 24B and below lose
  their instructions after 4 or 5 chained tool calls.

The fix is a small Python loop that owns every tool call, so the model never
chains tools. Each player turn is one short prompt to the local model; the
engine (`scripts/tactics/`) still owns every rule.

## Plan

### Pieces (`scripts/localdm/`, stdlib only)

| File | Job |
|---|---|
| `llm.py` | One chat call to an OpenAI-compatible endpoint (OmniRoute by default). Model names come from the environment. Appends token usage to `<campaign>/localdm/usage.jsonl`. |
| `reply.py` | Splits a DM reply into narration and a trailing JSON block `{"escalate": str or null, "command": str or null}`. Strips `<think>` blocks. A reply with no JSON is all narration. |
| `memory.py` | The session transcript (`<campaign>/localdm/transcript.jsonl`), the rolling summary (`summary.md`) and small persistent flags (`meta.json`). Thread safe. |
| `context.py` | Builds the messages for one call: a fixed system message (DM prompt plus a digest of `state.md`), then one user message with the summary, recent turns, engine output, advisor notes and the player's line. Trims the oldest recent turns to stay under a character budget. |
| `bridge.py` | Runs tactics commands in-process (`tactics.cli.main`, stdout captured) and reads a small snapshot of `combat/encounter.json`. |
| `triggers.py` | Deterministic escalation triggers read from the snapshot: a fight starts, a boss-sized enemy appears, a PC drops to 0 HP, a PC dies. Each fires once. |
| `advisor.py` | Loads the advisor briefs, picks 2 or 3 for a question by keyword, and asks them in parallel (threads). Each answer is at most about 150 words. |
| `summarizer.py` | In a background thread, folds older transcript turns into `summary.md` using the local model. |
| `play.py` | The terminal REPL: `python3 scripts/localdm/play.py -c <campaign>`. |
| `prompts/` | `dm.md` (persona and output contract), `advisors/*.md` (copied from the dnd-gm advisor council). |

### Model tiers (OmniRoute combos)

| Env var | Default combo | Suggested target | Used for |
|---|---|---|---|
| `GM_DM_MODEL` | `dm-local` | Ollama `qwen3:14b`, `/no_think` | every turn, enemy menu picks, summaries |
| `GM_ADVISOR_MODEL` | `dm-advisor` | free stack first, paid Claude last | escalations and triggers |
| `GM_COUNCIL_MODEL` | `dm-council` | fusion combo | `/advise council` |
| `GM_FAST_MODEL` | (`GM_DM_MODEL`) | a smaller local model | enemy picks, summaries |

`GM_LLM_URL` defaults to `http://localhost:20128` (OmniRoute). `GM_LLM_KEY`
falls back to `OMNIROUTE_API_KEY`. Any OpenAI-compatible server works (Ollama at
`http://localhost:11434` with a model name in place of a combo).

Hardware note: the reference machine has 16 GB of unified memory.
`qwen3:14b` (9.3 GB, Q4_K_M) fits with room for a 16k context; `qwen3:30b-a3b`
(18 GB) does not.

### One player turn

1. Record the player's line.
2. Read the combat snapshot. If a trigger fires (and `council: off` is not set
   in `state.md`), ask the matching advisors in parallel and keep their notes.
3. One call to `GM_DM_MODEL`.
4. If the reply sets `escalate`, ask the advisors that fit the question, then
   call the DM once more with the notes. At most one escalation per turn.
5. If the reply sets `command` (one tactics command for the player's own
   action, from an allowlist), run it. Exit 0: one more DM call narrates the
   engine result. Exit 2: show the roll request and wait for the player's
   number. Anything else: show the engine's message.
6. Print the narration, record it, and start a summary fold if enough turns
   have piled up.
7. If it is now a GM-controlled creature's turn, run enemy turns: `options`,
   a tiny DM call that answers only with an option number (fallback 1),
   `choose`, `end-turn`, repeated until a player's turn or the end of combat;
   then one DM call narrates all of them.

### Commands in the REPL

- Free text: the player's action or words.
- A bare number while a roll is pending: the player's natural die result.
- `/c <tactics command>`: run a tactics command directly (for example
  `/c start frog-pond --pc Kairos@B7 --monster "giant frog@J5"`).
- `/advise <advisor|council> <question>`: consult explicitly.
- `/usage`: token totals for this session by role and model.
- `/quit`: stop (the summary is saved).

### Rules carried over

- The human at the keyboard is the player. Advisor notes are GM-only: they go
  into the DM prompt and are never printed unless `--show-gm-notes` is passed.
- Advisors never talk to players, never edit files, never decide for a PC,
  never fudge dice (`prompts/advisors/_shared.md`).
- The engine owns every rule. The DM never computes distance, damage or HP.
- Python 3.10, stdlib only, `encoding="utf-8"` on every file open.

## Shipped

- `scripts/localdm/` (stdlib only): `llm`, `reply`, `memory`, `context`, `bridge`,
  `triggers`, `advisor`, `summarizer`, `play`; prompts in `scripts/localdm/prompts/`.
- `GM_FAST_MODEL` (added after the first live run): enemy picks and summaries
  can go to a smaller, faster local model; defaults to `GM_DM_MODEL`.
- Shadow advisor (after the benchmark): after a player turn nobody advised, one
  advisor reviews it in a background thread; its notes feed the next DM call.
  "nothing" answers are dropped. `GM_SHADOW=0` or `--no-shadow` turns it off.
  Live: 3 Haiku reviews at 2 to 4 s each, never on the player's critical path.
- `GM_REASONING` (reasoning_effort on local calls) and `GM_LOCAL_URL` (local
  tier straight to Ollama); see Findings.
- 69 tests in `tests/test_localdm_*.py` (fake model; the bridge and one end to
  end test use the real engine). Plan: `docs/superpowers/plans/2026-09-25-local-dm.md`.
- Setup: `docs/model-configs/omniroute-local-dm.md`.

## Findings

- First live run (Ollama `qwen3:14b` as DM and advisor, M1 Pro 16 GB): about
  520 prompt tokens per DM call, but about 40 s per call. Speed, not tokens, is
  the limit on this machine.
- `qwen3:14b` escalated on every turn when the prompt said "ask instead of
  guessing". Fixed by wording ("null on almost every turn", scenery is yours to
  invent) plus a 3-turn cooldown in code. Rerun: 3 turns, 3 calls, 1470 prompt
  tokens total, 95 s (from 480 s). It now swings the other way: asked "is this
  sigil tied to the old frog cult?", it invented the link instead of escalating.
  Tuning the escalate wording (or a lore keyword trigger) is open.
- A thinking model as advisor spent its 300 tokens thinking and returned an
  empty answer. Advisor briefs now end with `/no_think` (unless `GM_NO_THINK=0`)
  and get 400 tokens.
- DM benchmark, 2026-09-25, same 3-turn scene (look for Tobin, inspect a stone,
  ask a lore question), M1 Pro 16 GB, `GM_REASONING=none`:

  | DM | Time | Advisor asked | Notes |
  |---|---|---|---|
  | `qwen3.5:9b` (Ollama) | 23 s | never | best local prose, ends turns on a choice; invents lore |
  | `gemma4:e4b` (Ollama) | 31 s | never | grounded, does not invent lore, no choices offered |
  | `qwen3:14b` (Ollama) | 110 s | once (lore) | a rusted bicycle in a fantasy pond |
  | `qwen3.5:4b` (Ollama) | 48 s | once (lore) | decided the PC's action; fine for picks and summaries only |
  | Haiku 4.5 (OmniRoute `fast`) | 23 s | once (lore), the intended behaviour | best continuity; replies run long |

  Local models almost never escalate on their own, hence the shadow advisor.
- Qwen3.5 ignores `/no_think` and Ollama's `think: false`: it spent all 500
  tokens reasoning and returned empty narration. `reasoning_effort: "none"` works
  (now sent on local-tier calls, `GM_REASONING`).
- OmniRoute's request queue times out any request at 15 s
  (`requestQueue.maxWaitMs`), which kills local calls; `GM_LOCAL_URL` sends the
  local tier straight to Ollama.
- Argparse usage text contains `--roll`, so "needs a roll" must key on the
  engine's own "Re-run the same command with" wording.
- A boss at 2x the top PC max HP made a giant frog (18) a boss against a level
  1 wizard (8); 3x fixes that.

## Combat autopilot (no model in a fight)

Live runs, 5 maps (frog-pond, detention-bog, firejolt-rooftops, mage-tower,
blank), 4 local DMs (Ollama, M1 Pro 16 GB), 5 or 6 player turns each, the DM
model reading every action:

| DM model | Commands written | Accepted by the engine | Avg s per DM call | Fights finished |
|---|---|---|---|---|
| qwen3.5:4b | 0 of 26 turns (writes `{"token","target"}` objects) | 0 | 7.7 | 0 of 5 |
| gemma4:e4b | 14 of 24 | 5 | 6.9 | 0 of 5 |
| qwen3.5:9b | 14 of 27 | 2 | 20.8 | 0 of 5 |
| qwen3:14b | 20 of 28 | 9 | 18.9 | 0 of 5 |

Small models also invented damage ("you hit for 7") and mixed up names, and a
local advisor (qwen3.5:9b, no `reasoning_effort` sent) answered with nothing.

So grid combat now runs on code (`--combat engine`, the default; `GM_COMBAT`):

- `scripts/localdm/autopilot.py`: the player's line to engine commands by
  keywords (attack, cast, throw, move to C5, back away, dash, dodge, end turn),
  target names ("frog 2", "the second frog", "it", "nearest", "wounded") and the
  sheet's attacks and spells. Equal targets get a one-line question. Talk goes to
  the model as before. Results are templated from the engine text, so every
  number is the engine's.
- `scripts/tactics/policy.py` and `choose <token> auto`: enemy picks with no
  model. Profiles from the stat block (beast, pack, mindless, skirmisher,
  artillery, brute; `extra.traits` and `extra.alignment` are now kept) re-score
  `ai.options()`; `ai_difficulty: easy | normal | deadly` in state.md sets a
  seeded softmax. A downed PC is attacked only by the hungry dead, or on deadly
  by a smart evil foe. Design from two expert reviews (game-AI: utility scoring,
  archetypes, softmax difficulty; 5e: Ammann-style creature behaviour).
- The model speaks only at a kill, a PC down, a crit or the end (`--flavor big`),
  or never (`--flavor off`). A won fight runs `end` by itself.
- Fixed on the way: a Silvery Barbs question during an enemy turn was read as a
  roll request (the re-run hint lists `--roll`), so typed numbers piled up
  forever; every `--react` answer is now replayed in order.

Rerun with the autopilot, same maps, qwen3.5:4b only for big moments: 3 of 5
fights won and ended, 1 lost (wolf vs a level 1 wizard), 2 to 3 DM calls per
fight instead of about 11, 0 s per ordinary turn.

Open: on firejolt-rooftops the cafe walls block every shot and neither side
closes in (the menu offers no approach when no attack reaches; the autopilot
does not step to a square with line of sight). Pack Tactics is not applied by
`rules.advantage()`. Local advisors need `reasoning_effort: none`.

## Open

- Raise OmniRoute `requestQueue.maxWaitMs` (15 s) if `dm-local` should work
  through the router; the hybrid setup does not need it.
- Under memory pressure (9 GB of swap in use) `qwen3.5:9b` went from about 8 s
  to 23 to 35 s per call. Close heavy apps, or set `OLLAMA_MAX_LOADED_MODELS=1`.
- Ollama serves a 4096-token context by default; set `OLLAMA_CONTEXT_LENGTH=8192`
  before the summary and recent turns grow past it.

- Live combat through the REPL needs the SRD data (`systems/dnd5e/build_srd.py`).
- No display output yet (`display/send.py`); the REPL prints to the terminal.
- Qwen3 advice sometimes names non-5e checks ("Decipher Script"); a cloud
  `dm-advisor` should do better. Worth a `probe/` run per advisor model.

## Decisions

- **Own loop instead of a harness** (implementer, from the token and
  tool-depth findings above). Harness mode keeps working unchanged.
- **Triggers default on** (implementer): the point of a small DM is to lean on
  the advisor at big moments. `council: off` in `state.md` turns them off.
- **Boss heuristic** (implementer): an enemy whose max HP is at least three times the
  highest PC max HP. Deterministic and cheap; revisit with CR data later.
- **Council answers are concatenated, not merged by another call**: saves a
  call and keeps each advisor's voice visible to the DM.
- **Enemy picks are a separate tiny call** (a number only), not a JSON field in
  the narration call: small models answer that far more reliably.

## Questions for the user

- Which models should back `dm-advisor` and `dm-council` in OmniRoute?
- Should `/advise` output be visible when you run the table as GM for other
  players (a `--gm` mode), or stay hidden always?
