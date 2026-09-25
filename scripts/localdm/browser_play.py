#!/usr/bin/env python3
"""browser_play.py: play the local DM entirely from the browser display.

    python3 scripts/localdm/browser_play.py -c <campaign> [play.py options]

Runs play.py and feeds it what the players stage and ready in the display's Party
input panel (display/check_input.py drains it). Narration, the sidebar and dice
requests already go the other way, so nothing is typed in the terminal. Display
settings sent as [[...]] lines (narration length) are joined to the next action.
Everything play.py prints goes to this terminal, so the GM notes stay out of the browser.
Ctrl-C stops both.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
POLL = 1.0


def drain() -> list:
    out = subprocess.run([sys.executable, str(ROOT / "display" / "check_input.py")],
                         cwd=ROOT, capture_output=True, text=True).stdout
    lines, directives = [], ""
    for line in out.splitlines():
        if re.fullmatch(r"\[\[.*\]\]", line.strip()):
            directives += line.strip() + " "
            continue
        m = re.match(r"\[[^\]]+\]:\s*(.*)", line)
        text = (m.group(1) if m else line).strip()
        if text:
            lines.append(directives + text)
            directives = ""
    return lines


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    play = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "localdm" / "play.py"), *argv],
                            cwd=ROOT, stdin=subprocess.PIPE, text=True, bufsize=1)
    try:
        while play.poll() is None:
            for line in drain():
                print(f"> {line}", flush=True)
                play.stdin.write(line + "\n")
                play.stdin.flush()
            time.sleep(POLL)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        if play.poll() is None:
            play.terminate()
    return play.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
