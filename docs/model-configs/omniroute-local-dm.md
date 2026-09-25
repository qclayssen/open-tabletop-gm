# Local DM through OmniRoute

`scripts/localdm/play.py` asks for models by name. With OmniRoute in front,
those names are combos, so which real model answers is set in OmniRoute and
can change without touching the game.

## Combos, tiered by what they cost

Created on the reference machine (M1 Pro, 16 GB) with `omniroute combo create`;
strategy `priority`, so the first healthy entry answers.

| Combo | Chain | Needs | Used for |
|---|---|---|---|
| `dm-local` | `ollama/qwen3.5:9b` -> `ollama/gemma4:e4b` -> `ollama/qwen3:14b` | about 6 GB RAM, free | every turn |
| `dm-fast` | `ollama/qwen3.5:4b` -> `ollama/qwen3.5:9b` | about 3 GB RAM, free | enemy picks, summaries |
| `dm-advisor` | GitHub Haiku 4.5 -> Kiro Haiku 4.5 -> Gemini 3.5 Flash -> Gemini 3.7 Flash -> DeepSeek 3.2 -> Claude Haiku 4.5 (paid) -> `ollama/qwen3.5:9b` | light cloud calls, paid only as a late fallback, local if offline | triggers, escalations |
| `dm-council` | GitHub Sonnet 5 -> Kimi K3 -> GPT-5.4 -> Claude Sonnet 5 (paid) | heavy, rare | `/advise council` |

`omniroute combo create` has no `fusion` strategy, so `dm-council` is a
priority chain; the game already asks 2 or 3 advisors in parallel itself.
Local models come through the Ollama provider node
(`openai-compatible-chat-5debd282...`, model names `ollama/<tag>`).

Model choice (llmfit, 2026-09-25, for 16 GB): Qwen3.5-9B and Gemma 4 E4B both
score quality 92 against 86 for Qwen3-14B, at a smaller size; Qwen3.5-4B is the
fastest usable model (about 24 tok/s). MoE models (Qwen3.5/3.6-35B-A3B,
Gemma 4 26B-A4B) only fit at 2 or 3 bits.

## Environment

```bash
export GM_LLM_URL=http://localhost:20128      # OmniRoute (the default)
export GM_LLM_KEY="$OMNIROUTE_API_KEY"         # or leave unset: OMNIROUTE_API_KEY is read
export GM_DM_MODEL=dm-local GM_ADVISOR_MODEL=dm-advisor GM_COUNCIL_MODEL=dm-council
export GM_FAST_MODEL=dm-fast                   # defaults to GM_DM_MODEL if unset
python3 scripts/localdm/play.py -c <campaign>
```

Recommended (hybrid): local tier straight to Ollama, advisors through OmniRoute.
OmniRoute's request queue times out any request after 15 s, too short for a
local model loading or writing a long reply:

```bash
GM_LOCAL_URL=http://localhost:11434 GM_DM_MODEL=qwen3.5:9b GM_FAST_MODEL=qwen3.5:4b \
GM_ADVISOR_MODEL=dm-advisor GM_COUNCIL_MODEL=dm-council \
  python3 scripts/localdm/play.py -c <campaign>
```

Without OmniRoute, point straight at Ollama (everything local, advisor included):

```bash
GM_LLM_URL=http://localhost:11434 GM_DM_MODEL=qwen3:14b GM_ADVISOR_MODEL=qwen3:14b \
  python3 scripts/localdm/play.py -c <campaign>
```

`GM_NO_THINK=0` stops the `/no_think` suffix (only Qwen3 understands it).
`GM_REASONING` sets `reasoning_effort` on local calls: `none` (default; Qwen3.5
ignores `/no_think` and otherwise returns empty turns), `low`/`medium`/`high` to
let it think, `off` to send nothing.
`GM_SHADOW=0` (or `--no-shadow`) turns off the background advisor review.

## Display

`play.py` mirrors each turn's narration to the Flask display (`/chunk`, the
same payloads as `display/send.py`) and registers the campaign at startup. The
terminal output does not change. The endpoint, first match wins:
`--display-url URL`, `GM_DISPLAY_URL`, `http://localhost:${GM_DISPLAY_PORT}`,
a port number in `display/.port` (nothing in this repo writes it; a local
launcher may), then `http://localhost:5001`.

Port 5001 may belong to another display app: leave it running and point the
local DM at its own display.

```bash
GM_DISPLAY_PORT=5051 python3 display/gm-display-app.py &     # this repo's display

GM_DISPLAY_URL=http://localhost:5051 \
GM_LOCAL_URL=http://localhost:11434 \
GM_DM_MODEL=qwen3.5:9b \
GM_FAST_MODEL=qwen3.5:4b \
GM_ADVISOR_MODEL=dm-advisor \
GM_COUNCIL_MODEL=dm-council \
GM_LLM_URL=http://localhost:20128 \
python3 scripts/localdm/play.py -c strixhaven-kairos
```

Only DM narration is sent, one event per turn with paragraphs kept. GM notes
(even with `--show-gm-notes`), engine lines, roll prompts, `/usage` and model
errors stay in the terminal. A display that is down or refuses the request
prints one warning and play goes on; the campaign is registered on the first
send that gets through. `--no-display` sends nothing.

## Ollama settings for 16 GB

```bash
launchctl setenv OLLAMA_CONTEXT_LENGTH 8192     # default 4096 gets tight as the summary grows
launchctl setenv OLLAMA_MAX_LOADED_MODELS 2     # DM + fast model; use 1 under memory pressure
```

Restart Ollama afterwards. Heavy swap (check `sysctl vm.swapusage`) triples
local latency.

## Reading the cost

`/usage` in the REPL, or `<campaign>/localdm/usage.jsonl`: one line per call
with the role (`dm`, `enemy-pick`, `summary`, `advisor:<name>`), the combo
name and its tokens. Only the `advisor:*` rows can cost money.
