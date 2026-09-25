"""localdm: run the table on a small local model, with a smarter advisor on call.

The engine owns the rules (scripts/tactics). This package owns every tool call,
so the model answers one short prompt per turn and never chains tools.

Modules:
    llm         one chat call to an OpenAI-compatible endpoint, usage log
    reply       narration plus a trailing JSON block
    memory      transcript, rolling summary, flags (<campaign>/localdm/)
    context     the messages for one call, under a size budget
    bridge      tactics commands in-process, combat snapshot
    triggers    deterministic "ask the advisor now" moments
    advisor     advisor briefs, keyword routing, parallel consults
    summarizer  background folding of old turns into the summary
    play        the terminal REPL
"""

import pathlib
import sys

# scripts/ holds paths.py and the tactics package.
_SCRIPTS = str(pathlib.Path(__file__).resolve().parent.parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
