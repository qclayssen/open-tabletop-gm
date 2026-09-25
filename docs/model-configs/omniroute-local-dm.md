# Local DM through OmniRoute

`scripts/localdm/play.py` asks for models by name. With OmniRoute in front,
those names are combos, so which real model answers is set in OmniRoute and
can change without touching the game.

## Combos to create

| Combo | Put in it | Used for |
|---|---|---|
| `dm-local` | Ollama `qwen3:14b` (16k context) | every turn |
| `dm-fast` (optional) | a smaller local model, e.g. `qwen3:8b` | enemy picks, summaries |
| `dm-advisor` | free models first, paid Claude last | triggers and escalations |
| `dm-council` | a fusion combo (several models) | `/advise council` |

## Environment

```bash
export GM_LLM_URL=http://localhost:20128      # OmniRoute (the default)
export GM_LLM_KEY="$OMNIROUTE_API_KEY"         # or leave unset: OMNIROUTE_API_KEY is read
export GM_DM_MODEL=dm-local GM_ADVISOR_MODEL=dm-advisor GM_COUNCIL_MODEL=dm-council
export GM_FAST_MODEL=dm-fast                   # optional; defaults to GM_DM_MODEL
python3 scripts/localdm/play.py -c <campaign>
```

Without OmniRoute, point straight at Ollama (everything local, advisor included):

```bash
GM_LLM_URL=http://localhost:11434 GM_DM_MODEL=qwen3:14b GM_ADVISOR_MODEL=qwen3:14b \
  python3 scripts/localdm/play.py -c <campaign>
```

`GM_NO_THINK=0` stops the `/no_think` suffix (only Qwen3 understands it).

## Reading the cost

`/usage` in the REPL, or `<campaign>/localdm/usage.jsonl`: one line per call
with the role (`dm`, `enemy-pick`, `summary`, `advisor:<name>`), the combo
name and its tokens. Only the `advisor:*` rows can cost money.
