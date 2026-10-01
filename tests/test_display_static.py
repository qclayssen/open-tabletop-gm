"""The display's front end is three files, and the page has to keep working.

W2 of the 2026-09-30 webdev audit moved the template's inline <style> and <script>
to display/static/display.{css,js}. The point of the split is that a browser can
cache and re-parse the two assets independently of the Jinja-rendered markup, and
that the next wave of work has files to open. That benefit is only real if the
static route actually serves them and the markup really is small, so both are
asserted here rather than assumed.

The failure these guard against is quiet: if /static/display.js 404s, the page
still renders, the CSS still applies, and the display just sits there with no
narration and no connection, looking like a silent server.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

from tests.display_sources import CSS, JS, TEMPLATE, read_display_sources

REPO = pathlib.Path(__file__).resolve().parent.parent

#: A template that grew back past this has re-absorbed the stylesheet or the
#: script. It is a smell, not a failure: the point is to notice, not to block.
TEMPLATE_LINE_BUDGET = 400


def _load_app(name):
    spec = importlib.util.spec_from_file_location(
        name, str(REPO / "display" / "gm-display-app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StaticFilesAreServed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_app("gm_display_static_assets")
        cls.client = cls.mod.app.test_client()

    def test_the_stylesheet_is_served(self):
        r = self.client.get("/static/display.css")
        self.assertEqual(r.status_code, 200, "display.css 404s, so the page renders unstyled")
        self.assertIn("text/css", r.headers["Content-Type"])

    def test_the_script_is_served(self):
        r = self.client.get("/static/display.js")
        self.assertEqual(r.status_code, 200, "display.js 404s, so the display never runs")
        self.assertIn("javascript", r.headers["Content-Type"])

    def test_both_are_served_as_utf8(self):
        """Both files carry box-drawing and typographic characters. A response
        without a charset is decoded by the browser as the document's, which
        happens to be UTF-8 here, but only by luck of the <meta> tag."""
        for path in ("/static/display.css", "/static/display.js"):
            r = self.client.get(path)
            self.assertIn("utf-8", r.headers["Content-Type"].lower(), path)

    def test_the_served_bytes_match_the_files_on_disk(self):
        for path, disk in (("/static/display.css", CSS), ("/static/display.js", JS)):
            self.assertEqual(self.client.get(path).data, disk.read_bytes(), path)

    def test_the_index_never_reloads_itself_under_another_name(self):
        """A copy of the script pasted back into the template is the regression
        this whole item is about: two of everything, drifting apart silently."""
        html = self.client.get("/").data.decode("utf-8")
        self.assertNotIn("SCENE_DEFAULTS", html,
                         "the scene code is inline in the template again")


class TheTemplateStaysSmall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read_display_sources()
        cls.html = cls.src.template

    def test_it_is_under_the_line_budget(self):
        lines = self.html.splitlines()
        self.assertLess(
            len(lines), TEMPLATE_LINE_BUDGET,
            f"index.html is {len(lines)} lines; the stylesheet and script are "
            "inline in it again",
        )

    def test_there_is_no_large_inline_style_block(self):
        self.assertNotIn("<style>", self.html)

    def test_there_is_no_large_inline_script_block(self):
        """Two inline <script> blocks are expected and deliberate: the manifest
        (which has to be server-rendered) and the theme anti-FOUC snippet (which
        has to run before the stylesheet). What must not come back is a big one.
        """
        import re
        sizes = [len(b.splitlines()) for b in re.findall(r"<script>(.*?)</script>",
                                                        self.html, re.DOTALL)]
        self.assertTrue(sizes, "no inline script at all: where does the manifest go?")
        for size in sizes:
            self.assertLess(
                size, 30,
                f"an inline <script> is {size} lines; the display's script is "
                "being pasted back into the template",
            )

    def test_the_manifest_is_still_injected_inline(self):
        import re
        bodies = re.findall(r"<script>(.*?)</script>", self.html, re.DOTALL)
        self.assertTrue(any("GM_UI_MANIFEST" in b for b in bodies),
                        "no inline script sets the manifest global")

    def test_it_links_both_static_files(self):
        self.assertIn('<link rel="stylesheet" href="/static/display.css">', self.html)
        self.assertIn('<script src="/static/display.js"></script>', self.html)

    def test_the_stylesheet_is_linked_from_the_head(self):
        """A <link> after the body content is a flash of unstyled content on
        every load, which is the one thing the split must not cost."""
        head = self.html.split("</head>", 1)[0]
        self.assertIn("/static/display.css", head)

    def test_the_script_comes_after_the_markup_it_binds_to(self):
        """display.js queries the DOM at parse time (getElementById at the top
        level), so it cannot be deferred ahead of the elements it binds. It has
        to stay where the inline block was: last in the body, before tactics.js.
        """
        script_at = self.html.index('<script src="/static/display.js"></script>')
        tactics_at = self.html.index('<script src="/static/tactics.js" defer></script>')
        self.assertLess(script_at, tactics_at,
                        "display.js must still run before the deferred tactics.js")
        self.assertLess(self.html.index('id="text-content"'), script_at,
                        "display.js is loaded before the elements it binds to")
        self.assertNotIn("defer", self.html[:script_at].rsplit("\n", 2)[-1],
                         "display.js must not be deferred: it binds the DOM at parse time")

    def test_no_jinja_leaked_into_the_static_files(self):
        """The two files are served as-is, so a {{ }} in either would ship
        literally to the browser instead of being rendered."""
        for name, text in (("display.css", self.src.css), ("display.js", self.src.js)):
            for mark in ("{{", "{%", "{#"):
                self.assertNotIn(mark, text, f"{name} contains Jinja: {mark}")

    def test_the_four_server_values_stay_in_the_template(self):
        """The LAN token, the voice, the TTS flag and the UI manifest are the
        only things that have to be rendered per request. They stay where they
        were, so nothing had to be re-plumbed to move the code out.
        """
        for needle in ('<meta name="dnd-token" content="{{ lan_token }}">',
                       '<meta name="narrator-voice" content="{{ narrator_voice }}">',
                       "window.GM_UI_MANIFEST = {{ ui_manifest|safe }}"):
            self.assertIn(needle, self.html)


class TheMoveWasVerbatim(unittest.TestCase):
    """The two files are a copy, not a rewrite. Anything reformatted, reordered
    or 'improved' in the move is a behaviour change wearing a cleanup's clothes,
    so the seams are pinned instead of trusted.
    """

    def test_the_manifest_is_still_escaped_the_way_it_was(self):
        """|safe on a JSON blob is only safe because the server neutralises "<"
        before it gets here (gm-display-app._load_ui_manifest). Unchanged here:
        the same filter, the same place.
        """
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("{{ ui_manifest|safe }}", html)
        app = (REPO / "display" / "gm-display-app.py").read_text(encoding="utf-8")
        self.assertIn('.replace("<", "\\\\u003c")', app,
                      "the server no longer escapes < in the manifest, so |safe "
                      "in the template would be a script-injection hole")

    def test_the_script_still_binds_its_metadata_the_same_way(self):
        """display.js reads the token, the voice and the TTS flag off the meta
        tags the template already rendered, not off anything new.
        """
        js = JS.read_text(encoding="utf-8")
        for meta in ('meta[name="dnd-token"]', 'meta[name="tts-available"]',
                     'meta[name="narrator-voice"]'):
            self.assertIn(meta, js)

    def test_the_script_keeps_the_manifest_global_the_template_sets(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("window.GM_UI_MANIFEST", js)

    def test_the_script_is_still_a_classic_script_in_strict_mode(self):
        """'use strict' at the top of a classic <script> is a directive prologue
        that makes the whole file strict. As an external file it is still the
        first statement, so it still is.
        """
        self.assertTrue(JS.read_text(encoding="utf-8").lstrip().startswith("'use strict'"))


if __name__ == "__main__":
    unittest.main()
