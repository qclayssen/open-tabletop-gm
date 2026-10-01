"""Readers for the display's front-end sources (not a test module itself).

The display is three files now, and they used to be one. `display/templates/index.html`
kept the markup and the four server-rendered values; the stylesheet moved to
`display/static/display.css` and the script to `display/static/display.js` (W2 of the
2026-09-30 webdev audit). A test that pinned a CSS selector or a JS identifier used
to grep the template and now has to know which of the three it landed in, so the
readers are here rather than spelled out per file.

`all` is the old behaviour: the three sources concatenated. It is the right reader
for a test whose subject moved but whose question did not, because the assertion
stays byte-for-byte what it was.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

TEMPLATE = ROOT / "display" / "templates" / "index.html"
CSS = ROOT / "display" / "static" / "display.css"
JS = ROOT / "display" / "static" / "display.js"


def read_display_sources() -> DisplaySources:
    return DisplaySources()


class DisplaySources:
    """The markup, the stylesheet and the script, read once and held as text."""

    def __init__(self) -> None:
        self.template = TEMPLATE.read_text(encoding="utf-8")
        self.css = CSS.read_text(encoding="utf-8")
        self.js = JS.read_text(encoding="utf-8")

    @property
    def all(self) -> str:
        """All three, in load order. For a test that does not care which is which."""
        return self.template + "\n" + self.css + "\n" + self.js
