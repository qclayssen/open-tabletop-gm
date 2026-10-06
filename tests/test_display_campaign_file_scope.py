"""The display and its wrapper must agree on which campaign's files they read.

Both `display/gm-display-app.py` and `display/wrapper.py` resolve two files out of
`display/`: the campaign marker (`.campaign`) and the roster cache (`stats.json`).
They were both hardcoded to the bare names, so:

  - every display on the machine shared one campaign and one roster, which is what
    let `start-display.sh --campaign B --port 5051` rewrite the main display's
    campaign while the main display kept pushing its own roster; and
  - `wrapper.py` did not honour `GM_STATS_FILE` at all, even though
    `gm-display-app.py` did, so whenever that override was set the sidebar showed
    one party's roster while the wrapper's input allowlist was built from a
    different file.

The second half is the sharper defect. `wrapper.py` `_known_chars()` reads
`players` names out of `STATS_FILE` and uses them as the allowlist that decides
which character names may post a turn. Two files that disagree is a sidebar and an
authorship gate that do not describe the same table.
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "display"))

_MOD_DIR = _ROOT / "display" / "wrapper.py"


def _load_wrapper():
    """Import wrapper.py fresh, so module-level path resolution runs again."""
    spec = importlib.util.spec_from_file_location("wrapper_under_test", _MOD_DIR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CampaignFilesArePerDisplay(unittest.TestCase):
    """Both resolvers must honour the same environment variables."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("GM_STATS_FILE", "GM_DISPLAY_CAMPAIGN_FILE")}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_wrapper_honours_gm_stats_file(self):
        # before fix: STATS_FILE was hardcoded to display/stats.json, so this
        # import ignored the override the display itself respects.
        with tempfile.TemporaryDirectory() as tmp:
            chosen = str(pathlib.Path(tmp) / "stats-5051.json")
            os.environ["GM_STATS_FILE"] = chosen
            self.assertEqual(_load_wrapper().STATS_FILE, chosen)

    def test_wrapper_honours_the_campaign_file_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            chosen = str(pathlib.Path(tmp) / ".campaign-5051")
            os.environ["GM_DISPLAY_CAMPAIGN_FILE"] = chosen
            self.assertEqual(_load_wrapper().CAMP_FILE, chosen)

    def test_wrapper_defaults_to_the_bare_names_when_unset(self):
        # The single-display case must be byte-for-byte unchanged, or every
        # existing install and every .gitignore entry stops matching.
        os.environ.pop("GM_STATS_FILE", None)
        os.environ.pop("GM_DISPLAY_CAMPAIGN_FILE", None)
        module = _load_wrapper()
        self.assertEqual(module.STATS_FILE, str(_ROOT / "display" / "stats.json"))
        self.assertEqual(module.CAMP_FILE, str(_ROOT / "display" / ".campaign"))

    def test_two_displays_on_two_ports_resolve_two_different_rosters(self):
        # The property the fix exists for: a display on 5051 and one on 5001 must
        # not read the same roster file, or one campaign's party is pushed into
        # the other's sidebar.
        with tempfile.TemporaryDirectory() as tmp:
            main = str(pathlib.Path(tmp) / "stats.json")
            second = str(pathlib.Path(tmp) / "stats-5051.json")
            os.environ["GM_STATS_FILE"] = main
            self.assertEqual(_load_wrapper().STATS_FILE, main)
            os.environ["GM_STATS_FILE"] = second
            self.assertEqual(_load_wrapper().STATS_FILE, second)
            self.assertNotEqual(main, second)

    def test_the_launcher_scopes_campaign_state_per_port(self):
        # start-display.sh is the thing that has to export these, and a shell
        # script cannot be imported -- so read it and assert on the shape it
        # exports, which is the part that can silently regress.
        script = (_ROOT / "display" / "start-display.sh").read_text(encoding="utf-8")
        # One combined export line, so assert the names are exported rather than
        # pinning the line's exact shape.
        export_lines = [ln for ln in script.splitlines()
                        if ln.startswith("export ") and "GM_STATS_FILE" in ln]
        self.assertTrue(export_lines, "GM_STATS_FILE is never exported")
        self.assertTrue(any("GM_DISPLAY_CAMPAIGN_FILE" in ln for ln in export_lines),
                        "GM_DISPLAY_CAMPAIGN_FILE is not exported alongside GM_STATS_FILE")
        # A second port must get a different file, and the default port must keep
        # the bare name so existing installs and .gitignore entries still match.
        self.assertIn('stats-$PORT.json', script)
        self.assertIn('.campaign-$PORT', script)
        self.assertIn('stats.json"', script)

    def test_the_display_honours_the_same_two_variables(self):
        source = (_ROOT / "display" / "gm-display-app.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("GM_STATS_FILE")', source)
        self.assertIn('os.environ.get("GM_DISPLAY_CAMPAIGN_FILE")', source)


if __name__ == "__main__":
    unittest.main()
